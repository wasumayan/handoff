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


def _long_session() -> Session:
    """Shape of a real multi-day Claude Code session: each assistant message is a whole agentic turn,
    narration first and the report at the end; one human turn is a pasted script."""
    msgs = [Message("user", "build the medical task split", uuid="u0")]
    for i in range(1, 9):
        ask = "x = 1\n" * 600 if i == 3 else f"short correction {i}"  # i == 3: a 3,600-char paste
        msgs.append(Message("user", ask, uuid=f"u{i}"))
        msgs.append(Message("assistant", f"I'll look at step {i} first. " + "narration " * 150
                            + f"REPORT {i}: 5 tasks fixed, 53 tests pass.", uuid=f"a{i}"))
    msgs += [Message("user", "push it", uuid="u9"), Message("assistant", "Pushed.", uuid="a9")]
    return Session(id="s", source="t", messages=msgs)


def test_recent_replies_survive_with_their_reports():
    s = _long_session()
    out = compact_session(s, 6000)
    assert out.chars <= 6000
    kept = {m.uuid: m.text for m in out.messages}
    for i in (6, 7, 8):  # the three replies before the protected last two messages
        assert f"a{i}" in kept and kept[f"a{i}"].endswith(f"REPORT {i}: 5 tasks fixed, 53 tests pass.")
    assert "a1" not in kept and "dropped" in out.summaries[0]  # older replies go first
    # the final fit after rendering keeps them too
    tight = compact_session(out, out.chars - 1500, keep_users=True)
    assert all(f"a{i}" in {m.uuid for m in tight.messages} for i in (6, 7, 8))


def test_pastes_lose_their_middle_before_human_turns_are_dropped():
    s = _long_session()
    out = compact_session(s, 6000)
    users = {m.uuid: m.text for m in out.messages if m.role == "user"}
    assert all(f"u{i}" in users for i in range(10))  # every human turn survives
    assert "chars cut" in users["u3"] and users["u3"].startswith("x = 1")


def test_expected_exits_are_trimmed_like_successes():
    def bash(cmd, out, code):
        return ToolCall("Bash", cmd, f"Exit code {code}\n{out}", is_error=True, exit_code=code)

    s = Session(id="s", source="t", messages=[
        Message("user", "go"),
        Message("assistant", "a" * 2000, tool_calls=[
            bash("diff a b", "8c8 " * 100, 1), bash("python x.py", "Traceback: KeyError", 1)]),
        Message("user", "?"), Message("assistant", "done")])
    out = compact_session(s, 1500)
    assert [t.input_summary for t in out.messages[1].tool_calls] == ["python x.py"]


def test_budget_holds_even_for_huge_protected_messages():
    s = Session(id="s", source="t", summaries=["S" * 5000], messages=[
        Message("user", "A" * 9000), Message("assistant", "B" * 200), Message("user", "C" * 7000)])
    out = compact_session(s, 3000)
    assert out.chars <= 3000
    assert out.messages[0].text.startswith("AAA") and "chars cut" in out.messages[0].text
    assert out.messages[-1].text.endswith("CCC")
