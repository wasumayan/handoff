"""Claude Code on-disk state.

Verified layout (Claude Code 2.1.x):

    ~/.claude/CLAUDE.md                                  user-level instructions
    ~/.claude/projects/<slug>/<session-uuid>.jsonl       one record per line
    ~/.claude/projects/<slug>/memory/*.md                per-project memory
    <repo>/CLAUDE.md, <repo>/.claude/CLAUDE.md, <repo>/CLAUDE.local.md, <repo>/.claude/rules/*.md

<slug> is the absolute cwd with every non-alphanumeric character replaced by "-".

Record types that carry conversation: "user" and "assistant" (field "message" with "role" and
"content"; content is a string or a list of blocks of type text / thinking / tool_use /
tool_result). "summary" records hold Claude Code's own compaction notes. Everything else
(attachment, queue-operation, last-prompt, tools-used, ...) is dropped. Records with
isSidechain=true are subagent transcripts and are dropped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..model import Doc, Message, Session, ToolCall
from .base import Source, home, read_dir_docs, read_doc

TOOL_INPUT_CHARS = 240
TOOL_RESULT_CHARS = 320
TOOL_RESULT_ERROR_CHARS = 1200


def project_slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _summarize_tool_input(inp: object) -> str:
    if isinstance(inp, dict):
        # Prefer the fields humans care about; fall back to compact JSON.
        for key in ("command", "file_path", "pattern", "query", "prompt", "description"):
            if key in inp and isinstance(inp[key], str):
                extra = ""
                if key != "file_path" and isinstance(inp.get("file_path"), str):
                    extra = f" {inp['file_path']}"
                return _clip(f"{inp[key]}{extra}", TOOL_INPUT_CHARS)
        return _clip(json.dumps(inp, ensure_ascii=False), TOOL_INPUT_CHARS)
    return _clip(str(inp), TOOL_INPUT_CHARS)


def _text_of(content: object) -> str:
    """Flatten a tool_result content payload to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return "" if content is None else str(content)


class ClaudeCodeSource(Source):
    name = "claude_code"
    display = "Claude Code"

    def __init__(self, root: Path | None = None):
        self.root = root or (home() / ".claude")

    # ---- instruction layers -------------------------------------------------

    def identity_docs(self) -> list[Doc]:
        out = []
        for p in (self.root / "CLAUDE.md",):
            d = read_doc(p, self.name)
            if d:
                out.append(d)
        return out

    def project_docs(self, cwd: str) -> list[Doc]:
        repo = Path(cwd)
        out: list[Doc] = []
        for rel in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"):
            d = read_doc(repo / rel, self.name)
            if d:
                out.append(d)
        out += read_dir_docs(repo / ".claude" / "rules", self.name)
        out += read_dir_docs(self.root / "projects" / project_slug(cwd) / "memory", self.name)
        return out

    # ---- sessions -----------------------------------------------------------

    def session_paths(self, cwd: str) -> list[Path]:
        d = self.root / "projects" / project_slug(cwd)
        if not d.is_dir():
            return []
        paths = [p for p in d.glob("*.jsonl") if p.is_file()]
        return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)

    def read_session(self, path: Path) -> Session:
        s = Session(id=path.stem, source=self.name, path=str(path))
        pending_tools: dict[str, ToolCall] = {}
        with path.open(errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                rtype = rec.get("type")
                if rtype == "summary":
                    txt = str(rec.get("summary", "")).strip()
                    if txt:
                        s.summaries.append(txt)
                    continue
                if rtype not in ("user", "assistant"):
                    continue
                if rec.get("isSidechain") or rec.get("isMeta"):
                    continue  # subagent transcript / harness-injected message
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                if rec.get("isCompactSummary"):
                    txt = _text_of(msg.get("content")).strip()
                    if txt:
                        s.summaries.append(txt)
                    continue
                if not s.cwd and rec.get("cwd"):
                    s.cwd = str(rec["cwd"])
                if not s.branch and rec.get("gitBranch"):
                    s.branch = str(rec["gitBranch"])
                ts = str(rec.get("timestamp", ""))
                if ts:
                    s.started = s.started or ts
                    s.ended = ts
                self._ingest(s, msg, rtype, ts, str(rec.get("uuid", "")), pending_tools)
        return s

    def _ingest(
        self,
        s: Session,
        msg: dict,
        rtype: str,
        ts: str,
        uuid: str,
        pending_tools: dict[str, ToolCall],
    ) -> None:
        content = msg.get("content")
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    texts.append(str(b.get("text", "")))
                elif bt == "tool_use":
                    tc = ToolCall(
                        name=str(b.get("name", "tool")),
                        input_summary=_summarize_tool_input(b.get("input")),
                    )
                    tool_calls.append(tc)
                    tid = b.get("id")
                    if isinstance(tid, str):
                        pending_tools[tid] = tc
                elif bt == "tool_result":
                    tid = b.get("tool_use_id")
                    tc = pending_tools.pop(tid, None) if isinstance(tid, str) else None
                    is_err = bool(b.get("is_error"))
                    text = _text_of(b.get("content"))
                    limit = TOOL_RESULT_ERROR_CHARS if is_err else TOOL_RESULT_CHARS
                    if tc is not None:
                        tc.result_summary = _clip(text, limit)
                        tc.is_error = is_err
                    # A tool_result-only user record is not a human turn; skip it.
                # thinking blocks are dropped on purpose
        text = "\n".join(t for t in texts if t and t.strip()).strip()
        if rtype == "user" and text.startswith("<system-reminder>") and "</system-reminder>" in text:
            # Strip injected reminders; keep anything the human typed after them.
            text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
        if not text and not tool_calls:
            return
        if rtype == "user" and not text:
            return
        # Merge consecutive assistant records (Claude Code writes one per API block).
        if (
            rtype == "assistant"
            and s.messages
            and s.messages[-1].role == "assistant"
            and s.messages[-1].uuid != uuid
        ):
            last = s.messages[-1]
            if text:
                last.text = (last.text + "\n" + text).strip()
            last.tool_calls.extend(tool_calls)
            last.timestamp = ts or last.timestamp
            return
        s.messages.append(
            Message(role=rtype, text=text, tool_calls=tool_calls, timestamp=ts, uuid=uuid)
        )
