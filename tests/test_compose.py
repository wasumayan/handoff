import json

from handoff.backend import Backend, BackendError
from handoff.compose import _parse, compose, messages_for
from handoff.model import Bundle, Composed, Message, Session
from handoff.recipients import get_recipient


class FakeBackend(Backend):
    def __init__(self, replies):
        super().__init__(base_url="http://fake/v1", model="fake")
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _bundle():
    return Bundle(source="claude_code", recipient="codex", cwd="/p",
                  sessions=[Session(id="s", source="claude_code",
                                    messages=[Message("user", "do X"), Message("assistant", "did half")])])


def test_parse_variants():
    good = {"current_state": "half done", "next_steps": ["finish"]}
    assert _parse(json.dumps(good)) == good
    assert _parse("```json\n" + json.dumps(good) + "\n```") == good
    assert _parse("Sure! " + json.dumps(good) + " hope that helps") == good
    assert _parse("not json") is None
    assert _parse('{"unrelated": 1}') is None


def test_compose_retry_then_success():
    b = FakeBackend(["garbage", json.dumps({"current_state": "ok", "decisions": "one"})])
    c, warn = compose(_bundle(), get_recipient("codex").profile, b)
    assert isinstance(c, Composed) and c.current_state == "ok" and c.decisions == ["one"]
    assert warn == "" and len(b.calls) == 2 and len(b.calls[1]) == 4
    assert c.model == "fake@fake"


def test_compose_gives_up():
    b = FakeBackend(["nope", "still nope"])
    c, warn = compose(_bundle(), get_recipient("codex").profile, b)
    assert c is None and "non-JSON" in warn


def test_compose_backend_error():
    b = FakeBackend([BackendError("down")])
    c, warn = compose(_bundle(), get_recipient("codex").profile, b)
    assert c is None and warn == "down"


def test_prompt_mentions_recipient_profile():
    msgs = messages_for(_bundle(), get_recipient("codex").profile)
    assert msgs[0]["role"] == "system" and "Codex CLI" in msgs[0]["content"]
    assert "AGENTS" not in msgs[1]["content"] or True
    assert "do X" in msgs[1]["content"]


def test_backend_from_env(monkeypatch):
    monkeypatch.delenv("HANDOFF_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert Backend.from_env() is None
    b = Backend.from_env("ollama")
    assert b.host == "localhost:11434" and b.model == "llama3.1"
    monkeypatch.setenv("HANDOFF_BASE_URL", "https://x.example/v1/")
    monkeypatch.setenv("HANDOFF_MODEL", "m")
    assert Backend.from_env().base_url == "https://x.example/v1"
