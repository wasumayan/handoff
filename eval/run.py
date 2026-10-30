"""Evaluate handoff variants: how much of the previous session does each one carry, and how well does
the next agent finish the job with it?

    python eval/run.py doctor                    # check prerequisites (add --live to test agent auth)
    python eval/run.py score                     # handoff content per variant; no model, seconds
    python eval/run.py plan  --agent claude      # what a run would do, before spending anything
    python eval/run.py run   --agent claude --runs 3 --out eval/out/claude-1
    python eval/run.py report eval/out/claude-1  # rebuild tables from saved results

See eval/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from harness import report  # noqa: E402
from harness.agents import make_agent  # noqa: E402
from harness.runner import (  # noqa: E402
    CROSS,
    SOURCE_AGENT,
    Result,
    cell_dir,
    load_results,
    recall_only,
    run_cell,
)
from harness.scenario import git, load_scenarios  # noqa: E402
from harness.variants import load_variants  # noqa: E402

from handoff.backend import Backend, BackendError  # noqa: E402

AGENTS = ("fake", "standin", "claude", "codex")


def _split(s: str | None) -> list[str] | None:
    return [x for x in s.split(",") if x] if s else None


def _backend() -> Backend | None:
    try:
        return Backend.from_env()
    except BackendError as e:
        raise SystemExit(f"backend config error: {e}") from e


def _backend_env(b: Backend | None) -> dict | None:
    return {"HANDOFF_BASE_URL": b.base_url, "HANDOFF_MODEL": b.model, "HANDOFF_API_KEY": b.api_key} if b else None


def _sources(agent: str, wanted: list[str] | None, available: list[str]) -> list[str]:
    """Default pairing is cross-agent: a Claude Code session goes to Codex and vice versa. Same-agent
    pairs measure little, because the recipient reads the source's instruction file natively."""
    if wanted:
        return [s for s in wanted if s in available]
    if agent in ("claude", "codex"):
        return [s for s in available if SOURCE_AGENT[s] != agent]
    return available


def _matrix(a) -> list[tuple]:
    scenarios = load_scenarios(_split(a.scenarios))
    variants = load_variants(only=_split(a.variants))
    backend = _backend()
    if backend is None:
        skipped = [v.name for v in variants if v.needs_backend]
        if skipped:
            print(f"no backend configured; skipping {', '.join(skipped)} "
                  "(set HANDOFF_BASE_URL and HANDOFF_MODEL)", file=sys.stderr)
        variants = [v for v in variants if not v.needs_backend]
    agent = getattr(a, "agent", None) or "-"
    cells = []
    for sc in scenarios:
        for src in _sources(agent, _split(a.sources), sc.sources):
            for v in variants:
                for i in range(getattr(a, "runs", 1)):
                    cells.append((sc, src, v, i))
    return cells, backend


def cmd_plan(a) -> int:
    cells, backend = _matrix(a)
    out = Path(a.out) if a.out else None
    todo = [c for c in cells if not (out and (cell_dir(out, c[0].id, c[1], a.agent, c[2].name, c[3]) / "result.json").is_file())]
    print(f"agent: {a.agent}   backend: {backend.model + '@' + backend.host if backend else 'none'}")
    print(f"scenarios x sources x variants x runs = {len(cells)} runs ({len(cells) - len(todo)} already done in {out})"
          if out else f"scenarios x sources x variants x runs = {len(cells)} runs")
    seen = set()
    for sc, src, v, _ in cells:
        if (sc.id, src, v.name) in seen:
            continue
        seen.add((sc.id, src, v.name))
        to = CROSS[src] if a.agent in ("fake", "standin") else a.agent
        print(f"  {sc.id:20s} {src:12s} -> {to:7s} {v.name:10s} {v.description}")
    print(f"\nEach run makes 1 agent call plus up to {a.max_nudges} nudges. Measure the real cost per run with "
          f"`run --runs 1 --scenarios 01` before a full run; the report shows it.")
    return 0


