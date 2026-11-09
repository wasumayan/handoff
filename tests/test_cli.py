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
    assert files == {"HANDOFF.md", "BRIEF.md", "identity.md", "project.md", "session.md", "delta.md",
                     "next.md"}
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


def test_out_outside_project_writes_nothing_into_project(project, claude_home, tmp_path):
    def snapshot():
        return {(str(p.relative_to(project)), p.stat().st_mtime_ns)
                for p in project.rglob("*") if p.is_file()}

    before = snapshot()
    out = tmp_path / "elsewhere"
    assert main(["create", "--to", "codex", "--path", str(project), "--no-llm", "--out", str(out)]) == 0
    assert (out / "HANDOFF.md").is_file()
    assert not (project / ".handoff").exists()
    assert snapshot() == before  # includes .git/index


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


def test_keep_pii_flag(project, claude_home, capsys):
    (project / "CLAUDE.md").write_text("# Project rules\nEscalate to lead@corp.example.com.\n")
    assert main(["create", "--to", "codex", "--path", str(project), "--dry-run"]) == 0
    assert "lead@corp.example.com" not in capsys.readouterr().out
    assert main(["create", "--to", "codex", "--path", str(project), "--dry-run", "--keep-pii"]) == 0
    assert "lead@corp.example.com" in capsys.readouterr().out


def test_brief_installs_only_the_summary_and_budget_is_strict(project, claude_home, monkeypatch):
    out = project / ".handoff" / "b"
    assert main(["create", "--to", "codex", "--path", str(project), "--no-llm", "--out", str(out),
                 "--install", "--brief"]) == 0
    block = (project / "AGENTS.md").read_text().split("<!-- handoff:begin -->")[1]
    assert "From the session log" in block and "# Session history" not in block
    assert ".handoff/b/" in block  # points at the layers on disk
    assert "**Task (first request):**" in (out / "next.md").read_text()

    # a long session: the installed block, headers and next.md included, must fit the budget
    from handoff.sources.claude_code import ClaudeCodeSource
    sp = ClaudeCodeSource(claude_home).session_paths(str(project))[0]
    sp.write_text(sp.read_text() + "".join(json.dumps(
        {"type": "assistant", "uuid": f"x{i}", "message": {"role": "assistant", "content": "y" * 600}})
        + "\n" for i in range(40)))

    def block(budget):
        args = ["create", "--to", "codex", "--path", str(project), "--no-llm", "--out",
                str(project / f"o{budget}"), "--install"] + (["--budget", str(budget)] if budget else [])
        assert main(args) == 0
        return (project / "AGENTS.md").read_text().split("<!-- handoff:begin -->")[1].split(
            "<!-- handoff:end -->")[0].strip()

    # an --out outside the project is pointed at with ~, not the user's home path
    monkeypatch.setenv("HOME", str(project.parent))
    assert main(["create", "--to", "codex", "--path", str(project), "--no-llm", "--brief", "--install",
                 "--out", str(project.parent / "elsewhere")]) == 0
    assert "`~/elsewhere/`" in (project / "AGENTS.md").read_text()

    assert len(block(None)) > 8000
    assert len(block(8000)) <= 8000
