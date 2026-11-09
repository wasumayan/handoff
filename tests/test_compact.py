from handoff.compact import compact, compact_session
from handoff.model import Bundle, Doc, Message, Session, ToolCall


def _session(n: int) -> Session:
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(Message(role=role, text=f"{role} message {i} " + "x" * 400, uuid=f"m{i}",
                            tool_calls=[ToolCall("Bash", "cmd " * 20, "out " * 50)] if role == "assistant" else []))
    return Session(id="s", source="t", messages=msgs)


def test_session_budget_keeps_anchors():
    s = _session(20)
    out = compact_session(s, 3000)
    assert out.chars <= 3000
    assert out.truncated
    ids = [m.uuid for m in out.messages]
    assert ids[0] == "m0"  # first human turn
    assert ids[-2:] == ["m18", "m19"]  # last two
    assert out.summaries and "dropped" in out.summaries[0]


def test_noop_under_budget():
    s = _session(4)
    assert compact_session(s, 10**6) is s


def test_bundle_drops_old_sessions_first():
    b = Bundle(source="t", recipient="codex", cwd="/x",
               identity=[Doc("id", "p" * 2000, "t")],
               sessions=[_session(6), _session(30), _session(30)])
    out = compact(b, 8000)
    assert len(out.sessions) == 1 and out.sessions[0].messages[0].uuid == "m0"
    assert len(b.sessions) == 3  # original untouched
    assert any("dropped older session" in n for n in out.notes)
    assert out.identity[0].content.startswith("p")  # docs trimmed last; small doc intact


def _steered_session() -> Session:
    """Shape of a real Codex session: a long opening prompt, short human corrections in the
    middle, tool-heavy assistant turns."""
    def busy(i: int) -> Message:
        tools = [ToolCall("exec", f"step {i}.{k} " + "c" * 150, "r" * 300) for k in range(12)]
        tools.append(ToolCall("exec", f"deploy {i}", f"Error: version too old {i}", is_error=True))
        return Message("assistant", f"assistant report {i}: step {i} blocked " + "a" * 200,
                       tool_calls=tools, uuid=f"a{i}")
    msgs = [Message("user", "the task " + "t" * 3000, uuid="u0")]
    for i, ask in enumerate(["use the staging server", "dont stop the server after step 1",
                             "finish every step i listed", "why is this taking so long"]):
        msgs += [busy(i), Message("user", ask, uuid=f"u{i + 1}")]
    msgs += [busy(9)]
    return Session(id="s", source="t", messages=msgs)


def test_tool_detail_goes_before_prose():
    s = _steered_session()
    out = compact_session(s, 6000)
    assert out.chars <= 6000
    assert [m.uuid for m in out.messages] == [m.uuid for m in s.messages]  # nothing dropped
    report = out.messages[1]
    assert report.text.startswith("assistant report 0") and "12 tool call(s) omitted" in report.text
    assert [t.input_summary for t in report.tool_calls] == ["deploy 0"]  # the failure survives
    assert "version too old" in report.tool_calls[0].result_summary


def test_human_turns_outlive_assistant_turns():
    s = _steered_session()
    out = compact_session(s, 4200)
    texts = [m.text for m in out.messages if m.role == "user"]
    assert texts[1:] == ["use the staging server", "dont stop the server after step 1",
                         "finish every step i listed", "why is this taking so long"]
    assert out.messages[-1].uuid == "a9"
    assert "dropped" in out.summaries[0]


def test_assistant_reports_outlive_tool_lines():
    s = _steered_session()
    s.messages[3].text = "builds complete in 4.2 s " + "r" * 1200  # merged progress reports
    out = compact_session(s, 7500)
    assert out.messages[3].text.startswith(s.messages[3].text)
    assert "tool call(s) omitted" in out.messages[3].text


def test_repeated_calls_collapse_to_the_last_run():
    runs = [ToolCall("Bash", "pytest -q", "1 failed", is_error=True)] * 4 + [ToolCall("Bash", "pytest -q", "ok")]
    s = Session(id="s", source="t", messages=[
        Message("user", "go"), Message("assistant", "x" * 2000, tool_calls=runs), Message("user", "?"),
        Message("assistant", "y")])
    out = compact_session(s, 1500)
    calls = [t for m in out.messages for t in m.tool_calls]
    assert len(calls) <= 1 and all("run 5x" in t.input_summary for t in calls)


def test_budget_holds_even_for_huge_protected_messages():
    s = Session(id="s", source="t", summaries=["S" * 5000], messages=[
        Message("user", "A" * 9000), Message("assistant", "B" * 200), Message("user", "C" * 7000)])
    out = compact_session(s, 3000)
    assert out.chars <= 3000
    assert out.messages[0].text.startswith("AAA") and "chars cut" in out.messages[0].text
    assert out.messages[-1].text.endswith("CCC")
