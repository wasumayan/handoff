"""Codex CLI on-disk state.

Layout (verified against Codex CLI 0.142-0.146 sessions; formats are not stable):

    ~/.codex/AGENTS.md                                    user-level instructions
    ~/.codex/config.toml                                  not read: plugins, MCP env, paths
    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl one record per line
    <repo>/AGENTS.md (+ parent dirs)                      project instructions

Session record shapes handled:
    {"type":"session_meta","payload":{"id":..,"cwd":..,"cli_version":..,"thread_source":"user"|"subagent"}}
    {"type":"response_item","payload":{"type":"message","role":"user"|"assistant","content":[{"type":"input_text"|"output_text","text":..}]}}
    {"type":"event_msg","payload":{"type":"agent_message"|"user_message","message":..}}  (a second copy of each turn)
    {"type":"response_item","payload":{"type":"function_call","name":"shell"|"exec_command"|"wait",..,"call_id":..}}
    {"type":"response_item","payload":{"type":"custom_tool_call","name":"apply_patch"|"exec","input":"<patch or JS>",..}}
    {"type":"response_item","payload":{"type":"function_call_output"|"custom_tool_call_output","call_id":..,"output":..}}
        output: plain text with a harness header (Exit code / Process exited with code / Script ...),
        JSON-encoded input_text blocks, or {"output":..,"metadata":{"exit_code":..}}
    older: top-level {"id":..,"timestamp":..,"instructions":..,"cwd":..} on line 1, then items
           {"type":"message","role":..,"content":[..]} / {"type":"function_call",..}
Unknown record types are ignored.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from ..model import Doc, Message, Session, ToolCall
from .base import Source, home, read_doc

TOOL_INPUT_CHARS = 240
TOOL_RESULT_CHARS = 320
TOOL_RESULT_ERROR_CHARS = 1200
CWD_PROBE_LINES = 40


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _args_summary(args: object) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return _clip(args, TOOL_INPUT_CHARS)
    if isinstance(args, dict):
        cmd = args.get("command") or args.get("cmd")
        if isinstance(cmd, list):
            return _clip(" ".join(str(c) for c in cmd), TOOL_INPUT_CHARS)
        if isinstance(cmd, str):
            return _clip(cmd, TOOL_INPUT_CHARS)
        for k in ("path", "file_path", "input", "patch", "query"):
            if isinstance(args.get(k), str):
                return _clip(args[k], TOOL_INPUT_CHARS)
        return _clip(json.dumps(args, ensure_ascii=False), TOOL_INPUT_CHARS)
    return _clip(str(args), TOOL_INPUT_CHARS)


_JS_CMD = re.compile(r'\bcmd\s*:\s*("(?:[^"\\]|\\.)*"|`[^`]*`)')
_PATCH_FILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\]+)")
_OUTPUT_HEADER = re.compile(
    r"(Chunk ID|Wall time|Process exited with code|Original token count|Exit code|"
    r"Script (?:completed|failed|error|terminated|running with cell ID))\b.*|Output:|"
)
_SCRIPT_ERRORS = ("Script failed", "Script error", "Script terminated")
_EXIT = re.compile(r"(?:Process exited with code|Exit code:)\s*(-?\d+)")


def _custom_input_summary(name: str, inp: object) -> str:
    """Summarize a custom tool call: raw patch text (apply_patch) or JS code (0.146 `exec`)."""
    if not isinstance(inp, str):
        return _args_summary(inp)
    parts = []
    for m in _JS_CMD.finditer(inp):
        lit = m.group(1)
        try:
            parts.append(json.loads(lit) if lit.startswith('"') else lit[1:-1])
        except json.JSONDecodeError:
            parts.append(lit[1:-1])
    files = _PATCH_FILE.findall(inp)
    if files:
        parts.append("apply_patch " + ", ".join(f.strip() for f in files))
    if not parts:
        tools = sorted(set(re.findall(r"\btools\.(\w+)", inp)))
        parts = [", ".join(tools)] if tools else [inp.strip().split("\n", 1)[0]]
    return _clip(" ; ".join(parts), TOOL_INPUT_CHARS)


def _wait_cell(args: object) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return ""
    return str(args.get("cell_id", "")) if isinstance(args, dict) else ""


def _tool_output(raw: object) -> tuple[str, bool, str]:
    """Return (body, is_error, running_cell_id) for a tool output in any Codex version's shape:
    plain text with a harness header, JSON-encoded input_text blocks, or {"output", "metadata"}."""
    exit_code = None
    if isinstance(raw, str) and raw[:1] in "[{":
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            pass
    if isinstance(raw, dict):
        meta = raw.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("exit_code"), int):
            exit_code = meta["exit_code"]
        raw = raw["output"] if isinstance(raw.get("output"), str) else json.dumps(raw)
    text = _content_text(raw) if isinstance(raw, list) else str(raw or "")
    lines = text.split("\n")
    i = 0
    while i < len(lines) and _OUTPUT_HEADER.fullmatch(lines[i].strip()):
        i += 1
    header, body = "\n".join(lines[:i]), "\n".join(lines[i:]).strip()
    m = _EXIT.search(header)
    if m:
        exit_code = int(m.group(1))
    running = re.match(r"Script running with cell ID (\S+)", text)
    if exit_code is not None or text.startswith("Script "):
        is_err = text.startswith(_SCRIPT_ERRORS) or bool(exit_code)
    else:  # headerless legacy output: fall back to keywords
        is_err = any(k in text[:200].lower() for k in ("error", "traceback"))
    return body, is_err, running.group(1) if running else ""


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                out.append(b["text"])
            elif isinstance(b, str):
                out.append(b)
        return "\n".join(out)
    return ""


def _same_dir(recorded: str, want: Path) -> bool:
    """Path equality that holds on case-insensitive filesystems: `cd proj` into `Proj` on
    macOS resolves to the lowercase spelling, while Codex recorded the on-disk one."""
    if str(Path(recorded)) == str(want):
        return True
    try:
        return os.path.samefile(recorded, want)
    except OSError:
        return False


class _Copies:
    """Codex writes each turn twice: a response_item message and an event_msg copy, in either
    order (0.146: human turns item-first, assistant turns event-first). A copy cancels one
    unmatched copy from the other stream, so a human repeating themselves is still kept."""

    def __init__(self) -> None:
        self.open = {"item": Counter(), "event": Counter()}

    def first(self, origin: str, role: str, text: str) -> bool:
        key = (role, text)
        other = self.open["event" if origin == "item" else "item"]
        if other[key]:
            other[key] -= 1
            return False
        self.open[origin][key] += 1
        return True


def _is_injected(text: str) -> bool:
    t = text.lstrip()
    return t.startswith(("<environment_context>", "<user_instructions>", "# AGENTS.md",
                         "<permissions instructions>"))


class CodexSource(Source):
    name = "codex"
    display = "Codex CLI"

    def __init__(self, root: Path | None = None):
        self.root = root or (home() / ".codex")

    def identity_docs(self) -> list[Doc]:
        # config.toml is machine setup (plugins, MCP server env, binary paths), not instructions;
        # on a real machine it was 15% of the budget and is where API keys live.
        d = read_doc(self.root / "AGENTS.md", self.name)
        return [d] if d else []

    def project_docs(self, cwd: str) -> list[Doc]:
        out: list[Doc] = []
        p = Path(cwd).resolve()
        seen = set()
        for d in [p, *p.parents]:
            doc = read_doc(d / "AGENTS.md", self.name)
            if doc and doc.path not in seen:
                out.append(doc)
                seen.add(doc.path)
            if (d / ".git").exists():
                break
        return out

    def _session_cwd(self, path: Path) -> str:
        """The session's cwd, or "" for a sub-agent thread: those carry the parent's cwd but are
        not the user's sessions (cf. isSidechain in Claude Code)."""
        try:
            with path.open(errors="replace") as fh:
                for _ in range(CWD_PROBE_LINES):
                    line = fh.readline()
                    if not line:
                        break
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                    src = payload.get("source")
                    if payload.get("thread_source") == "subagent" or (
                        isinstance(src, dict) and "subagent" in src
                    ):
                        return ""
                    for holder in (payload, rec):
                        if isinstance(holder.get("cwd"), str):
                            return holder["cwd"]
                    # environment_context messages embed <cwd>...</cwd>
                    txt = _content_text(payload.get("content") or rec.get("content"))
                    if "<cwd>" in txt:
                        return txt.split("<cwd>", 1)[1].split("</cwd>", 1)[0].strip()
        except OSError:
            pass
        return ""

    def session_paths(self, cwd: str) -> list[Path]:
        d = self.root / "sessions"
        if not d.is_dir():
            return []
        want = Path(cwd).resolve()
        same: dict[str, bool] = {}  # sessions share a few dozen distinct cwds
        paths = []
        for p in d.rglob("*.jsonl"):
            scwd = self._session_cwd(p)
            if not scwd:
                continue
            if scwd not in same:
                same[scwd] = _same_dir(scwd, want)
            if same[scwd]:
                paths.append(p)
        return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)

    def read_session(self, path: Path) -> Session:
        s = Session(id=path.stem, source=self.name, path=str(path))
        pending: dict[str, ToolCall] = {}
        cells: dict[str, ToolCall] = {}  # exec cell id -> the call still running in it
        copies = _Copies()
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
                ts = str(rec.get("timestamp", "") or "")
                if ts:
                    s.started = s.started or ts
                    s.ended = ts
                payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
                if not s.cwd and isinstance(payload.get("cwd"), str):
                    s.cwd = payload["cwd"]
                ptype = payload.get("type") or rec.get("type")
                if ptype == "message" and payload.get("role") in ("user", "assistant"):
                    text = _content_text(payload.get("content")).strip()
                    if not text or (payload["role"] == "user" and _is_injected(text)):
                        continue
                    if copies.first("item", payload["role"], text):
                        self._add(s, payload["role"], text, ts)
                elif ptype in ("function_call", "custom_tool_call"):
                    name = str(payload.get("name", "tool"))
                    cid = payload.get("call_id")
                    cell = _wait_cell(payload.get("arguments")) if name == "wait" else ""
                    if cell in cells:
                        # a poll on a running exec cell: its output belongs to that exec call
                        if isinstance(cid, str):
                            pending[cid] = cells[cell]
                        continue
                    if ptype == "custom_tool_call":
                        summary = _custom_input_summary(name, payload.get("input"))
                    else:
                        summary = _args_summary(payload.get("arguments"))
                    tc = ToolCall(name=name, input_summary=summary)
                    if isinstance(cid, str):
                        pending[cid] = tc
                    self._add_tool(s, tc, ts)
                elif ptype in ("function_call_output", "custom_tool_call_output"):
                    cid = payload.get("call_id")
                    tc = pending.pop(cid, None) if isinstance(cid, str) else None
                    if tc is None:
                        continue
                    out, is_err, running = _tool_output(payload.get("output"))
                    if running:
                        cells[running] = tc
                        out = out or "(still running)"
                    tc.result_summary = _clip(out, TOOL_RESULT_ERROR_CHARS if is_err else TOOL_RESULT_CHARS)
                    tc.is_error = is_err
                elif ptype in ("agent_message", "user_message") and isinstance(payload.get("message"), str):
                    role = "assistant" if ptype == "agent_message" else "user"
                    text = payload["message"].strip()
                    if text and not (role == "user" and _is_injected(text)) and copies.first(
                        "event", role, text
                    ):
                        self._add(s, role, text, ts)
                elif ptype == "compacted" or ptype == "summary":
                    txt = payload.get("message") or payload.get("summary") or ""
                    if isinstance(txt, str) and txt.strip():
                        s.summaries.append(txt.strip())
        return s

    @staticmethod
    def _add(s: Session, role: str, text: str, ts: str) -> None:
        if role == "assistant" and s.messages and s.messages[-1].role == "assistant":
            last = s.messages[-1]
            last.text = (last.text + "\n" + text).strip()
            last.timestamp = ts or last.timestamp
            return
        s.messages.append(Message(role=role, text=text, timestamp=ts))

    @staticmethod
    def _add_tool(s: Session, tc: ToolCall, ts: str) -> None:
        if s.messages and s.messages[-1].role == "assistant":
            s.messages[-1].tool_calls.append(tc)
            return
        s.messages.append(Message(role="assistant", text="", tool_calls=[tc], timestamp=ts))
