"""Generate the midpoint source sessions for each scenario, in Claude Code's JSONL shape.

These are authored, not recorded. To swap in a recorded session, drop the real
`~/.claude/projects/<slug>/<id>.jsonl` at `eval/scenarios/<id>/source/claude_code.jsonl` with
its cwd rewritten to the placeholder `/WORKDIR` (the runner substitutes the real path).

Run: python eval/scenarios/build_sessions.py
"""

from __future__ import annotations

import json
import pathlib
import uuid

HERE = pathlib.Path(__file__).resolve().parent
CWD = "/WORKDIR"


class Writer:
    def __init__(self, scenario: str, branch: str = "main"):
        self.sid = str(uuid.uuid5(uuid.NAMESPACE_URL, scenario))
        self.recs: list[dict] = []
        self.parent: str | None = None
        self.t = 0
        self.base = {"sessionId": self.sid, "cwd": CWD, "gitBranch": branch, "version": "2.1.311",
                     "userType": "external", "isSidechain": False}

    def _ts(self) -> str:
        self.t += 7
        return f"2026-10-27T09:{self.t // 60:02d}:{self.t % 60:02d}.000Z"

    def _push(self, rec: dict) -> str:
        uid = str(uuid.uuid4())
        rec.update(uuid=uid, parentUuid=self.parent, timestamp=self._ts(), **self.base)
        self.recs.append(rec)
        self.parent = uid
        return uid

    def user(self, text: str) -> None:
        self._push({"type": "user", "message": {"role": "user", "content": text}})

    def say(self, text: str) -> None:
        self._push({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "(omitted)"}, {"type": "text", "text": text}]}})

    def tool(self, name: str, inp: dict, result: str, is_error: bool = False) -> None:
        tid = "toolu_" + uuid.uuid4().hex[:12]
        self._push({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self._push({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}})

    def write(self, scenario_dir: pathlib.Path) -> None:
        out = scenario_dir / "source"
        out.mkdir(exist_ok=True)
        (out / "claude_code.jsonl").write_text("\n".join(json.dumps(r) for r in self.recs) + "\n")


def read_repo(scenario: str, rel: str) -> str:
    return (HERE / scenario / "repo" / rel).read_text()


def s01() -> None:
    w = Writer("01_simple")
    w.user((HERE / "01_simple" / "task.md").read_text().strip())
    w.say("Reading the file and the project rules first.")
    w.tool("Read", {"file_path": f"{CWD}/CLAUDE.md"}, read_repo("01_simple", "CLAUDE.md"))
    w.tool("Read", {"file_path": f"{CWD}/app.py"},
           read_repo("01_simple", "app.py").replace(
               '    ap.add_argument("--verbose", action="store_true", help="enable debug logging")\n', ""))
    w.say("Plan: (1) add the argparse flag, (2) set the logging level from it and emit the debug line, "
          "(3) run tests. Starting with the flag.")
    w.tool("Edit", {"file_path": f"{CWD}/app.py",
                    "old_string": '    ap.add_argument("name")\n',
                    "new_string": '    ap.add_argument("name")\n    ap.add_argument("--verbose", action="store_true", help="enable debug logging")\n'},
           "The file /WORKDIR/app.py has been updated.")
    w.tool("Bash", {"command": "python -m pytest -q", "description": "run tests"}, "1 passed in 0.01s")
    w.say("Flag is parsed. Not wired yet: basicConfig still hardcodes INFO and nothing logs "
          "'verbose mode on'. Next step is to set level = DEBUG when --verbose and log.debug the line "
          "before log.info. Remember the rule: no print.")
    w.user("ok continue")
    w.write(HERE / "01_simple")


def s02() -> None:
    w = Writer("02_medium")
    w.user((HERE / "02_medium" / "task.md").read_text().strip())
    w.tool("Read", {"file_path": f"{CWD}/CLAUDE.md"}, read_repo("02_medium", "CLAUDE.md"))
    w.tool("Bash", {"command": "uv run pytest -q", "description": "reproduce"},
           "FFF.\n...\nE   assert 0 == 25.0 ± 2.5e-05\ntests/test_calc.py::test_percent_of_total FAILED\n"
           "2 passed, 1 failed in 0.03s", is_error=True)
    w.say("Reproduced. percent_of_total('25, 75', 0) returns 0 instead of 25. Tracing the call chain.")
    w.tool("Read", {"file_path": f"{CWD}/calc/api.py"}, read_repo("02_medium", "calc/api.py"))
    w.tool("Read", {"file_path": f"{CWD}/calc/parse.py"}, read_repo("02_medium", "calc/parse.py"))
    w.say("parse_amounts is fine: it returns floats. Checking stats.")
    w.tool("Read", {"file_path": f"{CWD}/calc/stats.py"}, read_repo("02_medium", "calc/stats.py"))
    w.say("Root cause: calc/stats.py percent() casts both operands to int and uses floor division, so "
          "25//100 == 0 and 3//4 == 0. parse.py and api.py are correct; do not touch api.py's signature. "
          "Fix is `return part / whole * 100`. I have not applied it yet.")
    w.user("go ahead")
    w.write(HERE / "02_medium")


def s03() -> None:
    w = Writer("03_instructions")
    w.user((HERE / "03_instructions" / "task.md").read_text().strip().replace(" (done)", ""))
    w.tool("Read", {"file_path": f"{CWD}/CLAUDE.md"}, read_repo("03_instructions", "CLAUDE.md"))
    w.say("Noted the rules: stdlib only (no requests), int cents, per-module exception classes, one "
          "commit per step prefixed refactor:, do not touch shop/legacy.py, mock urlopen in tests.")
    old_cart = read_repo("03_instructions", "shop/cart.py").replace(
        "from .money import format_price\n\n\n", "").replace(
        "        return format_price(self.total_cents())",
        '        c = self.total_cents()\n        return f"${c // 100}.{c % 100:02d}"')
    w.tool("Read", {"file_path": f"{CWD}/shop/cart.py"}, old_cart)
    w.tool("Write", {"file_path": f"{CWD}/shop/money.py", "content": read_repo("03_instructions", "shop/money.py")},
           "File created successfully at: /WORKDIR/shop/money.py")
    w.tool("Edit", {"file_path": f"{CWD}/shop/cart.py", "old_string": "        c = self.total_cents()\n",
                    "new_string": "        return format_price(self.total_cents())\n"},
           "The file /WORKDIR/shop/cart.py has been updated.")
    w.tool("Write", {"file_path": f"{CWD}/tests/test_money.py", "content": read_repo("03_instructions", "tests/test_money.py")},
           "File created successfully at: /WORKDIR/tests/test_money.py")
    w.tool("Bash", {"command": "python -m pytest -q", "description": "step 1 tests"}, "4 passed in 0.02s")
    w.tool("Bash", {"command": "git add -A && git commit -qm 'refactor: extract price formatting into shop/money.py'"},
           "[main 1a2b3c4] refactor: extract price formatting into shop/money.py\n 3 files changed")
    w.say("Step 1 done and committed. Step 2 next: shop/rates.py currently does raw socket HTTP and "
          "get_rate has no type hints and returns whatever JSON held. Plan for step 2: urllib.request, "
          "RateError class, return float, tests that patch urllib.request.urlopen. Then step 3 "
          "Cart.total_in(currency) via get_rate + format_price, round to int cents before formatting.")
    w.user("yes, keep going with step 2 and 3. remember one commit per step")
    w.write(HERE / "03_instructions")


if __name__ == "__main__":
    s01()
    s02()
    s03()
    print("wrote source sessions")
