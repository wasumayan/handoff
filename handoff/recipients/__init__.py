from __future__ import annotations

from .base import Profile, Recipient, load_profile
from .chat import ChatRecipient
from .claude_code import ClaudeCodeRecipient
from .codex import CodexRecipient
from .cursor import CursorRecipient

RECIPIENTS: dict[str, type[Recipient]] = {
    CodexRecipient.name: CodexRecipient,
    ClaudeCodeRecipient.name: ClaudeCodeRecipient,
    CursorRecipient.name: CursorRecipient,
    ChatRecipient.name: ChatRecipient,
}


def get_recipient(name: str) -> Recipient:
    key = name.replace("-", "_").lower()
    aliases = {"claude": "claude_code", "openai": "chat", "chatgpt": "chat", "local": "chat"}
    key = aliases.get(key, key)
    if key not in RECIPIENTS:
        raise KeyError(f"unknown recipient {name!r}; known: {', '.join(RECIPIENTS)}")
    return RECIPIENTS[key]()


__all__ = ["Profile", "Recipient", "get_recipient", "load_profile", "RECIPIENTS"]
