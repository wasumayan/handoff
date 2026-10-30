"""Generate each scenario's midpoint source session, in Claude Code and Codex formats.

A session is written once as an abstract script (user turns, assistant text, read / write / edit /
bash steps) and emitted as:

    source/claude_code.jsonl   Claude Code 2.1.x project JSONL
    source/codex.jsonl         Codex 0.14x rollout JSONL (each turn written twice, response_item and
                               event_msg, in the order real sessions use; exec_command calls;
                               apply_patch custom tool calls)

Paths use the placeholder /WORKDIR, which the runner replaces with the real work tree. The project
rules file is CLAUDE.md for Claude Code and AGENTS.md for Codex (the runner renames it to match the
source agent). Output is deterministic: ids and timestamps derive from the scenario name.

These sessions are authored, not recorded. To use a recorded one instead, drop it in place of the
generated file with its cwd rewritten to /WORKDIR, and stop regenerating that scenario.

Run: python eval/scenarios/build_sessions.py
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CWD = "/WORKDIR"
RULES = "CLAUDE.md"  # spelled per agent at emit time


@dataclass
class Step:
    kind: str  # user | say | read | write | edit | bash
    text: str = ""
    path: str = ""
    old: str = ""
    new: str = ""
    output: str = ""
    code: int = 0


@dataclass
class Script:
    name: str
    steps: list[Step] = field(default_factory=list)

    def user(self, text: str) -> None:
        self.steps.append(Step("user", text=text.strip()))

    def say(self, text: str) -> None:
        self.steps.append(Step("say", text=text.strip()))

    def read(self, path: str, content: str | None = None) -> None:
        if content is None:
            content = repo_file(self.name, path)
        self.steps.append(Step("read", path=path, output=content))

    def write(self, path: str, content: str) -> None:
        self.steps.append(Step("write", path=path, new=content))

    def edit(self, path: str, old: str, new: str) -> None:
        self.steps.append(Step("edit", path=path, old=old, new=new))

    def bash(self, command: str, output: str, code: int = 0) -> None:
        self.steps.append(Step("bash", text=command, output=output, code=code))


def repo_file(scenario: str, rel: str) -> str:
    return (HERE / scenario / "repo" / rel).read_text()


class Ids:
    def __init__(self, scenario: str, agent: str):
        self.ns = uuid.uuid5(uuid.NAMESPACE_URL, f"handoff-eval/{scenario}/{agent}")
        self.n = 0
        self.t = 0

    def uuid(self) -> str:
        self.n += 1
        return str(uuid.uuid5(self.ns, str(self.n)))

    def ts(self) -> str:
        self.t += 11
        secs = 9 * 3600 + self.t
        return f"2026-10-27T{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}.000Z"


# ---------------------------------------------------------------------------- Claude Code


def emit_claude(s: Script) -> list[dict]:
    ids = Ids(s.name, "claude")
    sid = ids.uuid()
    base = {"sessionId": sid, "cwd": CWD, "gitBranch": "main", "version": "2.1.311",
            "userType": "external", "isSidechain": False}
    out: list[dict] = []
    parent = None

    def push(rec: dict) -> None:
        nonlocal parent
        u = ids.uuid()
        rec.update(uuid=u, parentUuid=parent, timestamp=ids.ts(), **base)
        out.append(rec)
        parent = u

    def tool(name: str, inp: dict, result: str, is_error: bool = False) -> None:
        tid = "toolu_" + ids.uuid().replace("-", "")[:20]
        push({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        push({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}})

    for st in s.steps:
        if st.kind == "user":
            push({"type": "user", "message": {"role": "user", "content": st.text}})
        elif st.kind == "say":
            push({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": ""}, {"type": "text", "text": st.text}]}})
        elif st.kind == "read":
            numbered = "".join(f"{i:>6}\t{line}\n" for i, line in enumerate(st.output.splitlines(), 1))
            tool("Read", {"file_path": f"{CWD}/{st.path}"}, numbered)
        elif st.kind == "write":
            tool("Write", {"file_path": f"{CWD}/{st.path}", "content": st.new},
                 f"File created successfully at: {CWD}/{st.path}")
        elif st.kind == "edit":
            tool("Edit", {"file_path": f"{CWD}/{st.path}", "old_string": st.old, "new_string": st.new},
                 f"The file {CWD}/{st.path} has been updated.")
        elif st.kind == "bash":
            result = st.output if st.code == 0 else f"Exit code {st.code}\n{st.output}"
            tool("Bash", {"command": st.text, "description": "run"}, result, is_error=st.code != 0)
    return out


# ---------------------------------------------------------------------------- Codex


def _patch(kind: str, path: str, old: str = "", new: str = "") -> str:
    if kind == "add":
        body = "".join(f"+{line}\n" for line in new.splitlines())
        return f"*** Begin Patch\n*** Add File: {path}\n{body}*** End Patch\n"
    body = "".join(f"-{line}\n" for line in old.splitlines()) + "".join(
        f"+{line}\n" for line in new.splitlines())
    return f"*** Begin Patch\n*** Update File: {path}\n@@\n{body}*** End Patch\n"


def emit_codex(s: Script) -> list[dict]:
    ids = Ids(s.name, "codex")
    tid = ids.uuid()
    out: list[dict] = []

    def rec(type_: str, payload: dict) -> None:
        out.append({"timestamp": ids.ts(), "type": type_, "payload": payload})

    def item(payload: dict) -> None:
        rec("response_item", payload)

    def msg(role: str, text: str) -> None:
        kind = "output_text" if role == "assistant" else "input_text"
        item({"type": "message", "role": role, "content": [{"type": kind, "text": text}]})

    def call(name: str, args: dict, output: str) -> None:
        cid = "call_" + ids.uuid().replace("-", "")[:20]
        item({"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid})
        item({"type": "function_call_output", "call_id": cid, "output": output})

    def exec_command(cmd: str, output: str, code: int = 0) -> None:
        tokens = max(1, len(output) // 4)
        call("exec_command", {"cmd": cmd, "workdir": CWD},
             f"Chunk ID: {ids.uuid()[:6]}\nWall time: 0.1000 seconds\nProcess exited with code {code}\n"
             f"Original token count: {tokens}\nOutput:\n{output}")

    def apply_patch(patch: str, path: str, verb: str) -> None:
        cid = "call_" + ids.uuid().replace("-", "")[:20]
        item({"type": "custom_tool_call", "status": "completed", "call_id": cid, "name": "apply_patch",
              "input": patch})
        item({"type": "custom_tool_call_output", "call_id": cid,
              "output": f"Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\n{verb} {path}\n"})

    rec("session_meta", {"id": tid, "timestamp": ids.ts(), "cwd": CWD, "originator": "codex_cli_rs",
                         "cli_version": "0.145.0", "source": "cli", "thread_source": "user"})
    rec("turn_context", {"cwd": CWD, "approval_policy": "on-request", "sandbox_policy": {"type": "workspace-write"}})
    msg("developer", "<permissions instructions>Filesystem sandboxing: workspace-write.</permissions instructions>")
    rules = repo_file(s.name, RULES)
    msg("user", f"# AGENTS.md instructions for {CWD}\n\n<INSTRUCTIONS>\n{rules}</INSTRUCTIONS>")
    for st in s.steps:
        path = "AGENTS.md" if st.path == RULES else st.path
        if st.kind == "user":
            msg("user", st.text)  # human turns: response_item first, then the event copy
            rec("event_msg", {"type": "user_message", "message": st.text})
        elif st.kind == "say":
            rec("event_msg", {"type": "agent_message", "message": st.text})  # assistant: event first
            msg("assistant", st.text)
        elif st.kind == "read":
            exec_command(f"sed -n '1,400p' {path}", st.output)
        elif st.kind == "write":
            apply_patch(_patch("add", path, new=st.new), path, "A")
        elif st.kind == "edit":
            apply_patch(_patch("update", path, old=st.old, new=st.new), path, "M")
        elif st.kind == "bash":
            exec_command(st.text.replace("CLAUDE.md", "AGENTS.md"), st.output, st.code)
    return out


def write(s: Script) -> None:
    out = HERE / s.name / "source"
    out.mkdir(exist_ok=True)
    for name, recs in (("claude_code.jsonl", emit_claude(s)), ("codex.jsonl", emit_codex(s))):
        (out / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))


# ---------------------------------------------------------------------------- scenarios


def task(scenario: str) -> str:
    return (HERE / scenario / "task.md").read_text().strip()


def s01() -> Script:
    s = Script("01_verbose_flag")
    app = repo_file(s.name, "app.py")
    flag = '    ap.add_argument("--verbose", action="store_true", help="enable debug logging")\n'
    s.user(task(s.name))
    s.read(RULES)
    s.read("app.py", app.replace(flag, ""))
    s.read("logsetup.py")
    s.say("Plan: add the flag, raise the log level to DEBUG when it is set, log `verbose mode on` "
          "before the greeting.")
    s.edit("app.py", '    ap.add_argument("name")\n', '    ap.add_argument("name")\n' + flag)
    wired = ("    if args.verbose:\n        logging.basicConfig(level=logging.DEBUG)\n"
             "        log.debug(\"verbose mode on\")\n")
    s.edit("app.py", "    log.info(greet(args.name))\n", wired + "    log.info(greet(args.name))\n")
    s.bash("python app.py bob --verbose", "INFO hello, bob\n")
    s.say("No DEBUG line. `logging.basicConfig(level=logging.DEBUG)` is a no-op here: `logsetup.py` "
          "already attached a handler to the root logger at import, and basicConfig returns without "
          "doing anything when the root logger has handlers. The level has to be set directly with "
          "`logging.getLogger().setLevel(logging.DEBUG)`. Reverting the basicConfig attempt.")
    s.edit("app.py", wired + "    log.info(greet(args.name))\n", "    log.info(greet(args.name))\n")
    s.bash("python -m pytest -q", "1 passed in 0.01s\n")
    s.user("one more thing: when --verbose is on, also write the debug log to a file debug.log in the "
           "current directory. ops tails that file. keep stderr output as it is.")
    s.say("Understood: with --verbose, also add a FileHandler for ./debug.log at DEBUG (in app.py, "
          "not logsetup.py), and keep stderr unchanged. Not implemented yet. State: the flag is parsed, "
          "nothing is wired.")
    s.user("ok, we'll pick this up later")
    return s


def s02() -> Script:
    s = Script("02_percent_bug")
    s.user(task(s.name))
    s.read(RULES)
    s.bash("python -m pytest -q",
           "..F\n=================================== FAILURES ===================================\n"
           "____________________________ test_percent_of_total _____________________________\n"
           "    def test_percent_of_total():\n>       assert percent_of_total(\"25, 75\", 0) == pytest.approx(25.0)\n"
           "E       assert 0 == 25.0 ± 2.5e-05\n\ntests/test_calc.py:15: AssertionError\n"
           "1 failed, 2 passed in 0.03s\n", code=1)
    s.read("calc/api.py")
    s.read("calc/parse.py")
    s.bash("python -c \"from calc.parse import parse_amounts; print(parse_amounts('25, 75'))\"", "[25.0, 75.0]\n")
    s.say("parse_amounts is correct: it returns floats. Ruled out parse.py. Checking stats.")
    s.read("calc/stats.py")
    s.say("Root cause: `calc/stats.py` `percent()` does `int(part) // int(whole) * 100`, an int cast plus "
          "floor division, so 25 // 100 == 0. Fix is `part / whole * 100`. Not applied yet.")
    s.user("before you fix it: don't change percent_of_total's signature, the mobile client calls it "
           "positionally. and round the result to 2 decimals, the dashboard shows 33.33 not 33.3333333.")
    s.say("Noted: keep `percent_of_total(text, index)` as is, and round its result to 2 decimals. "
          "Next: fix stats.percent, round in api.percent_of_total, add a regression test.")
    s.user("ok stop here for now")
    return s


def s03() -> Script:
    s = Script("03_rates_refactor")
    s.user(task(s.name))
    s.read(RULES)
    s.bash("git log --oneline", "1a2b3c4 refactor: extract price formatting into shop/money.py\n9f8e7d6 initial\n")
    s.say("Step 1 is done and committed (shop/money.py, format_price, used from cart.py). Starting step 2.")
    s.read("shop/rates.py")
    s.bash("curl -s http://rates.example/rate/EUR", '{"rate": "1.0842", "as_of": "2026-10-27"}\n')
    s.say("Note: the rates API returns the rate as a JSON string (\"1.0842\"), not a number.")
    attempt = ('import json\nimport urllib.request\n\nRATES_URL = "http://rates.example/rate/{currency}"\n\n\n'
               'class RateError(Exception):\n    pass\n\n\ndef get_rate(currency: str) -> float:\n'
               '    with urllib.request.urlopen(RATES_URL.format(currency=currency), 5) as resp:\n'
               '        return float(json.loads(resp.read())["rate"])\n')
    original = repo_file(s.name, "shop/rates.py")
    s.edit("shop/rates.py", original, attempt)
    s.bash("python -c \"from shop.rates import get_rate; print(get_rate('EUR'))\"",
           "Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n"
           "  File \"/WORKDIR/shop/rates.py\", line 13, in get_rate\n"
           "  File \"/usr/lib/python3.12/urllib/request.py\", line 215, in urlopen\n"
           "TypeError: POST data should be bytes, an iterable of bytes, or a file object. It cannot be of type int.\n",
           code=1)
    s.say("urlopen's second positional parameter is `data`, not the timeout, so passing 5 positionally "
          "made it a POST. Must pass `timeout=5` as a keyword. Reverting rates.py to the original for now.")
    s.edit("shop/rates.py", attempt, original)
    s.user("change of plan for step 2: get_rate must return decimal.Decimal, not float. finance needs "
           "exact values. and RateError should carry the currency code as .currency")
    s.say("Understood: get_rate returns Decimal (build it from the string, Decimal(str(rate))), and "
          "RateError(currency) exposes `.currency`. Step 3's total_in will multiply Decimal cents by the "
          "rate and round half-up to int cents before format_price. Nothing for step 2 is applied yet.")
    s.user("good, continue later")
    return s


def s04() -> Script:
    s = Script("04_report_long")
    csv_text = repo_file(s.name, "data/orders.csv")
    lines = csv_text.splitlines()
    totals: dict[str, float] = defaultdict(float)
    for line in lines[1:]:
        _, cid, _, amount = line.split(";")
        totals[cid] += float(amount.replace(",", "."))
    report_out = "".join(f"{c} {t:.2f}\n" for c, t in totals.items())
    s.user(task(s.name))
    s.read(RULES)
    s.read("report.py")
    s.read("tests/test_report.py")
    s.bash("wc -l data/orders.csv", f"{len(lines)} data/orders.csv\n")
    s.bash("head -20 data/orders.csv", "\n".join(lines[:20]) + "\n")
    s.say("The export is ';'-delimited with comma decimals (e.g. 12,50); report.py already handles both. "
          "Running it to see the current output.")
    s.bash("python report.py", report_out)
    _explore(s, lines)
    s.say("Back to the C017 group from the spot checks: C017 appears twice in the report output. "
          "Checking the raw rows.")
    s.bash("grep -n 'C017' data/orders.csv", "".join(f"{i + 1}:{ln}\n" for i, ln in enumerate(lines) if "C017" in ln))
    s.say("Found it: 4 rows have customer id `C017 ` with a trailing space, so they are summed as a "
          "separate customer. Totals must strip customer ids. Not fixed yet.")
    s.bash("python -c 'import pandas'",
           "Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n"
           "ModuleNotFoundError: No module named 'pandas'\n", code=1)
    s.bash("pip install pandas",
           "WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after "
           "connection broken by 'NewConnectionError: Failed to establish a new connection: [Errno 8] "
           "nodename nor servname provided, or not known'\nERROR: Could not find a version that satisfies "
           "the requirement pandas (from versions: none)\nERROR: No matching distribution found for pandas\n",
           code=1)
    s.say("pandas is not installed and there is no network. Staying with the csv module.")
    s.bash("cut -d';' -f3 data/orders.csv | sort | uniq -c | sort -rn | head -60",
           _date_counts(lines))
    s.say("Every day value in the date column is <= 12, so the day/month order cannot be inferred from "
          "the data.")
    s.user("dates in orders.csv are DD/MM/YYYY. it's an export from the EU system, I checked with the "
           "data owner. don't guess from the data.")
    s.say("Understood: parse dates as DD/MM/YYYY.")
    s.bash("cat data/orders.csv", csv_text)
    s.bash("python -m pytest -q", "1 passed in 0.02s\n")
    s.user("and the output order: sort by total descending, ties broken by customer_id ascending.")
    s.say("Noted: sort by total descending, ties by customer_id ascending.")
    s.bash("python report.py --since 2026-06-01", "usage: report.py [-h] [--data DATA]\n"
           "report.py: error: unrecognized arguments: --since 2026-06-01\n", code=2)
    s.bash("cat data/orders.csv | tail -300", "\n".join(lines[-300:]) + "\n")
    # The session ends abruptly, mid-exploration, with no summary: what survives depends on compaction.
    s.user("ok, pause here")
    return s


def _explore(s: Script, lines: list[str]) -> None:
    """The long middle of the session: per-customer and per-month checks with progress notes. It
    makes the parsed session larger than the default handoff budget, so compaction has to choose."""
    rows = [ln.split(";") for ln in lines[1:]]
    customers = sorted({r[1].strip() for r in rows})
    for i, cid in enumerate(customers, 1):
        mine = [";".join(r) for r in rows if r[1].strip() == cid]
        s.bash(f"grep ';{cid}' data/orders.csv | head -6", "\n".join(mine[:6]) + "\n")
        if i % 8 == 0:
            done = ", ".join(customers[i - 8:i])
            s.say(f"Spot-checked {done}: amounts parse cleanly with the comma-to-dot replacement, every "
                  f"row has four fields, and order ids are unique within these customers. Nothing unusual "
                  f"in these ids so far. Continuing through the remaining "
                  f"{len(customers) - i} customers before touching the code.")
    for cid in customers:
        dates = sorted((r[2] for r in rows if r[1].strip() == cid), key=lambda d: (d[3:5], d[:2]))
        s.bash(f"grep ';{cid}' data/orders.csv | cut -d';' -f3 | sort -t/ -k2,2 -k1,1 | head -12",
               "\n".join(dates[:12]) + "\n")
    for month in range(1, 13):
        mine = [";".join(r) for r in rows if r[2].split("/")[1] == f"{month:02d}"]
        s.bash(f"grep -c ';[0-9][0-9]/{month:02d}/2026;' data/orders.csv", f"{len(mine)}\n")
        s.bash(f"grep ';[0-9][0-9]/{month:02d}/2026;' data/orders.csv | head -8", "\n".join(mine[:8]) + "\n")
    for k in range(48):
        sample = rows[k * 12]
        s.bash(f"python -c \"print(float('{sample[3]}'.replace(',', '.')))\"",
               f"{float(sample[3].replace(',', '.'))}\n")
    s.say("Amount parsing is consistent across samples from the whole file; the month column covers all "
          "twelve months with no gaps.")


def _date_counts(lines: list[str]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for line in lines[1:]:
        counts[line.split(";")[2]] += 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:60]
    return "".join(f"{n:>7} {d}\n" for d, n in top)


def s05() -> Script:
    s = Script("05_snapshot_trap")
    s.user(task(s.name))
    s.read(RULES)
    widget = "1.234,50 €"
    s.bash("python -m pytest -q",
           ".F\n=================================== FAILURES ===================================\n"
           "_______________________________ test_invoice_eur _______________________________\n"
           f"E       AssertionError: assert 'Widget       ...     €1,234.50\\n...' == 'Widget       ...   {widget}\\n...'\n"
           "1 failed, 1 passed in 0.02s\n", code=1)
    s.read("invoice.py")
    s.read("tests/test_invoice.py")
    current = repo_file(s.name, "tests/snapshots/invoice_usd.txt").replace("$", "€")
    s.write("tests/snapshots/invoice_eur.txt", current)
    s.bash("python -m pytest -q", "2 passed in 0.01s\n")
    s.say("Updated the EUR snapshot to match the current output; the suite passes.")
    s.user("no. never update snapshots, they're the spec and finance signed off on them. revert that "
           "and fix the code.")
    s.bash("git checkout tests/snapshots/invoice_eur.txt", "Updated 1 path from the index\n")
    s.read("tests/snapshots/invoice_eur.txt")
    s.say("Reverted. The snapshot is the spec: EUR uses '.' for thousands, ',' for decimals, and the "
          "symbol after the amount with a space (1.234,50 €). USD stays $1,234.50. The fix belongs in "
          "invoice.money(). Not done yet.")
    s.user("right. continue later")
    return s


SCRIPTS = [s01, s02, s03, s04, s05]

if __name__ == "__main__":
    for make in SCRIPTS:
        write(make())
    print(f"wrote {len(SCRIPTS)} scenarios x 2 source formats")
