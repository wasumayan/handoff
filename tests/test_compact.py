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
    assert out.chars <= 3000 + 500  # protected messages may slightly exceed
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
