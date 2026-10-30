"""Scrub obvious secrets before content leaves the machine or lands in a handoff file."""

from __future__ import annotations

import re

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
]
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


def redact(text: str) -> tuple[str, int]:
    """Return (scrubbed, count). Replacements are stable tags so the reader knows a value was there."""
    count = 0
    for tag, pat in PATTERNS:
        text, n = pat.subn(f"[REDACTED:{tag}]", text)
        count += n
    hits = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal hits
        out, hit = _redact_env(m)
        hits += hit
        return out

    text = ENV_LINE.sub(sub, text)
    return text, count + hits


def redact_bundle(bundle) -> int:
    """In-place scrub of every text field in a Bundle. Returns number of replacements."""
    total = 0
    for d in bundle.identity + bundle.project:
        d.content, n = redact(d.content)
        total += n
    for s in bundle.sessions:
        for m in s.messages:
            m.text, n = redact(m.text)
            total += n
            for t in m.tool_calls:
                t.input_summary, n1 = redact(t.input_summary)
                t.result_summary, n2 = redact(t.result_summary)
                total += n1 + n2
        s.summaries = [redact(x)[0] for x in s.summaries]
    return total
