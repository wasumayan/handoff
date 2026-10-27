from handoff.sources.claude_code import ClaudeCodeSource, project_slug


def test_slug():
    assert project_slug("/home/user/handoff") == "-home-user-handoff"
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
