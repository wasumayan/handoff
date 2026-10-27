from handoff.recipients import get_recipient
from handoff.recipients.base import BEGIN, END, splice_block


def test_splice_fresh_and_idempotent():
    s1 = splice_block("", "v1", header="# AGENTS.md")
    assert s1.startswith("# AGENTS.md\n\n") and f"{BEGIN}\nv1\n{END}" in s1
    s2 = splice_block(s1, "v2")
    assert "v1" not in s2 and "v2" in s2 and s2.count(BEGIN) == 1
    assert s2.count("# AGENTS.md") == 1


def test_splice_preserves_surroundings():
    existing = "# Mine\n\nkeep this\n\n" + f"{BEGIN}\nold\n{END}\n\nand this trailer\n"
    out = splice_block(existing, "new")
    assert out.startswith("# Mine\n\nkeep this\n\n")
    assert out.endswith("and this trailer\n")
    assert "old" not in out and "\nnew\n" in out


def test_install_paths(tmp_path):
    files = {"HANDOFF.md": "body"}
    p = get_recipient("codex").install(str(tmp_path), files)
    assert p.name == "AGENTS.md" and BEGIN in p.read_text()
    p = get_recipient("claude").install(str(tmp_path), files)
    assert p.name == "CLAUDE.md"
    p = get_recipient("cursor").install(str(tmp_path), files)
    assert p.parts[-3:] == (".cursor", "rules", "handoff.mdc")
    assert p.read_text().startswith("---\n") and "alwaysApply: true" in p.read_text()
    p = get_recipient("chat").install(str(tmp_path), files)
    assert p.read_text() == "body"


def test_profiles_load():
    for name in ("codex", "claude_code", "cursor", "chat"):
        pr = get_recipient(name).profile
        assert pr.context_budget > 0 and pr.style and pr.instruction_file