def cmd_score(a) -> int:
    cells, backend = _matrix(a)
    rows: list[Result] = []
    for sc, src, v, i in cells:
        if i:
            continue
        r = recall_only(sc, src, v, CROSS[src], _backend_env(backend))
        rows.append(r)
        print(f"{sc.id:20s} {src:12s} {v.name:10s} {r.handoff_chars:>7,} chars  recall "
              f"{json.dumps({k: sum(x.values()) for k, x in r.recall.items()})}"
              + (f"  ERROR {r.error}" if r.error else ""), file=sys.stderr)
    md = report.markdown(rows, title="handoff content score")
    print(md)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "score.md").write_text(md)
        (out / "score.csv").write_text(report.to_csv(rows))
    return 0


def _judge(backend: Backend | None):
    if backend is None:
        return None

    def judge(sc, repo, handoff_text, transcript_tail):
        qs = sc.checks.get("rubric", [])
        if not qs:
            return {}
        root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        git(repo, "add", "-A", check=False)
        diff = git(repo, "diff", "--cached", root, check=False)[:12000]
        prompt = ("You grade a coding agent that continued someone else's work. Answer each question "
                  "true or false from the evidence only. Reply with a JSON object mapping the question "
                  "index (\"0\", \"1\", ...) to a boolean.\n\n## Handoff the agent was given\n"
                  + handoff_text[:8000] + "\n\n## End of the agent's transcript\n" + transcript_tail[-6000:]
                  + "\n\n## Final diff\n" + diff + "\n\n## Questions\n"
                  + "\n".join(f"{i}. {q}" for i, q in enumerate(qs)))
        try:
            raw = backend.chat([{"role": "user", "content": prompt}], json_mode=True, temperature=0)
            obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except (BackendError, ValueError):
            return {}
        return {q: bool(obj.get(str(i), False)) for i, q in enumerate(qs)}

    return judge


def cmd_run(a) -> int:
    cells, backend = _matrix(a)
    agent = make_agent(a.agent, backend=backend, model=a.model, isolate=not a.no_isolate,
                       timeout=a.timeout, max_turns=a.max_turns, extra=a.agent_arg,
                       max_budget_usd=a.max_budget_usd)
    out = Path(a.out or ROOT / "eval" / "out" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps({k: v for k, v in vars(a).items() if k != "fn"}, indent=2))
    judge = _judge(backend) if a.judge else None
    for n, (sc, src, v, i) in enumerate(cells, 1):
        done = cell_dir(out, sc.id, src, agent.name, v.name, i) / "result.json"
        if done.is_file() and not a.rerun:
            continue
        r = run_cell(sc, src, agent, v, i, out, max_nudges=a.max_nudges, backend_env=_backend_env(backend),
                     judge=judge)
        ar = r.agent_run or {}
        print(f"[{n}/{len(cells)}] {sc.id:20s} {src:12s} {v.name:10s} run{i} "
              f"{'PASS' if r.passed else 'FAIL'}{'' if r.passed == r.passed_first else ' (after nudge)'} "
              f"nudges={r.nudges} q={ar.get('questions')} {ar.get('seconds')}s "
              f"cost={ar.get('cost_usd')}" + (f"  ERROR {r.error}" if r.error else ""), file=sys.stderr)
    return cmd_report(argparse.Namespace(out=str(out), baseline=a.baseline))


def cmd_report(a) -> int:
    out = Path(a.out)
    rows = load_results(out)
    if not rows:
        raise SystemExit(f"no results under {out}")
    md = report.markdown(rows, baseline=a.baseline, title=f"handoff eval: {out.name}")
    (out / "results.md").write_text(md)
    (out / "results.csv").write_text(report.to_csv(rows))
    print(md)
    print(f"written: {out / 'results.md'}, {out / 'results.csv'}", file=sys.stderr)
    return 0


