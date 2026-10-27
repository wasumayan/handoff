from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ..model import Bundle, Composed
from ..render import render_layers

BEGIN = "<!-- handoff:begin -->"
END = "<!-- handoff:end -->"


@dataclass
class Profile:
    name: str
    display: str
    instruction_file: str
    context_budget: int
    style: str
    emphasize: list[str] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    notes: str = ""
    install_header: str = ""
    native_docs: list[str] = field(default_factory=list)  # files the recipient reads on its own


def load_profile(name: str) -> Profile:
    pkg = resources.files(__package__) / "profiles" / f"{name}.toml"
    data = tomllib.loads(pkg.read_text())
    return Profile(**data)


def splice_block(existing: str, block: str, header: str = "") -> str:
    """Insert or replace the marker-delimited block. Content outside markers is untouched."""
    payload = f"{BEGIN}\n{block.rstrip()}\n{END}\n"
    if BEGIN in existing and END in existing:
        pre, rest = existing.split(BEGIN, 1)
        _, post = rest.split(END, 1)
        return pre + payload + post.lstrip("\n") if post.strip() else pre + payload
    if existing and not existing.endswith("\n"):
        existing += "\n"
    sep = "\n" if existing.strip() else ""
    head = (header.rstrip() + "\n\n") if header and not existing.strip() else ""
    return head + existing + sep + payload


class Recipient:
    name: str = "base"

    def __init__(self) -> None:
        self.profile = load_profile(self.name)

    def render(self, bundle: Bundle, composed: Composed | None) -> dict[str, str]:
        """Return {filename: content}; must include HANDOFF.md."""
        self._last = (bundle, composed)
        return render_layers(bundle, composed, self.profile)

    def install_path(self, cwd: str) -> Path:
        return Path(cwd) / self.profile.instruction_file

    def install_block(self, files: dict[str, str]) -> str:
        """What goes into the instruction file. Files the recipient reads natively (its own
        CLAUDE.md / AGENTS.md) are elided to a reference so they are not duplicated into themselves."""
        last = getattr(self, "_last", None)
        if last is None or not self.profile.native_docs:
            return files["HANDOFF.md"]
        bundle, composed = last
        return render_layers(bundle, composed, self.profile, elide=self.profile.native_docs)["HANDOFF.md"]

    def install(self, cwd: str, files: dict[str, str]) -> Path:
        path = self.install_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text() if path.exists() else ""
        path.write_text(splice_block(existing, self.install_block(files), self.profile.install_header))
        return path
