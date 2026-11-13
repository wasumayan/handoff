from handoff.card import build
from handoff.model import Message, Session, ToolCall


def test_card_copies_facts_from_the_log():
    s = Session(id="s", source="t", messages=[
        Message("user", "add a --since flag to report.py"),
        Message("assistant", "", tool_calls=[
            ToolCall("Bash", "pip install pandas", "No matching distribution", is_error=True),
            ToolCall("Edit", "/w/report.py"),
            ToolCall("apply_patch", "apply_patch report.py, tests/test_report.py"),
            ToolCall("Bash", "uv run pytest -q", "1 failed", is_error=True),
            ToolCall("Bash", "ruff check .", "E501", is_error=True),
            ToolCall("Bash", "ruff check .", "All checks passed!"),
        ]),
        Message("user", "dates are DD/MM, not MM/DD"),
    ])
    card = build(s)
    assert "add a --since flag" in card and "dates are DD/MM" in card
    assert "`/w/report.py`, `report.py`, `tests/test_report.py`" in card
    assert "**Last test run:** `uv run pytest -q`: FAILED" in card
    assert "pip install pandas" in card and "ruff check" not in card.split("never followed")[1]
    assert build(None) == "" and build(Session(id="e", source="t")) == ""


def test_expected_exits_are_not_open_errors():
    # Real shapes: Claude Code reports the last command's status, so a trailing diff, grep or
    # `ls x 2>/dev/null` marks a call whose work succeeded as an error.
    def bash(cmd, out, code):
        return ToolCall("Bash", cmd, f"Exit code {code}\n{out}", is_error=True, exit_code=code)

    s = Session(id="s", source="t", messages=[
        Message("user", "rerun the fixed tasks"),
        Message("assistant", "", tool_calls=[
            bash("diff old.sh new.sh", "8c8\n< OUT=a\n---\n> OUT=b", 1),
            bash("echo \"done $(grep -c FAILED log)\"", "qwen: done 60/60, FAILED 0", 1),
            bash("squeue -u $USER; ls results/ 2>/dev/null", "JOBID NAME ST", 2),
            bash("python check.py", "Traceback (most recent call last):\nKeyError: 'x'", 1),
            bash("git push", "fatal: Could not read from remote repository.", 128),
            bash("uv run pytest -q", "....F", 1),  # a test run's exit code is its result
        ]),
    ])
    card = build(s)
    errors = card.split("never followed")[1]
    assert "python check.py" in errors and "git push" in errors
    assert "diff" not in errors and "grep" not in errors and "squeue" not in errors
    assert "**Last test run:** `uv run pytest -q`: FAILED" in card


def test_card_says_when_nothing_was_edited():
    s = Session(id="s", source="t", messages=[
        Message("user", "fix it"), Message("assistant", "", tool_calls=[ToolCall("Bash", "rg bug")])])
    assert "**Files edited:** none in this session" in build(s)
