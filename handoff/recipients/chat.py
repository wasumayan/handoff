from pathlib import Path

from .base import Recipient


class ChatRecipient(Recipient):
    """A plain chat model with no file conventions. Output is one paste-able prompt."""

    name = "chat"

    def install_path(self, cwd: str) -> Path:
        return Path(cwd) / ".handoff" / "PASTE_ME.md"

    def install(self, cwd: str, files: dict[str, str]) -> Path:
        path = self.install_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files["HANDOFF.md"])
        return path
