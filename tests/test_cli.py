import json
from pathlib import Path

import pytest

from handoff.cli import main
from tests.conftest import git


def test_create_no_llm_install_and_delta(project, claude_home, capsys):
    out = project / "out1"
    assert main(["create", "--to", "codex", "--path", str(project), "--no-llm", "--out", str(out),
                 "--install"]) == 0
    files = {p.name for p in out.iterdir()}
    assert files == {"HANDOFF.md", "identity.md", "project.md", "session.md", "delta.md", "next.md"}
    h = (out / "HANDOFF.md").read_text()
    assert "sk-ant" not in h and "[REDACTED:anthropic]" in h
    assert "AI researcher" in (out / "identity.md").read_text()
    assert "8 GPUs" in (out / "project.md").read_text()
    assert "First handoff" in (out / "delta.md").read_text()
    assert "uv run pytest passes" in (out / "next.md").read_text()  # last human ask, not meta
    agents = (project / "AGENTS.md").read_text()
    assert agents.startswith("# Agents\nPre-existing content that must survive.")
    assert "<!-- handoff:begin -->" in agents
    st = json.loads((project / ".handoff" / "state.json").read_text())
    assert st["recipients"]["codex"]["last_message_uuid"] == "as4"
    assert (project / ".handoff" / "latest").is_symlink()

    # second run after a commit: delta lists the commit; install is idempotent
    (project / "new.py").write_text("x = 1\n")
    git(project, "add", ".")
    git(project, "commit", "-qm", "add new.py")
    out2 = project / "out2"
    assert main(["create", "--to", "codex", "--path", str(project), "--no-llm", "--out", str(out2),
                 "--install"]) == 0
    d = (out2 / "delta.md").read_text()
    assert "add new.py" in d and "new.py" in d and "First handoff" not in d
    agents2 = (project / "AGENTS.md").read_text()
    assert agents2.count("<!-- handoff:begin -->") == 1
    assert agents2.startswith("# Agents\nPre-existing content that must survive.")


def test_dry_run_writes_nothing(project, claude_home, capsys):
    assert main(["create", "--to", "claude", "--path", str(project), "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "===== system =====" in printed and "Claude Code" in printed
    assert "[REDACTED:anthropic]" in printed
    assert not (project / ".handoff").exists()


def test_budget_applies(project, claude_home):
    out = project / "o"
    assert main(["create", "--to", "cursor", "--path", str(project), "--no-llm", "--out", str(out),
                 "--budget", "500"]) == 0
    assert "truncated" in (out / "session.md").read_text()
    assert len((out / "HANDOFF.md").read_text()) < 4000


def test_unknown_recipient(project, claude_home):
    with pytest.raises(SystemExit):
        main(["create", "--to", "gemini", "--path", str(project), "--no-llm"])


def test_no_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFF_HOME_OVERRIDE", str(tmp_path / "empty"))
    with pytest.raises(SystemExit, match="no source agent state"):
        main(["create", "--to", "codex", "--path", str(tmp_path), "--no-llm"])


def test_codex_to_claude(project, codex_home):
    out = project / "o"
    assert main(["create", "--to", "claude", "--path", str(project), "--no-llm", "--out", str(out),
                 "--install"]) == 0
    assert "Fix the failing test" in (out / "session.md").read_text()
    assert "Always run tests" in (out / "identity.md").read_text()
    assert (project / "CLAUDE.md").read_text().startswith("# Project rules")


def test_sources_and_profiles(project, claude_home, capsys):
    assert main(["sources", "--path", str(project), "-v"]) == 0
    assert "* claude_code" in capsys.readouterr().out
    assert main(["profiles", "--json"]) == 0
    assert '"instruction_file": "AGENTS.md"' in capsys.readouterr().out
    Path(project)  # silence unused
