"""Scrub obvious secrets before content leaves the machine or lands in a handoff file."""

from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("aws_access", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]
# KEY=value lines where KEY smells secret and value is long enough to be one.
ENV_LINE = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY)[A-Z0-9_]*\s*[=:]\s*)(['\"]?)([^\s'\"]{8,})(\2)"
)


def redact(text: str) -> tuple[str, int]:
    """Return (scrubbed, count). Replacements are stable tags so the reader knows a value was there."""
    count = 0
    for tag, pat in PATTERNS:
        text, n = pat.subn(f"[REDACTED:{tag}]", text)
        count += n
    text, n = ENV_LINE.subn(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:env]{m.group(4)}", text)
    count += n
    return text, count


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
