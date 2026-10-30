"""Codex CLI on-disk state.

Expected layout (Codex CLI 0.2x+; verify on a real machine, formats are not stable):

    ~/.codex/AGENTS.md                                    user-level instructions
    ~/.codex/config.toml                                  model / approval preferences
    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl one record per line
    <repo>/AGENTS.md (+ parent dirs)                      project instructions

Session record shapes seen in the wild, all handled:
    {"type":"session_meta","payload":{"id":..,"cwd":..,"cli_version":..}}
    {"type":"response_item","payload":{"type":"message","role":"user"|"assistant","content":[{"type":"input_text"|"output_text","text":..}]}}
    {"type":"response_item","payload":{"type":"function_call","name":"shell","arguments":"{...json...}","call_id":..}}
    {"type":"response_item","payload":{"type":"function_call_output","call_id":..,"output":"..."}}
    {"type":"event_msg","payload":{"type":"agent_message"|"user_message","message":..}}
    older: top-level {"id":..,"timestamp":..,"instructions":..,"cwd":..} on line 1, then items
           {"type":"message","role":..,"content":[..]} / {"type":"function_call",..}
Unknown record types are ignored.
"""

from __future__ import annotations

import json
import os
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
        out = []
        for name in ("AGENTS.md", "config.toml"):
            d = read_doc(self.root / name, self.name)
            if d:
                out.append(d)
        return out

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
                elif ptype == "function_call":
                    tc = ToolCall(name=str(payload.get("name", "tool")),
                                  input_summary=_args_summary(payload.get("arguments")))
                    cid = payload.get("call_id")
                    if isinstance(cid, str):
                        pending[cid] = tc
                    self._add_tool(s, tc, ts)
                elif ptype == "function_call_output":
                    cid = payload.get("call_id")
                    tc = pending.pop(cid, None) if isinstance(cid, str) else None
                    if tc is None:
                        continue
                    out = payload.get("output")
                    if isinstance(out, dict):
                        out = out.get("output") or json.dumps(out)
                    out = str(out or "")
                    is_err = any(k in out[:200].lower() for k in ("error", "traceback", "exit code: 1"))
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
