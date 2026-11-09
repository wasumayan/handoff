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