def cmd_doctor(a) -> int:
    ok = True

    def line(good: bool, what: str, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else '!!'}] {what}" + (f"  -> {hint}" if hint and not good else ""))

    print("prerequisites")
    line(sys.version_info >= (3, 11), f"python {sys.version.split()[0]}", "needs 3.11+")
    line(shutil.which("git") is not None, "git", "install git")
    for name, key in (("claude", "ANTHROPIC_API_KEY"), ("codex", "OPENAI_API_KEY")):
        path = shutil.which(name)
        ver = subprocess.run([name, "--version"], capture_output=True, text=True).stdout.strip() if path else ""
        line(bool(path), f"{name} CLI {ver}".strip(), f"install it to use --agent {name}")
        line(bool(os.environ.get(key)), f"{key} set",
             f"needed for --agent {name} with isolation (default); or pass --no-isolate to use your login")
    b = None
    try:
        b = Backend.from_env()
    except BackendError as e:
        line(False, f"backend: {e}")
    line(b is not None, f"backend for `adapted`, --judge and --agent standin: {b.model + '@' + b.host if b else 'not set'}",
         "set HANDOFF_BASE_URL, HANDOFF_MODEL, HANDOFF_API_KEY (any OpenAI-compatible endpoint)")
    if a.live:
        print("live checks (each makes one tiny paid call)")
        import tempfile  # noqa: PLC0415
        for name in ("claude", "codex"):
            if not shutil.which(name):
                continue
            with tempfile.TemporaryDirectory() as t:
                agent = make_agent(name, isolate=not a.no_isolate, timeout=180)
                home = Path(t) / "home"
                home.mkdir()
                r = agent.start(None, Path(t), home, "Reply with the single word: ready", "")
                good = "ready" in r.final_message.lower()
                line(good, f"{name} answers headless{' (isolated)' if not a.no_isolate else ''}: "
                     f"{r.final_message[:40]!r}", r.error or r.transcript_tail[-300:])
    print("\nall good" if ok else "\nsome checks failed (only the ones you need matter)")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, runs=True):
        p.add_argument("--scenarios", help="comma list of scenario ids or numbers (default all)")
        p.add_argument("--variants", help="comma list from eval/variants.toml (default all)")
        p.add_argument("--sources", help="claude_code,codex (default: cross-agent pairing for the agent)")
        if runs:
            p.add_argument("--runs", type=int, default=1, help="repeats per cell (variance!)")

    p = sub.add_parser("score", help="handoff content per variant; no agent, no model")
    common(p, runs=False)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_score)

    for name, fn in (("plan", cmd_plan), ("run", cmd_run)):
        p = sub.add_parser(name)
        common(p)
        p.add_argument("--agent", choices=AGENTS, required=True)
        p.add_argument("--out", help="results dir; re-running with the same --out resumes")
        p.add_argument("--max-nudges", type=int, default=2,
                       help="times to resume a run that ends with failing tests or a question")
        p.add_argument("--model", default="", help="model for the claude/codex agent")
        p.add_argument("--agent-arg", action="append", default=[], help="extra CLI arg for the agent (repeatable)")
        p.add_argument("--timeout", type=int, default=1200, help="seconds per agent invocation")
        p.add_argument("--max-budget-usd", type=float, default=2.0,
                       help="Claude Code spend cap per invocation (0 = none); Codex has no equivalent")
        p.add_argument("--max-turns", type=int, default=30, help="stand-in agent turn cap")
        p.add_argument("--no-isolate", action="store_true", help="use your own claude/codex config and login")
        p.add_argument("--judge", action="store_true", help="grade the rubric with the backend model")
        p.add_argument("--rerun", action="store_true", help="redo cells that already have a result")
        p.add_argument("--baseline", default="none", help="variant to pair against in the report")
        p.set_defaults(fn=fn)

    p = sub.add_parser("report")
    p.add_argument("out")
    p.add_argument("--baseline", default="none")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("doctor")
    p.add_argument("--live", action="store_true", help="make one tiny call per installed agent CLI")
    p.add_argument("--no-isolate", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
