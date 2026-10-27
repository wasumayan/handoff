import shutil
import subprocess
from pathlib import Path

import pytest

from handoff.sources.claude_code import project_slug

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repo with one commit, plus CLAUDE.md and AGENTS.md at the root."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "cli.py").write_text("import sys\nprint('hi')\n")
    (proj / "CLAUDE.md").write_text("# Project rules\nRun `uv run pytest`.\n")
    (proj / "AGENTS.md").write_text("# Agents\nPre-existing content that must survive.\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=proj, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], cwd=proj, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                   cwd=proj, check=True)
    return proj


@pytest.fixture
def claude_home(tmp_path: Path, project: Path, monkeypatch) -> Path:
    """Fixture ~/.claude whose project dir slug matches `project`."""
    home = tmp_path / "home"
    shutil.copytree(FIXTURES / "claude_home", home / ".claude")
    projects = home / ".claude" / "projects"
    (projects / "-tmp-demo-proj").rename(projects / project_slug(str(project)))
    monkeypatch.setenv("HANDOFF_HOME_OVERRIDE", str(home))
    return home / ".claude"


@pytest.fixture
def codex_home(tmp_path: Path, project: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    dest = home / ".codex"
    shutil.copytree(FIXTURES / "codex_home", dest)
    # rewrite the fixture's cwd to the temp project
    for p in dest.rglob("*.jsonl"):
        p.write_text(p.read_text().replace("/tmp/demo-proj", str(project)))
    monkeypatch.setenv("HANDOFF_HOME_OVERRIDE", str(home))
    return dest


def git(proj: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=proj, check=True, capture_output=True, text=True).stdout
