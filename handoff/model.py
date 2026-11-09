"""Data model for a handoff bundle.

The hierarchy is deliberate and is never flattened:

    identity  -> who the user is, lasting preferences (user-level instruction files)
    project   -> this repository / project (project-level instruction files, memory)
    sessions  -> what happened in recent sessions of the source agent
    delta     -> what changed since the recipient last saw this project
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Doc:
    """A file worth of instructions or memory, with where it came from."""

    path: str
    content: str
    origin: str  # e.g. "claude_code", "codex", "cursor", "git"

    @property
    def chars(self) -> int:
        return len(self.content)


@dataclass
class ToolCall:
    name: str
    input_summary: str
    result_summary: str = ""
    is_error: bool = False


@dataclass
class Message:
    role: str  # "user" | "assistant"
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    timestamp: str = ""
    uuid: str = ""

    @property
    def chars(self) -> int:
        return len(self.text) + sum(
            len(t.name) + len(t.input_summary) + len(t.result_summary) for t in self.tool_calls
        )


@dataclass
class Session:
    id: str
    source: str
    cwd: str = ""
    branch: str = ""
    started: str = ""
    ended: str = ""
    path: str = ""
    messages: list[Message] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)  # the source agent's own compaction notes
    truncated: bool = False  # set by compact.py when it dropped content

    @property
    def chars(self) -> int:
        return sum(m.chars for m in self.messages) + sum(len(s) for s in self.summaries)


@dataclass
class Delta:
    baseline_sha: str = ""
    baseline_at: str = ""
    head_sha: str = ""
    branch: str = ""
    commits: list[str] = field(default_factory=list)
    diff_stat: str = ""
    status: str = ""
    new_messages: int = 0
    first_handoff: bool = False


@dataclass
class Bundle:
    source: str
    recipient: str
    cwd: str
    identity: list[Doc] = field(default_factory=list)
    project: list[Doc] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    delta: Delta | None = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    notes: list[str] = field(default_factory=list)  # warnings surfaced to the user/recipient
    card: str = ""  # deterministic state card of the newest session, built before compaction

    @property
    def chars(self) -> int:
        return (
            sum(d.chars for d in self.identity)
            + sum(d.chars for d in self.project)
            + sum(s.chars for s in self.sessions)
        )


@dataclass
class Composed:
    """Output of the LLM step. Every field is optional; missing ones render as 'not provided'."""

    current_state: str = ""
    decisions: list[str] = field(default_factory=list)
    open_problems: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)
    preferences_observed: list[str] = field(default_factory=list)
    model: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> Composed:
        def lst(k: str) -> list[str]:
            v = d.get(k, [])
            if isinstance(v, str):
                v = [v]
            return [str(x).strip() for x in v if str(x).strip()]

        return cls(
            current_state=str(d.get("current_state", "")).strip(),
            decisions=lst("decisions"),
            open_problems=lst("open_problems"),
            next_steps=lst("next_steps"),
            gotchas=lst("gotchas"),
            preferences_observed=lst("preferences_observed"),
        )
