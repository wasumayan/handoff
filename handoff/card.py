"""A short, deterministic state card for the newest session: what a recipient needs first.

Built from the session before compaction, so nothing compaction drops is lost from it. Every line
is copied or counted from the log, never inferred: the task, what the user said since, which files
were edited, the last test run and its outcome, and errors that were never followed by a success.
"""

from __future__ import annotations

import re

from .model import Session, ToolCall

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "shell", "exec_command", "exec"}  # Claude Code; Codex
TEST_RE = re.compile(r"\b(pytest|py\.test|unittest|tox|nox|(npm|yarn|pnpm)( run)? test|go test|"
                     r"cargo test|make (test|check)|jest|vitest|mvn test|gradle test|rspec)\b")
MAX_SAID = 8  # most recent user messages after the first
# A shell reports its last command's status, so a trailing grep, diff, test or `ls x 2>/dev/null`
# answering "no" marks a whole call as an error though its work succeeded. Exit 1 or 2 with nothing
# in the output that reads as a failure is taken as that answer. In real sessions these were a third
# of all errored calls; real failures said so (Traceback, fatal:, No such file, not permitted).
FAILURE_TEXT = re.compile(
    r"Traceback|Exception|[Ee]rror|ERROR|\bFAIL\b|[Ff]ailed|[Ff]atal|No such file|not found|"
    r"[Pp]ermission denied|not permitted|[Cc]annot |[Cc]ould not|[Uu]nable to|Killed|"
    r"Segmentation fault|[Aa]bort|[Dd]enied|[Ii]nvalid|[Uu]nrecognized|[Ss]yntax|[Uu]sage:")
LEADING_EXIT = re.compile(r"^Exit code \d+\s*")


def failed(t: ToolCall) -> bool:
    """Whether the call failed: an error, unless it is a shell's expected non-zero exit. A test
    run's exit code is always its result."""
    if not t.is_error:
        return False
    if t.exit_code not in (1, 2) or TEST_RE.search(t.input_summary):
        return True
    return bool(FAILURE_TEXT.search(LEADING_EXIT.sub("", t.result_summary)))


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _edited(t: ToolCall) -> list[str]:
    if t.name in EDIT_TOOLS:
        return [t.input_summary.strip()]
    if t.input_summary.startswith("apply_patch "):  # Codex: "apply_patch a.py, b.py"
        return [f.strip() for f in t.input_summary[len("apply_patch "):].split(",") if f.strip()]
    return []


def build(s: Session | None, with_user: bool = True) -> str:
    """with_user=False keeps only the latest request from the user's messages, for when the
    transcript is installed too: compaction drops human turns last, so the rest are already there."""
    if s is None or not s.messages:
        return ""
    users = [m.text for m in s.messages if m.role == "user" and m.text.strip()]
    calls = [t for m in s.messages for t in m.tool_calls]
    out: list[str] = []
    if users and with_user:
        out.append("**Task (first request):** " + _clip(users[0], 600))
    if len(users) > 1 and with_user:
        said = users[1:][-MAX_SAID:]
        out.append("**What the user said since, oldest first:**\n"
                   + "\n".join(f"- {_clip(u, 240)}" for u in said))
    elif users:
        out.append("**Latest request:** " + _clip(users[-1], 600))
    files = list(dict.fromkeys(f for t in calls for f in _edited(t)))
    if files:
        out.append("**Files edited:** " + ", ".join(f"`{f}`" for f in files[:15])
                   + (f" (+{len(files) - 15} more)" if len(files) > 15 else ""))
    elif calls:  # said outright: a summary that implies otherwise is wrong
        out.append("**Files edited:** none in this session")
    tests = [t for t in calls if t.name in SHELL_TOOLS and TEST_RE.search(t.input_summary)]
    if tests:
        t = tests[-1]
        line = f"**Last test run:** `{_clip(t.input_summary, 160)}`: {'FAILED' if t.is_error else 'exit 0'}"
        if t.result_summary:
            line += f"\n  > {_clip(t.result_summary, 300)}"
        out.append(line)
    open_errors: dict[str, ToolCall] = {}
    for t in calls:  # an error stays open until the same call succeeds later
        if failed(t):
            open_errors[t.input_summary] = t
        else:
            open_errors.pop(t.input_summary, None)
    if open_errors:
        last = list(open_errors.values())[-3:]
        out.append("**Errors never followed by a success of the same call:**\n" + "\n".join(
            f"- `{_clip(t.input_summary, 120)}`: {_clip(t.result_summary, 200)}" for t in last))
    return "\n\n".join(out)
