from pathlib import Path

import pytest

from handoff.sources.codex import CodexSource


def test_parse(project, codex_home):
    src = CodexSource(codex_home)
    assert src.detect(str(project))
    sessions = src.sessions(str(project))
    assert len(sessions) == 1
    s = sessions[0]
    assert s.cwd == str(project)
    assert [m.role for m in s.messages] == ["user", "assistant"]
    assert s.messages[0].text == "Fix the failing test in tests/test_cli.py"
    assert "<environment_context>" not in s.messages[0].text
    tc = s.messages[1].tool_calls[0]
    assert tc.name == "shell" and tc.input_summary == "bash -lc pytest -q"
    assert tc.is_error and "AssertionError" in tc.result_summary
    # event_msg duplicate of the response_item is not added twice
    assert s.messages[1].text.count("Patching cli.py") == 1


def test_layers(project, codex_home):
    src = CodexSource(codex_home)
    assert {d.path.split("/")[-1] for d in src.identity_docs()} == {"AGENTS.md", "config.toml"}
    proj = src.project_docs(str(project))
    assert len(proj) == 1 and proj[0].path.endswith("AGENTS.md")


def test_cwd_matches_across_case_on_case_insensitive_fs(project, codex_home):
    other_case = project.parent / project.name.upper()
    if not other_case.exists():
        pytest.skip("filesystem is case-sensitive")
    assert len(CodexSource(codex_home).session_paths(str(other_case))) == 1


SESSIONS = Path(__file__).parent / "fixtures" / "codex_sessions"


def test_messages_written_twice_are_kept_once():
    """Codex 0.14x writes every turn as a response_item message and an event_msg copy. Human
    turns come response_item first; assistant turns come event_msg first (real 0.146.1 order)."""
    s = CodexSource().read_session(SESSIONS / "v0146.jsonl")
    assert [m.role for m in s.messages] == ["user", "assistant", "user", "assistant"]
    assert s.messages[0].text == "Build the release artifacts."
    assert s.messages[1].text == ("The config file exists. Running a 3-step smoke build.\n"
                                  "Smoke build done in 4.2 s; the patch failed, retrying.")
    assert s.messages[3].text == "Stopping it now.\nServer abc is stopped."
    s = CodexSource().read_session(SESSIONS / "v0144.jsonl")
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "Add the changelog entry."), ("assistant", "Added docs/CHANGELOG.md.")]


def test_v0146_code_mode_exec():
    """Real 0.146.1 shapes: JS `exec` custom tool calls, `wait` polls on long-running cells,
    outputs as JSON-encoded input_text blocks."""
    s = CodexSource().read_session(SESSIONS / "v0146.jsonl")
    ls, build, patch = s.messages[1].tool_calls  # the two `wait` polls fold into the build call
    assert ls.name == "exec" and ls.input_summary == "ls assets && git status --short"
    assert ls.result_summary == "build.toml" and not ls.is_error
    assert build.input_summary == "python build.py --steps 3"
    assert build.result_summary == "built 3 steps in 4.2 s" and not build.is_error
    assert patch.input_summary == "apply_patch src/build.py"
    assert patch.is_error and "did not match" in patch.result_summary
    assert s.messages[3].tool_calls[0].result_summary == "server abc stopped"
    poll = s.messages[3].tool_calls[1]
    assert poll.input_summary == "write_stdin"
    assert poll.is_error and poll.result_summary == "exec cell 42 not found"


def test_v0144_exec_command_and_apply_patch():
    s = CodexSource().read_session(SESSIONS / "v0144.jsonl")
    cat, grep, patch = s.messages[1].tool_calls
    assert cat.is_error and cat.result_summary == "cat: missing.md: No such file or directory"
    assert not grep.is_error  # "error" in the output of a successful command
    assert patch.name == "apply_patch" and patch.input_summary == "apply_patch docs/CHANGELOG.md"
    assert not patch.is_error and patch.result_summary.startswith("Success.")
