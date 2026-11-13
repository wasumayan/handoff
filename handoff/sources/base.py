from __future__ import annotations

import os
from pathlib import Path

from ..model import Doc, Session


def read_doc(path: Path, origin: str) -> Doc | None:
    try:
        if path.is_file():
            return Doc(path=str(path), content=path.read_text(errors="replace"), origin=origin)
    except OSError:
        return None
    return None


def read_dir_docs(d: Path, origin: str, pattern: str = "*.md") -> list[Doc]:
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.rglob(pattern)):
        doc = read_doc(p, origin)
        if doc:
            out.append(doc)
    return out


def home() -> Path:
    return Path(os.environ.get("HANDOFF_HOME_OVERRIDE") or Path.home())


class Source:
    """A place a coding agent leaves state on disk.

    Subclasses implement the four readers. All of them must tolerate missing files and
    unknown record shapes: these formats are not public APIs and drift between versions.
    """

    name: str = "base"
    display: str = "Base"

    def detect(self, cwd: str) -> bool:
        return bool(self.session_paths(cwd)) or bool(self.project_docs(cwd))

    def identity_docs(self) -> list[Doc]:
        return []

    def project_docs(self, cwd: str) -> list[Doc]:
        return []

    def session_paths(self, cwd: str) -> list[Path]:
        """Session files for this cwd, newest first."""
        return []

    def read_session(self, path: Path) -> Session:
        raise NotImplementedError

    def sessions(self, cwd: str, limit: int = 1, session_id: str | None = None) -> list[Session]:
        paths = self.session_paths(cwd)
        if session_id:
            paths = [p for p in paths if session_id in p.name]
            if not paths:
                raise FileNotFoundError(f"no {self.display} session matching {session_id!r}")
        out = []
        for p in paths:  # newest first, past sessions with nothing in them (only `!` commands)
            if len(out) >= limit:
                break
            try:
                s = self.read_session(p)
            except (OSError, ValueError):
                continue
            if s.messages or s.summaries:
                out.append(s)
        return out
