"""The eval framework: scenarios, variants, isolation, agent output parsers, nudges, report."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from harness import report  # noqa: E402
from harness.agents import AgentRun, FakeAgent, merge, parse_claude_stream, parse_codex_events  # noqa: E402
from harness.runner import Result, load_results, recall_only, run_cell  # noqa: E402
from harness.scenario import SCENARIOS, load_scenarios, prepare  # noqa: E402
from harness.variants import Variant, build, load_variants  # noqa: E402

from tests.test_eval_harness import Scripted  # noqa: E402

AGENT_FIXTURES = ROOT / "tests" / "fixtures" / "agents"
ALL = load_scenarios()
CELLS = [(sc, src) for sc in ALL for src in sc.sources]


def _ids(cells):
    return [f"{sc.id}-{src}" for sc, src in cells]


# ---------------------------------------------------------------- scenarios


@pytest.mark.parametrize("sc", ALL, ids=[s.id for s in ALL])
def test_scenario_midpoint_fails_and_solution_passes(sc):
    cmd = [sys.executable if c == "python" else c for c in sc.checks["test_cmd"]]
    with tempfile.TemporaryDirectory() as t:
        shutil.copytree(sc.dir / "repo", t, dirs_exist_ok=True)
        shutil.copytree(sc.dir / "hidden", Path(t) / "hidden")
        env = {**os.environ, "PYTHONPATH": t}
        assert subprocess.run(cmd, cwd=t, capture_output=True, env=env).returncode != 0
        shutil.copytree(sc.dir / "solution", t, dirs_exist_ok=True)
        r = subprocess.run(cmd, cwd=t, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stdout[-2000:]


def test_committed_sessions_match_the_generator():
    spec = importlib.util.spec_from_file_location("build_sessions", SCENARIOS / "build_sessions.py")
    gen = importlib.util.module_from_spec(spec)
    sys.modules["build_sessions"] = gen  # dataclasses look their module up here
    spec.loader.exec_module(gen)
    for make in gen.SCRIPTS:
        s = make()
        for name, recs in (("claude_code.jsonl", gen.emit_claude(s)), ("codex.jsonl", gen.emit_codex(s))):
            want = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
            assert (SCENARIOS / s.name / "source" / name).read_text() == want, (
                f"{s.name}/{name} is stale: run python eval/scenarios/build_sessions.py")


@pytest.mark.parametrize("sc", ALL, ids=[s.id for s in ALL])
def test_checks_are_well_formed(sc):
    c = sc.checks
    for cat in ("facts", "corrections", "dead_ends"):
        assert c["handoff_checks"][cat], f"{sc.id} has no {cat}: the scenario cannot discriminate"
        for item in c["handoff_checks"][cat]:
            re.compile(item["pattern"])
    for item in c.get("redo_patterns", []):
        re.compile(item["pattern"])
        assert item.get("on") in ("commands", "files")
    assert sc.ideal and sc.sources == ["claude_code", "codex"]


# ---------------------------------------------------------------- variants and recall


@pytest.mark.parametrize("sc,src", CELLS, ids=_ids(CELLS))
def test_recall_bounds(sc, src):
    v = {x.name: x for x in load_variants()}
    profile = "codex" if src == "claude_code" else "claude"
    none = recall_only(sc, src, v["none"], profile, None)
    assert none.handoff_chars == 0 and not any(any(c.values()) for c in none.recall.values())
    for name in ("ideal", "raw", "dump"):
        r = recall_only(sc, src, v[name], profile, None)
        assert not r.error, r.error
        assert all(all(c.values()) for c in r.recall.values()), (name, r.recall)


def test_compaction_keeps_user_corrections_at_tight_budgets():
    sc = load_scenarios(["04"])[0]
    tight = Variant("tight", "handoff", ["--no-llm", "--budget", "2000"])
    for src, profile in (("claude_code", "codex"), ("codex", "claude")):
        r = recall_only(sc, src, tight, profile, None)
        assert all(r.recall["corrections"].values()), r.recall
        assert r.handoff_chars < recall_only(sc, src, load_variants(only=["dump"])[0], profile, None).handoff_chars


def test_adapted_variant_calls_the_backend():
    composed = {"current_state": "flag parsed, not wired", "decisions": [], "open_problems": [],
                "next_steps": ["add FileHandler for debug.log"], "gotchas": ["basicConfig is a no-op here"],
                "preferences_observed": []}
    srv = Scripted([{"content": json.dumps(composed)}] * 2)
    sc = load_scenarios(["01"])[0]
    try:
        v = Variant("adapted", "handoff", [], needs_backend=True)
        env = {"HANDOFF_BASE_URL": srv.base_url, "HANDOFF_MODEL": "m", "HANDOFF_API_KEY": ""}
        r = recall_only(sc, "claude_code", v, "codex", env)
    finally:
        srv.close()
    assert not r.error and srv.requests
    assert all(r.recall["corrections"].values())


def test_variant_needing_backend_without_one_is_an_error():
    sc = load_scenarios(["01"])[0]
    w = prepare(sc, "claude_code")
    try:
        b = build(Variant("adapted", "handoff", [], needs_backend=True), sc, w, "claude_code", "codex", None)
    finally:
        w.cleanup()
    assert b.error and not b.text


# ---------------------------------------------------------------- isolation and the run loop


class Probe(FakeAgent):
    """Records what the agent could see, then behaves like the fake agent."""

    name = "probe"
    seen: dict = {}

    def start(self, sc, repo, home, prompt, profile):
        work = repo.parent
        Probe.seen = {
            "outside_repo_root": ROOT not in repo.resolve().parents,
            "source_gone": not (work / "source-home").exists(),
            "no_handoff_out": not list(work.glob("handoff-out-*")),
            "no_hidden": not (repo / "hidden").exists(),
            "no_solution": not any(p.name == "solution" for p in work.rglob("*")),
            "no_dot_handoff": not (repo / ".handoff").exists(),
            "instruction": (repo / "AGENTS.md").read_text() if (repo / "AGENTS.md").exists() else "",
            "prompt": prompt,
        }
        return super().start(sc, repo, home, prompt, profile)


def test_run_cell_isolates_the_agent(tmp_path):
    sc = load_scenarios(["05"])[0]
    dump = load_variants(only=["dump"])[0]
    r = run_cell(sc, "claude_code", Probe(), dump, 0, tmp_path)
    s = Probe.seen
    assert r.passed and not r.error, r.error
    assert all(s[k] for k in ("outside_repo_root", "source_gone", "no_handoff_out", "no_hidden",
                              "no_solution", "no_dot_handoff")), s
    assert "<!-- handoff:begin -->" in s["instruction"] and sc.task.split()[0] in s["prompt"]
    assert all(r.prefs.values()) and r.redo == 0
    saved = tmp_path / sc.id / "claude_code" / "probe" / "dump" / "run0"
    assert (saved / "handoff.md").read_text() and (saved / "final.diff").read_text()
    assert load_results(tmp_path)[0].key == r.key


class Lazy(FakeAgent):
    """Asks a question and does nothing; finishes only when nudged."""

    name = "lazy"

    def start(self, sc, repo, home, prompt, profile):
        return AgentRun(invocations=1, turns=1, final_message="Should I also update the tests?",
                        session_id="s1", input_tokens=100, output_tokens=10, questions=1)

    def resume(self, sc, repo, home, prev, text, profile):
        assert "No further input" in text
        done = super().start(sc, repo, home, text, profile)
        done.input_tokens, done.output_tokens = 50, 5
        return done


def test_nudges_count_as_manual_input(tmp_path):
    sc = load_scenarios(["02"])[0]
    r = run_cell(sc, "codex", Lazy(), load_variants(only=["none"])[0], 0, tmp_path, max_nudges=2)
    assert not r.passed_first and r.passed and r.nudges == 1
    assert r.agent_run["questions"] == 1 and r.agent_run["input_tokens"] == 150
    assert r.agent_run["invocations"] == 2


def test_merge_keeps_unknowns_unknown():
    a = AgentRun(input_tokens=None, peak_context_tokens=10, commands=["x"], cost_usd=None)
    b = AgentRun(input_tokens=None, peak_context_tokens=30, commands=["y"], cost_usd=None, final_message="z")
    m = merge(a, b)
    assert m.input_tokens is None and m.cost_usd is None and m.peak_context_tokens == 30
    assert m.commands == ["x", "y"] and m.final_message == "z"


# ---------------------------------------------------------------- agent output parsers (real captures)


def test_parse_claude_stream_fixture():
    r = parse_claude_stream((AGENT_FIXTURES / "claude_stream.jsonl").read_text())
    assert r.session_id == "sess-1" and r.turns == 3 and r.tool_calls == 2
    assert (r.input_tokens, r.cache_read_tokens, r.cache_write_tokens, r.output_tokens) == (4, 29284, 6744, 207)
    assert r.cost_usd == pytest.approx(0.0639648)
    # two tool calls in one API message share usage; the peak is the largest single call
    assert r.peak_context_tokens == 2 + 17902 + 224
    assert r.commands == ["ls"] and "hello from the probe" in r.final_message
    assert r.questions == 0 and r.blocked_actions == 0


def test_parse_codex_events_fixture():
    r = parse_codex_events((AGENT_FIXTURES / "codex_exec.jsonl").read_text())
    assert r.session_id.startswith("0190aaaa") and r.turns == 1 and r.tool_calls == 2
    assert r.input_tokens == 22888 - 11330 and r.cache_read_tokens == 11330 and r.output_tokens == 197
    assert r.peak_context_tokens is None and r.cost_usd is None  # not observable from codex exec
    assert r.final_message == "hello from the probe" and r.commands[1].endswith("ls")


def test_question_at_the_end_counts():
    text = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Which file?"}})
    assert parse_codex_events(text).questions == 1


# ---------------------------------------------------------------- report


def _row(variant, passed, inp, run=0, scenario="s"):
    return Result(scenario, "claude_code", "claude", variant, run, handoff_chars=100 if variant != "none" else 0,
                  recall={"facts": {"a": variant != "none"}, "corrections": {}, "dead_ends": {}},
                  passed_first=passed, passed=passed, prefs={"p": True},
                  agent_run={"input_tokens": inp, "output_tokens": 10, "seconds": 5.0, "cost_usd": 0.5,
                             "questions": 0, "turns": 3, "tool_calls": 4})


def test_report_pairs_against_baseline():
    rows = [_row("none", False, 1000, 0), _row("none", True, 1200, 1),
            _row("dump", True, 600, 0), _row("dump", True, 700, 1)]
    md = report.markdown(rows, baseline="none")
    assert "## Paired against `none`" in md
    line = next(ln for ln in md.splitlines() if ln.startswith("| dump | 2 |"))
    assert "50" in line and "-450" in line  # +50pp pass, -450 input tokens on average
    assert "| claude | dump | 2 | 100% | 100% |" in md
    assert report.to_csv(rows).count("\n") == 5
