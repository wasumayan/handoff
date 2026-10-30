"""Exercise backend.complete, the stand-in agent, and eval arms against a scripted local server."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from handoff.backend import Backend  # noqa: E402


class Scripted:
    """Serves /v1/chat/completions from a queue of replies; records requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                srv.requests.append(body)
                reply = srv.replies.pop(0) if srv.replies else {"content": "{}"}
                if callable(reply):
                    reply = reply(body)
                msg = {"role": "assistant", "content": reply.get("content"),
                       **({"tool_calls": reply["tool_calls"]} if reply.get("tool_calls") else {})}
                out = {"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}/v1"

    def close(self):
        self.httpd.shutdown()


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_complete_and_usage():
    srv = Scripted([{"content": "hi"}])
    try:
        b = Backend(srv.base_url, "m")
        msg = b.complete([{"role": "user", "content": "x"}], json_mode=True)
        assert msg["content"] == "hi" and msg["_usage"]["prompt_tokens"] == 10
        assert srv.requests[0]["response_format"] == {"type": "json_object"}
    finally:
        srv.close()


def test_agent_loop(tmp_path):
    from agent import run_agent

    (tmp_path / "a.txt").write_text("old")
    srv = Scripted([
        {"content": "reading", "tool_calls": [call("read_file", {"path": "a.txt"})]},
        {"tool_calls": [call("write_file", {"path": "a.txt", "content": "new"}, "c2"),
                        call("run", {"command": "echo ran"}, "c3")]},
        {"tool_calls": [call("read_file", {"path": "../etc/passwd"}, "c4")]},
        {"tool_calls": [call("done", {"summary": "ok"}, "c5")]},
    ])
    try:
        res = run_agent(tmp_path, "INSTRUCTIONS", "do it", Backend(srv.base_url, "m"), max_turns=10)
    finally:
        srv.close()
    assert res.finished and res.turns == 4 and res.tool_calls == 5 and res.files_written == ["a.txt"]
    assert (tmp_path / "a.txt").read_text() == "new"
    assert res.commands == ["echo ran"]
    assert "INSTRUCTIONS" in srv.requests[0]["messages"][0]["content"]
    tool_msgs = [m for m in srv.requests[-1]["messages"] if m.get("role") == "tool"]
    assert any("outside repo" in m["content"] for m in tool_msgs)
    assert any(m["content"].startswith("exit code: 0") for m in tool_msgs)


def test_agent_stops_at_max_turns(tmp_path):
    from agent import run_agent

    srv = Scripted([{"tool_calls": [call("run", {"command": "true"})]}] * 10)
    try:
        res = run_agent(tmp_path, "", "x", Backend(srv.base_url, "m"), max_turns=3)
    finally:
        srv.close()
    assert res.turns == 3 and not res.finished


def test_native_docs_elided_for_claude(project, claude_home):
    from handoff.cli import main

    out = project / "o"
    assert main(["create", "--to", "claude", "--path", str(project), "--no-llm", "--out", str(out),
                 "--install"]) == 0
    claude_md = (project / "CLAUDE.md").read_text()
    assert claude_md.startswith("# Project rules\nRun `uv run pytest`.")
    # the installed block must not re-embed CLAUDE.md's own content
    block = claude_md.split("<!-- handoff:begin -->", 1)[1]
    assert "not duplicated here" in block and "Run `uv run pytest`." not in block
    # but memory notes (not native) and the standalone project.md keep everything
    assert "8 GPUs" in block
    assert "Run `uv run pytest`." in (out / "project.md").read_text()
