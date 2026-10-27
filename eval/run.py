"""Run the handoff evaluation: scenarios x arms x runs -> results table.

    python eval/run.py --recipient fake                       # harness self-check
    python eval/run.py --recipient agent --runs 3             # stand-in agent via HANDOFF_BASE_URL
    python eval/run.py --recipient "cmd:codex exec --full-auto 'Continue the task in AGENTS.md'"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from handoff.backend import Backend, BackendError  # noqa: E402
from handoff.cli import main as handoff_main  # noqa: E402
from handoff.recipients import get_recipient  # noqa: E402
from handoff.sources.claude_code import ClaudeCodeSource, project_slug  # noqa: E402

SCEN = ROOT / "eval" / "scenarios"
ARMS = ("A_raw", "B_dump", "C_adapted")


@dataclass
class Result:
    scenario: str
    arm: str
    run: int
    recipient: str
    passed: bool = False
    test_output: str = ""
    prefs: dict[str, bool] = field(default_factory=dict)
    rubric: dict[str, bool] = field(default_factory=dict)
    turns: int = 0
    tool_calls: int = 0
    prompt_chars: int = 0
    prompt_tokens: int = 0
    seconds: float = 0.0
    error: str = ""


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-c", "user.email=eval@handoff", "-c", "user.name=eval", *args],
                       cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def prepare_workdir(scenario: Path, work: Path) -> tuple[Path, Path]:
    """Copy the repo, init git, build a fake ~/.claude with the session under the right slug."""
    repo = work / "repo"
    shutil.copytree(scenario / "repo", repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "midpoint")
    home = work / "home"
    proj = home / ".claude" / "projects" / project_slug(str(repo))
    proj.mkdir(parents=True)
    src = (scenario / "source" / "claude_code.jsonl").read_text().replace("/WORKDIR", str(repo))
    (proj / "session.jsonl").write_text(src)
    return repo, home


def raw_transcript(repo: Path, home: Path) -> str:
    """Arm A: everything, verbatim-ish, no layering, no trimming of tool output."""
    src = ClaudeCodeSource(home / ".claude")
    path = src.session_paths(str(repo))[0]
    out = ["# Previous session transcript\n"]
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        content = rec["message"]["content"]
        if isinstance(content, str):
            out.append(f"## {rec['type']}\n\n{content}\n")
            continue
        for b in content:
            t = b.get("type")
            if t == "text":
                out.append(f"## {rec['type']}\n\n{b['text']}\n")
            elif t == "tool_use":
                out.append(f"## {rec['type']} tool_use {b['name']}\n\n```json\n{json.dumps(b['input'], indent=1)}\n```\n")
            elif t == "tool_result":
                c = b.get("content")
                c = c if isinstance(c, str) else json.dumps(c)
                out.append(f"## tool_result\n\n```\n{c}\n```\n")
    return "\n".join(out)


def build_artifact(arm: str, repo: Path, home: Path, to: str, env: dict) -> Path:
    rcp = get_recipient(to)
    if arm == "A_raw":
        files = {"HANDOFF.md": raw_transcript(repo, home)}
        return rcp.install(str(repo), files)
    args = ["create", "--to", to, "--path", str(repo), "--from", "claude_code", "--install",
            "--out", str(repo.parent / f"handoff-{arm}")]
    if arm == "B_dump":
        args.append("--no-llm")
    old = dict(os.environ)
    os.environ.update(env)
    os.environ["HANDOFF_HOME_OVERRIDE"] = str(home)
    try:
        rc = handoff_main(args)
    finally:
        os.environ.clear()
        os.environ.update(old)
    if rc != 0:
        raise RuntimeError(f"handoff create failed for {arm}")
    return rcp.install_path(str(repo))


def run_recipient(kind: str, repo: Path, instruction_path: Path, task_prompt: str, scenario: Path,
                  backend: Backend | None, max_turns: int) -> dict:
    if kind == "fake":
        sol = scenario / "solution"
        for p in sol.rglob("*"):
            if p.is_file():
                dest = repo / p.relative_to(sol)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p, dest)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "refactor: apply reference solution", check=False)
        return {"turns": 1, "tool_calls": 0, "commands": ["uv run pytest -q"], "prompt_tokens": 0}
    if kind == "agent":
        from agent import run_agent
        if backend is None:
            raise RuntimeError("--recipient agent needs HANDOFF_BASE_URL / HANDOFF_MODEL")
        res = run_agent(repo, instruction_path.read_text(), task_prompt, backend, max_turns)
        return {"turns": res.turns, "tool_calls": res.tool_calls, "commands": res.commands,
                "prompt_tokens": res.prompt_tokens, "error": res.error,
                "transcript": res.transcript}
    if kind.startswith("cmd:"):
        cmd = shlex.split(kind[4:])
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=1800)
        return {"turns": 0, "tool_calls": 0, "commands": [], "prompt_tokens": 0,
                "transcript": [(r.stdout + r.stderr)[-4000:]],
                "error": "" if r.returncode == 0 else f"exit {r.returncode}"}
    raise ValueError(f"unknown recipient {kind}")


def run_hidden_tests(repo: Path, scenario: Path, checks: dict) -> tuple[bool, str]:
    shutil.copytree(scenario / "hidden", repo / "hidden", dirs_exist_ok=True)
    r = subprocess.run(checks["test_cmd"], cwd=repo, capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PYTHONPATH": str(repo)})
    shutil.rmtree(repo / "hidden", ignore_errors=True)
    return r.returncode == 0, (r.stdout + r.stderr)[-3000:]


def _root_commit(repo: Path) -> str:
    return git(repo, "rev-list", "--max-parents=0", "HEAD").strip()


def _changed_since_midpoint(repo: Path) -> set[str]:
    """Files changed by the recipient: committed since the midpoint commit, or still uncommitted."""
    committed = git(repo, "diff", "--name-only", _root_commit(repo), "HEAD", check=False).split()
    dirty = [ln[3:] for ln in git(repo, "status", "--short", check=False).splitlines() if len(ln) > 3]
    return set(committed) | set(dirty)


def pref_checks(repo: Path, checks: dict, commands: list[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    changed = _changed_since_midpoint(repo)
    for c in checks.get("pref_checks", []):
        t = c["type"]
        if t == "file_not_contains":
            p = repo / c["file"]
            out[c["name"]] = p.is_file() and c["needle"] not in p.read_text()
        elif t == "tree_not_contains":
            out[c["name"]] = all(c["needle"] not in p.read_text() for p in repo.glob(c["glob"]))
        elif t == "file_unchanged":
            out[c["name"]] = c["file"] not in changed
        elif t == "tree_changed":
            out[c["name"]] = any(Path(x).match(c["glob"]) for x in changed)
        elif t == "commands_match":
            out[c["name"]] = any(re.search(c["pattern"], cmd) for cmd in commands)
        elif t == "commands_not_match":
            out[c["name"]] = not any(re.search(c["pattern"], cmd) for cmd in commands)
        elif t == "commits_match":
            subjects = [x for x in git(repo, "log", "--format=%s").splitlines() if x != "midpoint"]
            out[c["name"]] = bool(subjects) and all(re.search(c["pattern"], x) for x in subjects)
    return out


def judge(backend: Backend | None, checks: dict, repo: Path, instruction: str, transcript: list[str]) -> dict[str, bool]:
    qs = checks.get("rubric", [])
    if not qs or backend is None:
        return {}
    diff = git(repo, "diff", _root_commit(repo), check=False)[:12000]
    prompt = (
        "You grade a coding agent's continuation of someone else's work. Answer each question with true or false "
        "based only on the evidence. Respond as a JSON object mapping the question index (\"0\", \"1\", ...) to a boolean.\n\n"
        "## Instruction file the agent was given\n" + instruction[:8000] +
        "\n\n## Agent transcript (truncated)\n" + "\n".join(transcript)[:8000] +
        "\n\n## Final diff\n" + diff +
        "\n\n## Questions\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(qs))
    )
    try:
        raw = backend.chat([{"role": "user", "content": prompt}], json_mode=True, temperature=0)
        obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except (BackendError, ValueError):
        return {}
    return {q: bool(obj.get(str(i), False)) for i, q in enumerate(qs)}


def one(scenario: Path, arm: str, run_idx: int, a: argparse.Namespace, backend: Backend | None,
        out_root: Path) -> Result:
    checks = json.loads((scenario / "checks.json").read_text())
    to = a.to or checks.get("to", "codex")
    res = Result(scenario=scenario.name, arm=arm, run=run_idx, recipient=a.recipient)
    work = out_root / scenario.name / arm / f"run{run_idx}"
    work.mkdir(parents=True)
    t0 = time.time()
    try:
        repo, home = prepare_workdir(scenario, work)
        env = {}
        if arm == "C_adapted":
            if backend is None:
                raise RuntimeError("arm C needs a backend (HANDOFF_BASE_URL / HANDOFF_MODEL)")
            env = {"HANDOFF_BASE_URL": backend.base_url, "HANDOFF_MODEL": backend.model,
                   "HANDOFF_API_KEY": backend.api_key}
        instr = build_artifact(arm, repo, home, to, env)
        res.prompt_chars = len(instr.read_text())
        task_prompt = ("Continue the task described in the instruction file. Finish it, make sure tests "
                       "pass, then call done.")
        r = run_recipient(a.recipient, repo, instr, task_prompt, scenario, backend, a.max_turns)
        res.turns, res.tool_calls = r.get("turns", 0), r.get("tool_calls", 0)
        res.prompt_tokens = r.get("prompt_tokens", 0)
        res.error = r.get("error", "") or ""
        res.passed, res.test_output = run_hidden_tests(repo, scenario, checks)
        res.prefs = pref_checks(repo, checks, r.get("commands", []))
        if a.judge:
            res.rubric = judge(backend, checks, repo, instr.read_text(), r.get("transcript", []))
        (work / "result.json").write_text(json.dumps({**asdict(res), "recipient_raw": r}, indent=2, default=str))
    except Exception as e:  # noqa: BLE001 - we want the table to show failures, not crash
        res.error = f"{type(e).__name__}: {e}"
    res.seconds = round(time.time() - t0, 1)
    return res


def table(results: list[Result]) -> str:
    lines = ["| scenario | arm | runs | pass | prefs ok | rubric ok | turns | tool calls | prompt chars | errors |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    keys = sorted({(r.scenario, r.arm) for r in results})
    for sc, arm in keys:
        rs = [r for r in results if r.scenario == sc and r.arm == arm]
        n = len(rs)
        p = sum(r.passed for r in rs)
        prefs = [v for r in rs for v in r.prefs.values()]
        rub = [v for r in rs for v in r.rubric.values()]
        turns = sum(r.turns for r in rs) / n
        tc = sum(r.tool_calls for r in rs) / n
        pc = sum(r.prompt_chars for r in rs) / n
        errs = sum(1 for r in rs if r.error)
        lines.append(f"| {sc} | {arm} | {n} | {p}/{n} | "
                     f"{sum(prefs)}/{len(prefs) or '-'} | {sum(rub)}/{len(rub) or '-'} | "
                     f"{turns:.1f} | {tc:.1f} | {pc:,.0f} | {errs} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipient", default="fake", help="fake | agent | cmd:<command>")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--scenarios", default="", help="comma list of scenario ids (default all)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--to", help="recipient profile to use (default per scenario, usually codex)")
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--judge", action="store_true", help="grade the rubric with the backend model")
    ap.add_argument("--out", default=str(ROOT / "eval" / "out"))
    a = ap.parse_args(argv)

    try:
        backend = Backend.from_env()
    except BackendError as e:
        print(f"backend config error: {e}", file=sys.stderr)
        return 2
    arms = [x for x in a.arms.split(",") if x]
    if "C_adapted" in arms and backend is None:
        print("no backend configured; skipping arm C_adapted (set HANDOFF_BASE_URL and HANDOFF_MODEL)",
              file=sys.stderr)
        arms = [x for x in arms if x != "C_adapted"]
    scenarios = sorted(p for p in SCEN.iterdir() if p.is_dir() and (p / "checks.json").exists())
    if a.scenarios:
        want = set(a.scenarios.split(","))
        scenarios = [s for s in scenarios if s.name in want]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(a.out) / stamp
    results: list[Result] = []
    for sc in scenarios:
        for arm in arms:
            for i in range(a.runs):
                r = one(sc, arm, i, a, backend, out_root)
                results.append(r)
                flag = "PASS" if r.passed else "FAIL"
                print(f"{sc.name:18s} {arm:10s} run{i} {flag} prefs={r.prefs} {r.seconds}s"
                      + (f" ERROR {r.error}" if r.error else ""), file=sys.stderr)
    md = (f"# handoff eval {stamp}\n\nrecipient: `{a.recipient}`; backend: "
          f"`{backend.model}@{backend.host}` \n\n" if backend else
          f"# handoff eval {stamp}\n\nrecipient: `{a.recipient}`; backend: none\n\n") + table(results) + "\n"
    (out_root / "results.json").write_text(json.dumps([asdict(r) for r in results], indent=2))
    (out_root / "results.md").write_text(md)
    print(md)
    print(f"details: {out_root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
