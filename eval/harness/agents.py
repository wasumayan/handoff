"""Recipient agents: what runs in the work tree after the handoff is installed.

Every adapter returns an AgentRun with the same fields, so variants and agents compare on one table.
Counts an adapter cannot observe stay None (shown as "n/a"), never 0.

    fake     copies the scenario's reference solution; checks the harness, measures nothing
    standin  eval/agent.py against HANDOFF_BASE_URL / HANDOFF_MODEL (any OpenAI-compatible server)
    claude   Claude Code CLI, `claude -p --output-format stream-json`
    codex    Codex CLI, `codex exec --json`

Isolation (default): the CLI agents get fresh config dirs (CLAUDE_CONFIG_DIR, CODEX_HOME) inside the
run's work dir, so the operator's own memory, instructions, MCP servers and hooks do not leak into
the measurement, and runs do not land in their history. Auth then comes from ANTHROPIC_API_KEY /
OPENAI_API_KEY. --no-isolate uses the operator's normal login instead.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

from .scenario import ROOT, Scenario

NUDGE = "No further input is available from the user. Use your best judgment and finish the task."


@dataclass
class AgentRun:
    invocations: int = 0
    exit_code: int | None = None
    timed_out: bool = False
    seconds: float = 0.0
    turns: int | None = None
    tool_calls: int | None = None
    input_tokens: int | None = None  # uncached input
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    peak_context_tokens: int | None = None  # largest single model call's input, all sources
    cost_usd: float | None = None
    questions: int = 0  # times the agent stopped to ask the user something
    blocked_actions: int = 0  # tool calls refused by the agent's permission system
    commands: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    final_message: str = ""
    session_id: str = ""
    transcript_tail: str = ""
    error: str = ""

    @property
    def asked(self) -> bool:
        return self.final_message.rstrip().endswith("?")


def merge(a: AgentRun, b: AgentRun) -> AgentRun:
    """Combine a run and its continuation (a nudge): sums, maxima, the latest message and id."""
    out = AgentRun()
    for f in fields(AgentRun):
        x, y = getattr(a, f.name), getattr(b, f.name)
        if f.name in ("peak_context_tokens",):
            v = max((z for z in (x, y) if z is not None), default=None)
        elif f.name in ("final_message", "session_id", "transcript_tail", "exit_code", "error"):
            v = y if y not in ("", None) else x
        elif isinstance(x, list):
            v = x + y
        elif isinstance(x, bool):
            v = x or y
        elif x is None and y is None:
            v = None
        elif isinstance(x, (int, float)) or isinstance(y, (int, float)):
            v = (x or 0) + (y or 0)
        else:
            v = y
        setattr(out, f.name, v)
    return out


# ---------------------------------------------------------------- output parsers


def parse_claude_stream(text: str) -> AgentRun:
    """Claude Code `-p --output-format stream-json --verbose`. Parallel tool calls in one API message
    arrive as separate assistant events carrying the same usage, so per-call usage is keyed by
    message id."""
    run = AgentRun(tool_calls=0)
    per_call: dict[str, int] = {}
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = r.get("type")
        if t == "system" and r.get("subtype") == "init":
            run.session_id = r.get("session_id", run.session_id)
        elif t == "assistant":
            m = r.get("message", {})
            u = m.get("usage") or {}
            if m.get("id"):
                per_call[m["id"]] = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                     + u.get("cache_creation_input_tokens", 0))
            for b in m.get("content", []):
                if b.get("type") != "tool_use":
                    continue
                run.tool_calls += 1
                name, inp = b.get("name", ""), b.get("input") or {}
                if name == "AskUserQuestion":
                    run.questions += 1
                elif name == "Bash" and isinstance(inp.get("command"), str):
                    run.commands.append(inp["command"])
                elif name in ("Write", "Edit", "MultiEdit", "NotebookEdit") and inp.get("file_path"):
                    run.files_written.append(str(inp["file_path"]))
        elif t == "result":
            u = r.get("usage") or {}
            run.input_tokens = u.get("input_tokens")
            run.cache_read_tokens = u.get("cache_read_input_tokens")
            run.cache_write_tokens = u.get("cache_creation_input_tokens")
            run.output_tokens = u.get("output_tokens")
            run.cost_usd = r.get("total_cost_usd")
            run.turns = r.get("num_turns")
            run.final_message = str(r.get("result") or "")
            run.session_id = r.get("session_id", run.session_id)
            run.blocked_actions = len(r.get("permission_denials") or [])
            if r.get("is_error"):
                run.error = str(r.get("subtype", "error"))
    run.peak_context_tokens = max(per_call.values()) if per_call else None
    if run.asked:
        run.questions += 1
    return run


def parse_codex_events(text: str) -> AgentRun:
    """Codex `exec --json`. Usage is reported per turn, not per model call, so the peak context of a
    single call is not observable: peak_context_tokens stays None. No cost is reported."""
    run = AgentRun(tool_calls=0, turns=0)
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0}
    seen_usage = False
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = r.get("type")
        item = r.get("item") or {}
        if t == "thread.started":
            run.session_id = r.get("thread_id", "")
        elif t == "turn.completed":
            run.turns += 1
            seen_usage = True
            for k in totals:
                totals[k] += (r.get("usage") or {}).get(k, 0) or 0
        elif t == "turn.failed":
            run.error = str((r.get("error") or {}).get("message", "turn failed"))
        elif t == "item.completed":
            it = item.get("type")
            if it == "agent_message":
                run.final_message = item.get("text", "")
            elif it == "command_execution":
                run.tool_calls += 1
                run.commands.append(str(item.get("command", "")))
            elif it == "file_change":
                run.tool_calls += 1
                for ch in item.get("changes") or []:
                    if ch.get("path"):
                        run.files_written.append(str(ch["path"]))
            elif it in ("mcp_tool_call", "web_search"):
                run.tool_calls += 1
    if seen_usage:
        # Codex reports input_tokens including the cached part.
        run.input_tokens = totals["input_tokens"] - totals["cached_input_tokens"]
        run.cache_read_tokens = totals["cached_input_tokens"]
        run.cache_write_tokens = totals["cache_write_input_tokens"]
        run.output_tokens = totals["output_tokens"]
    if run.asked:
        run.questions += 1
    return run


# ---------------------------------------------------------------- adapters


class Agent:
    name = "base"
    #: the handoff recipient profile whose instruction file this agent reads natively; None means
    #: "whichever profile is the cross pairing for the source"
    profile: str | None = None

    def start(self, sc: Scenario, repo: Path, home: Path, prompt: str, profile: str) -> AgentRun:
        raise NotImplementedError

    def resume(self, sc: Scenario, repo: Path, home: Path, prev: AgentRun, text: str,
               profile: str) -> AgentRun | None:
        return None


class FakeAgent(Agent):
    name = "fake"

    def start(self, sc, repo, home, prompt, profile):
        t0 = time.time()
        sol = sc.dir / "solution"
        written = []
        for p in sol.rglob("*"):
            dest = repo / p.relative_to(sol)
            if p.is_file() and not (dest.is_file() and dest.read_bytes() == p.read_bytes()):
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p, dest)
                written.append(str(p.relative_to(sol)))
        return AgentRun(invocations=1, exit_code=0, seconds=round(time.time() - t0, 2), turns=1,
                        tool_calls=len(written), files_written=written, final_message="done")


class StandInAgent(Agent):
    name = "standin"

    def __init__(self, backend, max_turns: int = 30):
        if backend is None:
            raise SystemExit("--agent standin needs HANDOFF_BASE_URL and HANDOFF_MODEL")
        self.backend = backend
        self.max_turns = max_turns
        self._messages: dict[str, list[dict]] = {}

    def _go(self, repo, prompt, profile, messages=None) -> AgentRun:
        sys.path.insert(0, str(ROOT / "eval"))
        from agent import run_agent  # noqa: PLC0415  (eval/agent.py; not a package module)

        from handoff.recipients import get_recipient  # noqa: PLC0415
        instr = get_recipient(profile).install_path(str(repo))
        t0 = time.time()
        res = run_agent(repo, instr.read_text() if instr.is_file() else "", prompt, self.backend,
                        self.max_turns, messages=messages)
        key = str(repo)
        self._messages[key] = res.messages
        last = next((m.get("content") for m in reversed(res.messages)
                     if m.get("role") == "assistant" and m.get("content")), "") or ""
        return AgentRun(invocations=1, exit_code=0, seconds=round(time.time() - t0, 2), turns=res.turns,
                        tool_calls=res.tool_calls, input_tokens=res.prompt_tokens,
                        output_tokens=res.completion_tokens, peak_context_tokens=res.peak_prompt_tokens,
                        questions=res.questions, commands=res.commands, files_written=res.files_written,
                        final_message=last, session_id=key, transcript_tail="\n".join(res.transcript[-20:]),
                        error=res.error)

    def start(self, sc, repo, home, prompt, profile):
        return self._go(repo, prompt, profile)

    def resume(self, sc, repo, home, prev, text, profile):
        msgs = self._messages.get(prev.session_id)
        return self._go(repo, text, profile, messages=msgs) if msgs else None


class _CLI(Agent):
    binary = ""

    def __init__(self, model: str = "", isolate: bool = True, timeout: int = 1200, extra: list[str] | None = None):
        if not shutil.which(self.binary):
            raise SystemExit(f"--agent {self.name}: `{self.binary}` is not on PATH")
        self.model, self.isolate, self.timeout, self.extra = model, isolate, timeout, extra or []

    def env(self, home: Path) -> dict[str, str]:
        return dict(os.environ)

    def parse(self, text: str) -> AgentRun:
        raise NotImplementedError

    def invoke(self, cmd: list[str], repo: Path, home: Path) -> AgentRun:
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=repo, env=self.env(home), stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=self.timeout)
            out, code, timed_out = r.stdout, r.returncode, False
            err = r.stderr
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err, code, timed_out = "", None, True
        run = self.parse(out)
        run.invocations, run.exit_code, run.timed_out = 1, code, timed_out
        run.seconds = round(time.time() - t0, 2)
        run.transcript_tail = (out[-3000:] + ("\n[stderr]\n" + err[-2000:] if err else ""))
        if code not in (0, None) and not run.error:
            run.error = f"exit {code}: {err.strip()[-300:]}"
        if timed_out:
            run.error = f"timed out after {self.timeout}s"
        return run


class ClaudeAgent(_CLI):
    name = "claude"
    binary = "claude"
    profile = "claude"

    def env(self, home):
        env = dict(os.environ)
        if self.isolate:
            env["CLAUDE_CONFIG_DIR"] = str(home / "claude")
        return env

    def options(self) -> list[str]:
        # The prompt must precede these: --allowedTools is variadic and would swallow it.
        cmd = ["-p", "--output-format", "stream-json", "--verbose",
               "--setting-sources", "project", "--strict-mcp-config",
               "--permission-mode", "acceptEdits",
               "--allowedTools", "Bash", "Read", "Edit", "Write", "Glob", "Grep"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra

    def parse(self, text):
        return parse_claude_stream(text)

    def start(self, sc, repo, home, prompt, profile):
        return self.invoke(["claude", prompt, *self.options()], repo, home)

    def resume(self, sc, repo, home, prev, text, profile):
        if not prev.session_id:
            return None
        return self.invoke(["claude", text, *self.options(), "--resume", prev.session_id], repo, home)


class CodexAgent(_CLI):
    name = "codex"
    binary = "codex"
    profile = "codex"

    def env(self, home):
        env = dict(os.environ)
        if self.isolate:
            codex_home = home / "codex"
            if not codex_home.exists():
                codex_home.mkdir(parents=True)
                key = env.get("OPENAI_API_KEY", "")
                if key:
                    subprocess.run(["codex", "login", "--with-api-key"], input=key, text=True,
                                   env={**env, "CODEX_HOME": str(codex_home)}, capture_output=True)
            env["CODEX_HOME"] = str(codex_home)
        return env

    def flags(self) -> list[str]:
        cmd = ["--json", "--skip-git-repo-check", "-c", 'sandbox_mode="workspace-write"',
               "-c", 'approval_policy="never"']
        if self.isolate:
            cmd.append("--ignore-user-config")
        if self.model:
            cmd += ["-m", self.model]
        return cmd + self.extra

    def parse(self, text):
        return parse_codex_events(text)

    def start(self, sc, repo, home, prompt, profile):
        return self.invoke(["codex", "exec", *self.flags(), prompt], repo, home)

    def resume(self, sc, repo, home, prev, text, profile):
        if not prev.session_id:
            return None
        return self.invoke(["codex", "exec", "resume", *self.flags(), prev.session_id, text], repo, home)


def make_agent(name: str, *, backend=None, model: str = "", isolate: bool = True, timeout: int = 1200,
               max_turns: int = 30, extra: list[str] | None = None) -> Agent:
    if name == "fake":
        return FakeAgent()
    if name == "standin":
        return StandInAgent(backend, max_turns)
    if name == "claude":
        return ClaudeAgent(model, isolate, timeout, extra)
    if name == "codex":
        return CodexAgent(model, isolate, timeout, extra)
    raise SystemExit(f"unknown agent {name!r}; known: fake, standin, claude, codex")


def python_for_tests() -> str:
    return sys.executable
