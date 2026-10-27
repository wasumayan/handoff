from __future__ import annotations

from .base import Source
from .claude_code import ClaudeCodeSource
from .codex import CodexSource
from .cursor import CursorSource

SOURCES: dict[str, type[Source]] = {
    ClaudeCodeSource.name: ClaudeCodeSource,
    CodexSource.name: CodexSource,
    CursorSource.name: CursorSource,
}


def get_source(name: str) -> Source:
    key = name.replace("-", "_").lower()
    if key not in SOURCES:
        raise KeyError(f"unknown source {name!r}; known: {', '.join(SOURCES)}")
    return SOURCES[key]()


def detect_sources(cwd: str) -> list[Source]:
    """Return instantiated sources that have any data for cwd, best first.

    A source with session history outranks one that only has instruction files: a repo with a
    CLAUDE.md that Codex has been working in should hand off from Codex.
    """
    ranked = []
    for cls in SOURCES.values():
        s = cls()
        n_sessions = len(s.session_paths(cwd))
        n_docs = len(s.project_docs(cwd))
        if n_sessions or n_docs:
            ranked.append((n_sessions > 0, n_sessions, n_docs, s))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [t[3] for t in ranked]
