import json
import os

from handoff.sources.claude_code import ClaudeCodeSource, project_slug


def test_slug():
    assert project_slug("/handoff-test/user/handoff") == "-handoff-test-user-handoff"
    assert project_slug("/Users/x/my.proj_1") == "-Users-x-my-proj-1"


def test_parse_session(project, claude_home):
    src = ClaudeCodeSource(claude_home)
    sessions = src.sessions(str(project))
    assert len(sessions) == 1
    s = sessions[0]
    assert s.cwd == "/tmp/demo-proj" and s.branch == "feat/flag"
    roles = [m.role for m in s.messages]
    # user, merged assistant (3 records), user, assistant
    assert roles == ["user", "assistant", "user", "assistant"]
    text = "\n".join(m.text for m in s.messages)
    assert "SUBAGENT" not in text
    assert "HARNESS INJECTED" not in text
    assert "secret thoughts" not in text
    assert "<system-reminder>" not in text
    assert "uv run pytest passes" in s.messages[2].text
    merged = s.messages[1]
    assert merged.text == "Reading the file first."
    assert [t.name for t in merged.tool_calls] == ["Read", "Bash"]
    assert merged.tool_calls[0].input_summary == "/tmp/demo-proj/cli.py"
    assert merged.tool_calls[0].result_summary.startswith("import sys")
    assert merged.tool_calls[1].is_error and "AssertionError" in merged.tool_calls[1].result_summary
    assert any("Compaction note" in x for x in s.summaries)
    assert any("Session summary" in x for x in s.summaries)
    assert s.started.startswith("2026-10-27T10:00:00") and s.ended.startswith("2026-10-27T10:01:05")


def test_layers(project, claude_home):
    src = ClaudeCodeSource(claude_home)
    ident = src.identity_docs()
    assert len(ident) == 1 and "AI researcher" in ident[0].content
    proj = src.project_docs(str(project))
    paths = [d.path for d in proj]
    assert any(p.endswith("CLAUDE.md") for p in paths)
    assert any(p.endswith("memory/notes.md") for p in paths)
    assert src.detect(str(project))
    assert not src.detect("/nonexistent/dir")


def test_tolerates_garbage(tmp_path, claude_home, project):
    src = ClaudeCodeSource(claude_home)
    p = src.session_paths(str(project))[0]
    p.write_text(p.read_text() + "not json\n{\"type\":\"user\",\"message\":\"notadict\"}\n[1,2]\n")
    assert len(src.sessions(str(project))[0].messages) == 4


def test_message_typed_mid_turn_is_a_user_turn(project, claude_home):
    # Shapes as Claude Code 2.1.281 writes them: the message is a queued_command attachment between
    # tool turns, and queue-operation records repeat its text.
    def queued(uuid, prompt, mode, **extra):
        return {"type": "attachment", "uuid": uuid, "isSidechain": False,
                "timestamp": "2026-11-02T20:57:46.134Z",
                "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": mode,
                               **extra}}

    records = [
        {"type": "user", "uuid": "u1", "timestamp": "2026-11-02T20:57:00.000Z",
         "message": {"role": "user", "content": "Survey the repos in ~."}},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls ~"}}]}},
        {"type": "queue-operation", "operation": "enqueue", "content": "only read the handoff repo"},
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "handoff\nother"}]}},
        {"type": "queue-operation", "operation": "remove", "content": "only read the handoff repo",
         "reason": "absorbed_mid_turn"},
        queued("q1", "only read the handoff repo", "prompt", origin={"kind": "human"},
               humanTurn=True),
        queued("q2", "<task-notification>done</task-notification>", "task-notification"),
        queued("q3", '<agent-message from="x">hi</agent-message>', "prompt", isMeta=True,
               origin={"kind": "peer"}),
        {"type": "user", "uuid": "n1", "origin": {"kind": "task-notification"},  # between turns
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        {"type": "assistant", "uuid": "a2", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Only handoff, then."}]}},
    ]
    d = claude_home / "projects" / project_slug(str(project))
    (d / "mid.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    s = ClaudeCodeSource(claude_home).sessions(str(project), session_id="mid")[0]
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "Survey the repos in ~."),
        ("assistant", ""),
        ("user", "only read the handoff repo"),
        ("assistant", "Only handoff, then."),
    ]
    assert s.messages[2].uuid == "q1" and s.messages[2].timestamp.startswith("2026-11-02T20:57:46")


