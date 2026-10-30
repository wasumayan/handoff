"""Scenarios on disk, and the throwaway work tree each run happens in.

A scenario directory holds:
    task.md       what the user originally asked
    repo/         the project at the midpoint, rules file named CLAUDE.md
    source/       the midpoint session: claude_code.jsonl and/or codex.jsonl (cwd = /WORKDIR)
    hidden/       tests the recipient never sees; copied in only to grade
    solution/     a reference finish, used by the `fake` agent
    ideal.md      a hand-written handoff: the upper bound for what a handoff could carry
    checks.json   test command, preference checks, handoff recall patterns, redo patterns, rubric
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from handoff.sources.claude_code import project_slug

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "eval" / "scenarios"
SOURCE_FILES = {"claude_code": "claude_code.jsonl", "codex": "codex.jsonl"}
RULES_FILE = {"claude_code": "CLAUDE.md", "codex": "AGENTS.md"}
GIT = ["git", "-c", "user.email=eval@example.invalid", "-c", "user.name=eval",
       "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


@dataclass
class Scenario:
    id: str
    dir: Path

    @property
    def task(self) -> str:
        return (self.dir / "task.md").read_text().strip()

    @property
    def checks(self) -> dict:
        return json.loads((self.dir / "checks.json").read_text())

    @property
    def sources(self) -> list[str]:
        return [s for s, f in SOURCE_FILES.items() if (self.dir / "source" / f).is_file()]

    @property
    def ideal(self) -> str:
        p = self.dir / "ideal.md"
        return p.read_text() if p.is_file() else ""


def load_scenarios(only: list[str] | None = None) -> list[Scenario]:
    out = [Scenario(p.name, p) for p in sorted(SCENARIOS.iterdir())
           if p.is_dir() and (p / "checks.json").is_file()]
    if only:
        out = [s for s in out if s.id in only or s.id.split("_", 1)[0] in only]
    return out


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run([*GIT, *args], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


@dataclass
class Work:
    """One run's private directory, outside the handoff repo so the agent cannot reach the scenario's
    solution or hidden tests by walking up from its working directory."""
    root: Path
    repo: Path
    source_home: Path  # fake ~ holding only the source session; deleted before the agent starts
    agent_home: Path  # isolated config dirs for the recipient agent
    session_file: Path

    def drop_source(self) -> None:
        shutil.rmtree(self.source_home, ignore_errors=True)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def prepare(sc: Scenario, source: str) -> Work:
    root = Path(tempfile.mkdtemp(prefix=f"handoff-eval-{sc.id}-")).resolve()
    repo = root / "repo"
    shutil.copytree(sc.dir / "repo", repo)
    rules = RULES_FILE[source]
    if rules != "CLAUDE.md" and (repo / "CLAUDE.md").exists():
        (repo / "CLAUDE.md").rename(repo / rules)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "midpoint")
    session = (sc.dir / "source" / SOURCE_FILES[source]).read_text().replace("/WORKDIR", str(repo))
    home = root / "source-home"
    if source == "claude_code":
        dest = home / ".claude" / "projects" / project_slug(str(repo)) / f"{sc.id}.jsonl"
    else:
        dest = home / ".codex" / "sessions" / "2026" / "10" / "27" / f"rollout-2026-10-27T09-00-00-{sc.id}.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text(session)
    agent_home = root / "agent-home"
    agent_home.mkdir()
    return Work(root=root, repo=repo, source_home=home, agent_home=agent_home, session_file=dest)


def raw_transcript(session_file: Path, source: str) -> str:
    """The "just paste the chat" baseline: every message and every tool call with its full output,
    in order, no layering and no trimming."""
    out = ["# Previous session transcript\n"]
    for line in session_file.read_text().splitlines():
        rec = json.loads(line)
        out.extend(_raw_claude(rec) if source == "claude_code" else _raw_codex(rec))
    return "\n".join(out) + "\n"


def _raw_claude(rec: dict) -> list[str]:
    if rec.get("type") not in ("user", "assistant"):
        return []
    content = rec["message"]["content"]
    if isinstance(content, str):
        return [f"## {rec['type']}\n\n{content}\n"]
    out = []
    for b in content:
        if b.get("type") == "text" and b.get("text"):
            out.append(f"## {rec['type']}\n\n{b['text']}\n")
        elif b.get("type") == "tool_use":
            out.append(f"## tool call: {b['name']}\n\n```json\n{json.dumps(b['input'], indent=1)}\n```\n")
        elif b.get("type") == "tool_result":
            out.append(f"## tool result\n\n```\n{b.get('content', '')}\n```\n")
    return out


def _raw_codex(rec: dict) -> list[str]:
    p = rec.get("payload") or {}
    t = p.get("type")
    if rec.get("type") != "response_item":
        return []
    if t == "message" and p.get("role") in ("user", "assistant"):
        text = "".join(c.get("text", "") for c in p.get("content", []))
        if p["role"] == "user" and text.startswith("# AGENTS.md instructions"):
            return []
        return [f"## {p['role']}\n\n{text}\n"]
    if t in ("function_call", "custom_tool_call"):
        return [f"## tool call: {p.get('name')}\n\n```\n{p.get('arguments') or p.get('input')}\n```\n"]
    if t in ("function_call_output", "custom_tool_call_output"):
        return [f"## tool result\n\n```\n{p.get('output')}\n```\n"]
    return []


# ---------------------------------------------------------------- grading


def run_hidden_tests(sc: Scenario, repo: Path, python: str, timeout: int = 300) -> tuple[bool, str]:
    hidden = repo / "hidden"
    shutil.copytree(sc.dir / "hidden", hidden, dirs_exist_ok=True)
    cmd = [python if c == "python" else c for c in sc.checks["test_cmd"]]
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PYTHONPATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1"})
        ok, out = r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        ok, out = False, "hidden tests timed out"
    shutil.rmtree(hidden, ignore_errors=True)
    return ok, out


def changed_since_midpoint(repo: Path) -> set[str]:
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    committed = git(repo, "diff", "--name-only", root, "HEAD", check=False).split()
    dirty = [ln[3:].strip('"') for ln in git(repo, "status", "--short", "--untracked-files=all",
                                             check=False).splitlines() if len(ln) > 3]
    return set(committed) | set(dirty)


def pref_checks(repo: Path, checks: dict, commands: list[str]) -> dict[str, bool]:
    """Rules from the project's instruction file, checked from the final tree and command log."""
    out: dict[str, bool] = {}
    changed = changed_since_midpoint(repo)
    for c in checks.get("pref_checks", []):
        t, name = c["type"], c["name"]
        if t == "file_not_contains":
            p = repo / c["file"]
            out[name] = p.is_file() and c["needle"] not in p.read_text()
        elif t == "tree_not_contains":
            out[name] = all(c["needle"] not in p.read_text() for p in repo.glob(c["glob"]))
        elif t == "tree_contains":
            out[name] = any(c["needle"] in p.read_text() for p in repo.glob(c["glob"]))
        elif t == "file_unchanged":
            out[name] = c["file"] not in changed
        elif t == "tree_unchanged":
            out[name] = not any(Path(x).match(c["glob"]) for x in changed)
        elif t == "tree_changed":
            out[name] = any(Path(x).match(c["glob"]) for x in changed)
        elif t == "commands_match":
            out[name] = any(re.search(c["pattern"], cmd) for cmd in commands)
        elif t == "commands_not_match":
            out[name] = not any(re.search(c["pattern"], cmd) for cmd in commands)
        else:
            raise ValueError(f"unknown pref check type {t!r}")
    return out


def handoff_recall(text: str, checks: dict) -> dict[str, dict[str, bool]]:
    """Which of the scenario's session-only facts, user corrections and dead ends the handoff text
    carries. Regex matching: a paraphrase the pattern does not anticipate counts as a miss, which
    under-scores model-written handoffs more than verbatim ones."""
    hc = checks.get("handoff_checks", {})
    return {cat: {i["name"]: bool(re.search(i["pattern"], text)) for i in hc.get(cat, [])}
            for cat in ("facts", "corrections", "dead_ends")}


def redo_count(checks: dict, commands: list[str], files_written: list[str]) -> int:
    """Actions that repeat a dead end or already-finished work, per the scenario's redo patterns.
    Each pattern matches either the agent's shell commands ("on": "commands") or the paths of files
    it wrote ("on": "files")."""
    n = 0
    for p in checks.get("redo_patterns", []):
        log = files_written if p.get("on") == "files" else commands
        n += sum(1 for x in log if re.search(p["pattern"], x))
    return n
