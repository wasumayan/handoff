"""One eval cell: scenario x source x agent x variant x run."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .agents import NUDGE, Agent, AgentRun, merge, python_for_tests
from .scenario import (
    ROOT,
    Scenario,
    git,
    handoff_recall,
    pref_checks,
    prepare,
    redo_count,
    run_hidden_tests,
)
from .variants import Variant, build

CROSS = {"claude_code": "codex", "codex": "claude"}  # source agent -> profile of the other agent
SOURCE_AGENT = {"claude_code": "claude", "codex": "codex"}
TOKENS_PER_CHAR = 0.25  # rough English/code estimate; exact counts depend on the model's tokenizer


def profile_for(agent: Agent, source: str) -> str:
    return agent.profile or CROSS[source]


def task_prompt(sc: Scenario, instruction_file: str) -> str:
    """Identical for every variant, so only the installed handoff differs between them."""
    return (f"Continue this task in the current repository. A previous session already worked on it; "
            f"any notes it left are in {instruction_file}. Finish the task and make sure the project's "
            f"tests pass.\n\nTask:\n{sc.task}\n")


@dataclass
class Result:
    scenario: str
    source: str
    agent: str
    variant: str
    run: int
    profile: str = ""
    handoff_chars: int = 0
    handoff_tokens_est: int = 0
    build_seconds: float = 0.0
    recall: dict = field(default_factory=dict)
    passed_first: bool = False
    passed: bool = False
    nudges: int = 0
    prefs: dict = field(default_factory=dict)
    redo: int = 0
    rubric: dict = field(default_factory=dict)
    agent_run: dict = field(default_factory=dict)
    leaked: bool = False  # the agent's commands, reads or transcript mention the eval checkout
    test_output: str = ""
    error: str = ""

    @property
    def key(self) -> tuple:
        return (self.scenario, self.source, self.agent, self.variant, self.run)


def cell_dir(out: Path, sc: str, source: str, agent: str, variant: str, run: int) -> Path:
    return out / sc / source / agent / variant / f"run{run}"


def recall_only(sc: Scenario, source: str, v: Variant, profile: str, backend_env: dict | None) -> Result:
    """Build the handoff and score its content; no agent. Model-free unless the variant needs one."""
    res = Result(sc.id, source, "-", v.name, 0, profile=profile)
    work = prepare(sc, source)
    try:
        built = build(v, sc, work, source, profile, backend_env)
        res.error = built.error
        res.handoff_chars = len(built.text)
        res.handoff_tokens_est = round(len(built.text) * TOKENS_PER_CHAR)
        res.build_seconds = built.seconds
        res.recall = handoff_recall(built.text, sc.checks)
    finally:
        work.cleanup()
    return res


def run_cell(sc: Scenario, source: str, agent: Agent, v: Variant, run: int, out: Path, *,
             max_nudges: int = 2, backend_env: dict | None = None, judge=None) -> Result:
    profile = profile_for(agent, source)
    res = Result(sc.id, source, agent.name, v.name, run, profile=profile)
    dest = cell_dir(out, sc.id, source, agent.name, v.name, run)
    dest.mkdir(parents=True, exist_ok=True)
    work = prepare(sc, source)
    try:
        built = build(v, sc, work, source, profile, backend_env)
        work.drop_source()  # the agent must not find the transcript next to its repo
        for p in work.root.glob("handoff-out-*"):
            shutil.rmtree(p, ignore_errors=True)
        (dest / "handoff.md").write_text(built.text)
        res.handoff_chars = len(built.text)
        res.handoff_tokens_est = round(len(built.text) * TOKENS_PER_CHAR)
        res.build_seconds = built.seconds
        res.recall = handoff_recall(built.text, sc.checks)
        if built.error:
            raise RuntimeError(built.error)
        from handoff.recipients import get_recipient  # noqa: PLC0415
        instr_name = get_recipient(profile).profile.instruction_file
        prompt = task_prompt(sc, instr_name)
        ar: AgentRun = agent.start(sc, work.repo, work.agent_home, prompt, profile)
        res.passed_first, res.test_output = run_hidden_tests(sc, work.repo, python_for_tests())
        res.passed = res.passed_first
        while (not res.passed or ar.asked) and res.nudges < max_nudges and not ar.timed_out:
            more = agent.resume(sc, work.repo, work.agent_home, ar, NUDGE, profile)
            if more is None:
                break
            ar = merge(ar, more)
            res.nudges += 1
            res.passed, res.test_output = run_hidden_tests(sc, work.repo, python_for_tests())
        res.prefs = pref_checks(work.repo, sc.checks, ar.commands)
        res.redo = redo_count(sc.checks, ar.commands, ar.files_written)
        res.agent_run = {k: v for k, v in asdict(ar).items() if k not in ("transcript_tail",)}
        trail = "\n".join([*ar.commands, *ar.paths_read, *ar.files_written, ar.transcript_tail])
        res.leaked = str(ROOT) in trail or "eval/scenarios" in trail
        res.error = ar.error
        if judge is not None:
            res.rubric = judge(sc, work.repo, built.text, ar.transcript_tail)
        (dest / "transcript_tail.txt").write_text(ar.transcript_tail)
        git(work.repo, "add", "-A", check=False)
        root = git(work.repo, "rev-list", "--max-parents=0", "HEAD").strip()
        (dest / "final.diff").write_text(git(work.repo, "diff", "--cached", root, check=False))
    except Exception as e:  # noqa: BLE001 - a failed cell is a row in the table, not a crash
        res.error = f"{type(e).__name__}: {e}"
    finally:
        work.cleanup()
    (dest / "result.json").write_text(json.dumps(asdict(res), indent=2, default=str))
    return res


def load_results(out: Path) -> list[Result]:
    rows = []
    for p in sorted(out.rglob("result.json")):
        rows.append(Result(**json.loads(p.read_text())))
    return rows
