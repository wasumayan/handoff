"""Cursor as a source: rules files only.

Cursor's chat history lives in SQLite (workspaceStorage/<hash>/state.vscdb) in an undocumented,
version-dependent schema. v0 reads only the instruction layer:

    <repo>/.cursorrules            legacy single-file rules
    <repo>/.cursor/rules/*.mdc     project rules
    ~/.cursor/rules/*.mdc          user rules (newer builds)
"""

from __future__ import annotations

from pathlib import Path

from ..model import Doc
from .base import Source, home, read_dir_docs, read_doc


class CursorSource(Source):
    name = "cursor"
    display = "Cursor"

    def __init__(self, root: Path | None = None):
        self.root = root or (home() / ".cursor")

    def identity_docs(self) -> list[Doc]:
        return read_dir_docs(self.root / "rules", self.name, "*.mdc")

    def project_docs(self, cwd: str) -> list[Doc]:
        repo = Path(cwd)
        out = []
        d = read_doc(repo / ".cursorrules", self.name)
        if d:
            out.append(d)
        out += [x for x in read_dir_docs(repo / ".cursor" / "rules", self.name, "*.mdc")
                if not x.path.endswith("handoff.mdc")]
        return out

    def detect(self, cwd: str) -> bool:
        return bool(self.project_docs(cwd))
