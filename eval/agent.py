"""Minimal tool-calling agent: a stand-in recipient when Codex / Claude Code are not installed.

It is deliberately dumb. The instruction file is the system prompt, exactly as Codex would load
AGENTS.md. Tools: read_file, write_file, run. Capped turns. Logs every command for the pref checks.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from handoff.backend import Backend, BackendError

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file relative to the repo root.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file (full content) relative to the repo root.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run", "description": "Run a shell command in the repo root. Returns stdout+stderr and exit code.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "done", "description": "Call when the task is complete.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]


@dataclass
class AgentResult:
    turns: int = 0
    tool_calls: int = 0
    commands: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finished: bool = False
    error: str = ""
    transcript: list[str] = field(default_factory=list)


def _exec(root: Path, name: str, args: dict, res: AgentResult) -> str:
    try:
        if name == "read_file":
            p = (root / args["path"]).resolve()
            if root.resolve() not in p.parents and p != root.resolve():
                return "error: path outside repo"
            return p.read_text()[:20000] if p.is_file() else f"error: no such file {args['path']}"
        if name == "write_file":
            p = (root / args["path"]).resolve()
            if root.resolve() not in p.parents:
                return "error: path outside repo"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"wrote {len(args['content'])} chars to {args['path']}"
        if name == "run":
            cmd = str(args["command"])
            res.commands.append(cmd)
            r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=120)
            out = (r.stdout + r.stderr)[-6000:]
            return f"exit code: {r.returncode}\n{out}"
        if name == "done":
            res.finished = True
            return "ok"
    except subprocess.TimeoutExpired:
        return "error: command timed out"
    except (OSError, KeyError, TypeError) as e:
        return f"error: {e}"
    return f"error: unknown tool {name}"


def run_agent(root: Path, instructions: str, task_prompt: str, backend: Backend,
              max_turns: int = 25) -> AgentResult:
    res = AgentResult()
    system = ("You are a coding agent working in a git repository. You can read and write files and "
              "run shell commands. The following is the repository's instruction file; follow it.\n\n"
              + instructions)
    msgs: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": task_prompt}]
    for _ in range(max_turns):
        res.turns += 1
        try:
            msg = backend.complete(msgs, tools=TOOLS, temperature=0.1, max_tokens=4000)
        except BackendError as e:
            res.error = str(e)
            break
        u = msg.pop("_usage", {})
        res.prompt_tokens += u.get("prompt_tokens", 0)
        res.completion_tokens += u.get("completion_tokens", 0)
        msgs.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if msg.get("content"):
            res.transcript.append(f"assistant: {msg['content'][:500]}")
        calls = msg.get("tool_calls") or []
        if not calls:
            # No tool call and no explicit done: nudge once, then stop.
            if res.turns >= 2 and msgs[-2].get("role") == "user" and "call done" in str(msgs[-2].get("content")):
                break
            msgs.append({"role": "user", "content": "If the task is complete, call done. Otherwise continue."})
            continue
        for call in calls:
            res.tool_calls += 1
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            out = _exec(root, fn.get("name", ""), args, res)
            res.transcript.append(f"tool {fn.get('name')}({json.dumps(args)[:200]}) -> {out[:300]}")
            msgs.append({"role": "tool", "tool_call_id": call.get("id"), "content": out})
        if res.finished:
            break
    return res
