"""Tables from results: handoff content, outcomes, per scenario, and paired deltas vs a baseline."""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from statistics import mean, stdev

from .runner import Result


def _ar(r: Result, k: str):
    return (r.agent_run or {}).get(k)


def _total_input(r: Result) -> int | None:
    parts = [_ar(r, k) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens")]
    return None if all(p is None for p in parts) else sum(p or 0 for p in parts)


def _recall_frac(r: Result, cat: str | None = None) -> float | None:
    cats = [cat] if cat else ["facts", "corrections", "dead_ends"]
    vals = [v for c in cats for v in (r.recall.get(c) or {}).values()]
    return sum(vals) / len(vals) if vals else None


def _fmt(xs: list, pct: bool = False, digits: int = 0, money: bool = False) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "n/a"
    m = mean(xs)
    se = stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else None
    if pct:
        s = f"{m * 100:.0f}%" + (f" ±{se * 100:.0f}" if se else "")
    elif money:
        s = f"${m:.3f}" + (f" ±{se:.3f}" if se else "")
    else:
        s = f"{m:,.{digits}f}" + (f" ±{se:,.{digits}f}" if se else "")
    return s


def content_table(rows: list[Result]) -> str:
    """What each variant hands over, independent of any agent."""
    by: dict[str, list[Result]] = defaultdict(list)
    seen = set()
    for r in rows:
        k = (r.scenario, r.source, r.variant)
        if k in seen:
            continue  # one handoff per scenario/source/variant; repeats are identical for --no-llm
        seen.add(k)
        by[r.variant].append(r)
    out = ["| variant | n | chars | est. tokens | facts | corrections | dead ends | all | build s |",
           "|---|---|---|---|---|---|---|---|---|"]
    for v in sorted(by):
        rs = by[v]
        out.append(f"| {v} | {len(rs)} | {_fmt([r.handoff_chars for r in rs])} | "
                   f"{_fmt([r.handoff_tokens_est for r in rs])} | "
                   f"{_fmt([_recall_frac(r, 'facts') for r in rs], pct=True)} | "
                   f"{_fmt([_recall_frac(r, 'corrections') for r in rs], pct=True)} | "
                   f"{_fmt([_recall_frac(r, 'dead_ends') for r in rs], pct=True)} | "
                   f"{_fmt([_recall_frac(r) for r in rs], pct=True)} | "
                   f"{_fmt([r.build_seconds for r in rs], digits=1)} |")
    return "\n".join(out)


def outcome_table(rows: list[Result]) -> str:
    by: dict[tuple, list[Result]] = defaultdict(list)
    for r in rows:
        by[(r.agent, r.variant)].append(r)
    out = ["| agent | variant | runs | pass 1st | pass | nudges | questions | prefs ok | redo | turns | "
           "tool calls | input tok | output tok | peak ctx | agent s | cost | cost/pass | errors |",
           "|" + "---|" * 18]
    for (agent, v) in sorted(by):
        rs = by[(agent, v)]
        prefs = [sum(r.prefs.values()) / len(r.prefs) for r in rs if r.prefs]
        costs = [_ar(r, "cost_usd") for r in rs]
        known = [c for c in costs if c is not None]
        passes = sum(r.passed for r in rs)
        cpp = f"${sum(known) / passes:.3f}" if known and passes else "n/a"
        errs = sum(1 for r in rs if r.error)
        tos = sum(1 for r in rs if _ar(r, "timed_out"))
        out.append(
            f"| {agent} | {v} | {len(rs)} | {_fmt([float(r.passed_first) for r in rs], pct=True)} | "
            f"{_fmt([float(r.passed) for r in rs], pct=True)} | {_fmt([r.nudges for r in rs], digits=1)} | "
            f"{_fmt([_ar(r, 'questions') for r in rs], digits=1)} | {_fmt(prefs, pct=True)} | "
            f"{_fmt([r.redo for r in rs], digits=1)} | {_fmt([_ar(r, 'turns') for r in rs], digits=1)} | "
            f"{_fmt([_ar(r, 'tool_calls') for r in rs], digits=1)} | {_fmt([_total_input(r) for r in rs])} | "
            f"{_fmt([_ar(r, 'output_tokens') for r in rs])} | {_fmt([_ar(r, 'peak_context_tokens') for r in rs])} | "
            f"{_fmt([_ar(r, 'seconds') for r in rs], digits=0)} | {_fmt(costs, money=True)} | {cpp} | "
            f"{errs}{f' ({tos} timeout)' if tos else ''} |")
    return "\n".join(out)


def scenario_table(rows: list[Result]) -> str:
    variants = sorted({r.variant for r in rows})
    cells: dict[tuple, list[Result]] = defaultdict(list)
    for r in rows:
        cells[(r.scenario, r.source, r.agent, r.variant)].append(r)
    keys = sorted({(r.scenario, r.source, r.agent) for r in rows})
    out = ["| scenario | source -> agent | " + " | ".join(variants) + " |",
           "|---|---|" + "---|" * len(variants)]
    for sc, src, agent in keys:
        row = []
        for v in variants:
            rs = cells.get((sc, src, agent, v), [])
            row.append(f"{sum(r.passed for r in rs)}/{len(rs)}" if rs else "-")
        out.append(f"| {sc} | {src} -> {agent} | " + " | ".join(row) + " |")
    return "\n".join(out)


def paired_table(rows: list[Result], baseline: str) -> str:
    """Each variant against the baseline on the same scenario, source, agent and run index."""
    idx = {(r.scenario, r.source, r.agent, r.run): r for r in rows if r.variant == baseline}
    out = ["| variant | pairs | Δ pass (pp) | Δ total input tok | Δ output tok | Δ agent s | Δ nudges | Δ questions |",
           "|---|---|---|---|---|---|---|---|"]
    for v in sorted({r.variant for r in rows} - {baseline}):
        pairs = [(r, idx[(r.scenario, r.source, r.agent, r.run)]) for r in rows
                 if r.variant == v and (r.scenario, r.source, r.agent, r.run) in idx]
        if not pairs:
            continue

        def d(f, pairs=pairs):
            return [f(a) - f(b) for a, b in pairs if f(a) is not None and f(b) is not None]

        out.append(f"| {v} | {len(pairs)} | {_fmt([x * 100 for x in d(lambda r: float(r.passed))], digits=0)} | "
                   f"{_fmt(d(_total_input))} | {_fmt(d(lambda r: _ar(r, 'output_tokens')))} | "
                   f"{_fmt(d(lambda r: _ar(r, 'seconds')), digits=0)} | {_fmt(d(lambda r: r.nudges), digits=1)} | "
                   f"{_fmt(d(lambda r: _ar(r, 'questions')), digits=1)} |")
    return "\n".join(out)


def markdown(rows: list[Result], baseline: str = "none", title: str = "handoff eval") -> str:
    parts = [f"# {title}\n", f"{len(rows)} runs. Mean ± standard error where n > 1; n/a = not observable "
             "for that agent.\n", "## Handoff content (no model involved)\n", content_table(rows)]
    if any(r.agent != "-" for r in rows):
        parts += ["\n## Outcomes\n", outcome_table(rows), "\n## Pass count per scenario\n", scenario_table(rows)]
        if any(r.variant == baseline for r in rows):
            parts += [f"\n## Paired against `{baseline}`\n", paired_table(rows, baseline)]
    return "\n".join(parts) + "\n"


def to_csv(rows: list[Result]) -> str:
    cols = ["scenario", "source", "agent", "variant", "run", "profile", "handoff_chars", "handoff_tokens_est",
            "build_seconds", "recall_facts", "recall_corrections", "recall_dead_ends", "passed_first", "passed",
            "nudges", "prefs_ok", "redo", "turns", "tool_calls", "input_tokens", "cache_read_tokens",
            "cache_write_tokens", "output_tokens", "peak_context_tokens", "agent_seconds", "cost_usd",
            "questions", "blocked_actions", "timed_out", "error"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.scenario, r.source, r.agent, r.variant, r.run, r.profile, r.handoff_chars,
                    r.handoff_tokens_est, r.build_seconds, _recall_frac(r, "facts"),
                    _recall_frac(r, "corrections"), _recall_frac(r, "dead_ends"), r.passed_first, r.passed,
                    r.nudges, (sum(r.prefs.values()) / len(r.prefs)) if r.prefs else None, r.redo,
                    _ar(r, "turns"), _ar(r, "tool_calls"), _ar(r, "input_tokens"), _ar(r, "cache_read_tokens"),
                    _ar(r, "cache_write_tokens"), _ar(r, "output_tokens"), _ar(r, "peak_context_tokens"),
                    _ar(r, "seconds"), _ar(r, "cost_usd"), _ar(r, "questions"), _ar(r, "blocked_actions"),
                    _ar(r, "timed_out"), r.error])
    return buf.getvalue()