def test_harness_records_are_not_user_turns(project, claude_home):
    # Shapes as Claude Code 2.1.x writes them: a `!` command and its output, a built-in slash
    # command and its local output, each after an isMeta caveat. A slash command that runs a skill
    # gets no local output and is a request.
    def user(uuid, content, **extra):
        return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": content}, **extra}

    caveat = "<local-command-caveat>Caveat: generated by local commands.</local-command-caveat>"
    records = [
        user("c0", caveat, isMeta=True),
        user("m1", "<command-name>/model</command-name>\n<command-message>model</command-message>\n"
                   "<command-args></command-args>"),
        user("m2", "<local-command-stdout>Set model to Opus</local-command-stdout>"),
        user("u1", "fix the flaky test"),
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": "On it."}},
        user("c1", caveat, isMeta=True),
        user("b1", "<bash-input>srun --gpus=1 python check.py</bash-input>"),
        user("b2", "<bash-stdout>ok</bash-stdout><bash-stderr></bash-stderr>"),
        user("n1", "<task-notification>\n<status>completed</status>\n</task-notification>"),
        user("s1", "<command-message>review</command-message>\n<command-name>/review</command-name>\n"
                   "<command-args>only the parser</command-args>"),
        user("s2", "Review the code. (skill prompt)", isMeta=True),
        {"type": "assistant", "uuid": "a2", "message": {"role": "assistant", "content": "Reviewed."}},
    ]
    d = claude_home / "projects" / project_slug(str(project))
    (d / "noise.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    s = ClaudeCodeSource(claude_home).sessions(str(project), session_id="noise")[0]
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "fix the flaky test"),
        ("assistant", "On it."),
        ("user", "/review only the parser"),
        ("assistant", "Reviewed."),
    ]


def test_session_of_only_harness_records_is_skipped(project, claude_home):
    d = claude_home / "projects" / project_slug(str(project))
    noise = d / "znoise.jsonl"
    noise.write_text(json.dumps({"type": "user", "uuid": "b1", "message": {
        "role": "user", "content": "<bash-input>ls</bash-input>"}}) + "\n")
    newest = max(p.stat().st_mtime for p in d.glob("*.jsonl")) + 10
    os.utime(noise, (newest, newest))
    s = ClaudeCodeSource(claude_home).sessions(str(project))
    assert len(s) == 1 and s[0].id != "znoise"


def test_compacted_history_is_replaced_by_its_summary(project, claude_home):
    # Shapes as Claude Code 2.1.319 writes them: the boundary lists the recent records it kept
    def rec(t, uuid, content):
        return {"type": t, "uuid": uuid, "message": {"role": t, "content": content}}

    def boundary(kept):
        meta = {"trigger": "auto", "preservedMessages": {"uuids": kept}} if kept is not None else {}
        return {"type": "system", "subtype": "compact_boundary", "compactMetadata": meta}

    records = [
        rec("user", "u1", "build the importer"),
        rec("assistant", "a1", "explored for an hour " + "x" * 500),
        rec("user", "u2", "never touch the fixtures"),
        rec("assistant", "a2", "more exploration"),
        rec("assistant", "a3", "the parser is the culprit"),  # merged into a2's message, but kept
        rec("user", "u3", "ok, fix it"),
        boundary(["a3", "u3"]),
        {"type": "user", "uuid": "s1", "isCompactSummary": True,
         "message": {"role": "user", "content": "Summary: importer half done."}},
        rec("assistant", "a4", "fixing the parser now"),
    ]
    d = claude_home / "projects" / project_slug(str(project))
    (d / "cmp.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    s = ClaudeCodeSource(claude_home).sessions(str(project), session_id="cmp")[0]
    assert [m.text for m in s.messages] == [
        "build the importer", "never touch the fixtures",
        "more exploration\nthe parser is the culprit", "ok, fix it", "fixing the parser now"]
    assert s.summaries == ["Summary: importer half done."]

    # Older versions write no preservedMessages: the summary replaces every assistant turn before it
    (d / "old.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        rec("user", "u1", "go"), rec("assistant", "a1", "went"), boundary(None), rec("user", "u2", "?")]))
    s = ClaudeCodeSource(claude_home).sessions(str(project), session_id="old")[0]
    assert [m.text for m in s.messages] == ["go", "?"]
