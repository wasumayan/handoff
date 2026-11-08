"""Scrub obvious secrets and personal data before content leaves the machine or lands in a
handoff file."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# Prefixes are unanchored where the prefix is distinctive: keys appear right after a JSON-escaped
# "\n" or a URL-encoded "%3D".
_SK_START = r"(?:(?<![A-Za-z])|(?<=\\[nrt])|(?<=%[0-9A-Fa-f]{2}))"

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openrouter", re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}")),
    # Known prefixes and legacy all-alphanumeric keys anywhere; any other sk-... only where "sk"
    # does not end a word, so "Task-specific-retry-..." in prose is not a key.
    ("openai", re.compile(r"sk-(?:proj|svcacct|admin|None)-[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9]{32,}|"
                          + _SK_START + r"sk-[A-Za-z0-9_\-]{20,}")),
    ("huggingface", re.compile(r"hf_[A-Za-z0-9]{30,}")),
    ("runpod", re.compile(r"rpa_[A-Za-z0-9]{30,}")),
    ("github", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("aws_access", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("stripe", re.compile(_SK_START + r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("stripe_webhook", re.compile(r"whsec_[A-Za-z0-9]{24,}")),
    ("google_oauth", re.compile(r"GOCSPX-[A-Za-z0-9_\-]{20,}|ya29\.[A-Za-z0-9_\-]{20,}")),
    ("gitlab", re.compile(r"glpat-[A-Za-z0-9_\-]{20,}")),
    ("npm", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("pypi", re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,}")),
    ("sendgrid", re.compile(r"SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+")),
    ("discord_webhook", re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w\-]+")),
    ("groq", re.compile(r"gsk_[A-Za-z0-9]{40,}")),
    ("xai", re.compile(r"xai-[A-Za-z0-9]{40,}")),
    ("replicate", re.compile(r"r8_[A-Za-z0-9]{30,}")),
    ("digitalocean", re.compile(r"do[por]_v1_[a-f0-9]{64}")),
]
# Secrets inside a larger string: keep the surrounding text so the reader can follow it.
CONTEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("basic_auth", re.compile(r"(?i)(\bauthorization:\s*basic\s+)[A-Za-z0-9+/=]{8,}()")),
    ("cookie", re.compile(r"(?i)(\b(?:set-)?cookie:\s*)[^\r\n'\"]+()")),
]
# scheme://user:PASS@host. Scanned in code rather than by a regex: the password runs to the last "@"
# in its whitespace-delimited token, with no length cap (a cap fails open on long passwords), and
# each token is visited once, so the scan is linear. A later "@" in a query string makes it
# over-redact, which is the safe direction.
URL_USER = re.compile(r"://[^\s/:@]*:")
WHITESPACE = re.compile(r"\s")


def _redact_url_passwords(text: str) -> tuple[str, int]:
    out, pos, n = [], 0, 0
    while (m := URL_USER.search(text, pos)) is not None:
        ws = WHITESPACE.search(text, m.end())
        end = ws.start() if ws else len(text)
        at = text.rfind("@", m.end(), end)
        if at > m.end():
            out.append(text[pos:m.end()] + "[REDACTED:url_password]")
            pos = at
            n += 1
        else:
            out.append(text[pos:end])
            pos = end
    out.append(text[pos:])
    return "".join(out), n


# Personal data, on unless pii=False (`handoff create --keep-pii`). Phone numbers: North American
# shapes with separators only, so ids, ports, dates and versions are left alone.
# Not after "/": Hydra overrides like `env/maze_small@env.train` are config, not addresses.
EMAIL = re.compile(r"(?<![/\w.%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
EMAIL_KEEP = re.compile(r"(?:git|noreply)@.*|.*@users\.noreply\.github\.com")
PHONE = re.compile(r"(?<![\w.+/\-])(?:\+1[\s.\-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?![\w.\-])")
# key=value / key: value where the key smells secret, anywhere in a line (`cd x && HF_TOKEN=...
# cmd`, `"api_key": "..."`, JSON-escaped `\"api_key\": \"...\"`). Errs toward redacting: a miss
# leaks a key, a false positive loses a few chars. The value runs to whitespace, a quote, or a
# JSON escape (\n \t \"), so an escaped `UNSET\n...` is too short to match; other backslashes are
# part of the value.
ENV_LINE = re.compile(
    r"(?i)((?<![A-Za-z0-9_])(?:export\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY)"
    r"[A-Z0-9_]*(?:\\?['\"])?\s*[=:]\s*)((?:\\?['\"])?)((?:[^\s'\"\\]|\\(?![nrt\"\\]))+)"
)
MIN_SECRET_CHARS = 8
SHAPE_CHECK_CHARS = 256  # NOT_SECRET is quadratic; long values are redacted unchecked
# Whole values that are clearly not secrets.
NOT_SECRET = re.compile(
    r"(?:~?/|\./)\S*"  # a path
    r"|\$\{?[A-Z_][A-Z0-9_]*\}?[,;)\]}]*"  # a bare $ENV_VAR reference (upper case: not $ecret)
    r"|[\w.]+\(.*\)[,;)\]}]*|[\w.]+\[.*\][,;)\]}]*"  # a call or subscript ending the value
    # regex syntax: a ranged character class with a quantifier (`[A-Za-z0-9._-]{24,})`), or a
    # parenthesized alternation of 3+ words (`|secret|key|token)/`)
    r"|.*\[[^\]]*\w-\w[^\]]*\](?:[*+?]|\{\d).*"
    r"|[(|]?\w+(?:\|\w+){2,}\)[/']*"
    r"|\[REDACTED:\w+\][,;)\]}]*"  # exactly a tag left by a pattern above
)
# max_tokens=20000000, "cached_tokens": 123456789, or a count clipped by the parser ("101524,…").
# Only counter-named keys (plural *tokens, *token_count); a singular *_TOKEN is always redacted.
TOKEN_COUNT = re.compile(r"\d[\d_.,]*[,;)\]}…]*")
COUNTER_KEY = re.compile(r"(?i)\w*tokens|\w*token_count")


def _redact_env(m: re.Match[str]) -> tuple[str, bool]:
    key, quote, value = m.group(1), m.group(2), m.group(3)
    if (
        len(value) < MIN_SECRET_CHARS
        or (len(value) <= SHAPE_CHECK_CHARS and NOT_SECRET.fullmatch(value))
        or (COUNTER_KEY.fullmatch(re.findall(r"\w+", key)[-1]) and TOKEN_COUNT.fullmatch(value))
    ):
        return m.group(0), False
    return f"{key}{quote}[REDACTED:env]", True


@lru_cache(maxsize=8)
def _home_pattern(home: str) -> re.Pattern[str] | None:
    """`home` as spelled and as resolved: `/home/me` may be a symlink to `/storage/home/me`, and the
    resolved form is what cwd, slugs and the agent's own records use. Longest first, so that form
    is replaced whole. No check on what precedes a match: `.../home/me` in truncated output and
    `-I/home/me/include` must still lose the name, at the cost of `/srv/backup/home/me` becoming
    `/srv/backup~`."""
    forms = {home}
    try:
        forms.add(str(Path(home).resolve()))
    except (OSError, RuntimeError):  # RuntimeError: symlink loop before Python 3.13
        pass
    ordered = sorted((f for f in forms if len(f) >= 2), key=len, reverse=True)
    if not ordered:
        return None
    return re.compile(r"(?:" + "|".join(map(re.escape, ordered)) + r")(?=/|\b)")


def home_to_tilde(text: str) -> str:
    """`/Users/<name>/...` -> `~/...`: keeps paths usable without naming the user."""
    pat = _home_pattern(str(Path.home()))
    return pat.sub("~", text) if pat else text


def redact(text: str, pii: bool = True) -> tuple[str, int]:
    """Return (scrubbed, count). Replacements are stable tags so the reader knows a value was there.
    With pii, also emails and phone numbers (counted) and the home path (normalized, not counted)."""
    count = 0
    for tag, pat in PATTERNS:
        text, n = pat.subn(f"[REDACTED:{tag}]", text)
        count += n
    text, n = _redact_url_passwords(text)
    count += n
    for tag, pat in CONTEXT_PATTERNS:
        text, n = pat.subn(rf"\1[REDACTED:{tag}]\2", text)
        count += n
    if pii:
        text, n = _sub_counting(EMAIL, _redact_email, text)
        count += n
        text, n = PHONE.subn("[REDACTED:phone]", text)
        count += n
        text = home_to_tilde(text)
    text, n = _sub_counting(ENV_LINE, _redact_env, text)
    return text, count + n


def _redact_email(m: re.Match[str]) -> tuple[str, bool]:
    if EMAIL_KEEP.fullmatch(m.group(0)):
        return m.group(0), False
    return "[REDACTED:email]", True


def _sub_counting(pat: re.Pattern[str], fn, text: str) -> tuple[str, int]:
    """re.sub with a callback returning (replacement, redacted?); counts only real redactions."""
    hits = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal hits
        out, hit = fn(m)
        hits += hit
        return out

    return pat.sub(sub, text), hits


def redact_bundle(bundle, pii: bool = True) -> int:
    """In-place scrub of every text field in a Bundle, paths included. Returns the number of
    values redacted."""
    total = 0

    def r(text: str) -> str:
        nonlocal total
        out, n = redact(text, pii)
        total += n
        return out

    bundle.cwd = r(bundle.cwd)
    bundle.notes = [r(x) for x in bundle.notes]
    for d in bundle.identity + bundle.project:
        d.path, d.content = r(d.path), r(d.content)
    for s in bundle.sessions:
        s.cwd, s.path = r(s.cwd), r(s.path)
        for m in s.messages:
            m.text = r(m.text)
            for t in m.tool_calls:
                t.input_summary, t.result_summary = r(t.input_summary), r(t.result_summary)
        s.summaries = [r(x) for x in s.summaries]
    if bundle.delta is not None:
        d = bundle.delta
        d.commits = [r(x) for x in d.commits]
        d.status, d.diff_stat = r(d.status), r(d.diff_stat)
    return total
