"""Variants: the different things a recipient can be handed. See eval/variants.toml."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from handoff.recipients import get_recipient
from handoff.recipients.base import BEGIN, END

from .scenario import ROOT, Scenario, Work, raw_transcript

VARIANTS_FILE = ROOT / "eval" / "variants.toml"
REFS = ROOT / "eval" / ".cache" / "refs"


@dataclass
class Variant:
    name: str
    kind: str
    args: list[str] = field(default_factory=list)
    ref: str = ""
    checkout: str = ""
    needs_backend: bool = False
    description: str = ""


def load_variants(path: Path = VARIANTS_FILE, only: list[str] | None = None) -> list[Variant]:
    data = tomllib.loads(path.read_text())
    out = [Variant(name=k, **v) for k, v in data.items()]
    for v in out:
        if v.kind not in ("none", "raw", "ideal", "handoff"):
            raise ValueError(f"variant {v.name}: unknown kind {v.kind!r}")
    if only:
        missing = set(only) - {v.name for v in out}
        if missing:
            raise ValueError(f"unknown variant(s): {', '.join(sorted(missing))}")
        out = [v for v in out if v.name in only]
    return out


def tool_checkout(v: Variant) -> Path:
    """The copy of the handoff package this variant runs."""
    if v.checkout:
        return Path(v.checkout).expanduser().resolve()
    if not v.ref:
        return ROOT
    dest = REFS / v.ref.replace("/", "_")
    if not (dest / "handoff" / "cli.py").is_file():
        REFS.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "worktree", "add", "--detach", "--force", str(dest), v.ref],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"variant {v.name}: cannot check out {v.ref}: {r.stderr.strip()}")
    return dest


@dataclass
class Built:
    text: str  # exactly what was added to the recipient's instruction file ("" for none)
    seconds: float = 0.0
    stderr: str = ""
    error: str = ""


def installed_block(instruction_file: Path) -> str:
    if not instruction_file.is_file():
        return ""
    s = instruction_file.read_text()
    if BEGIN not in s or END not in s:
        return ""
    return s.split(BEGIN, 1)[1].split(END, 1)[0].strip()


def build(v: Variant, sc: Scenario, work: Work, source: str, profile: str,
          backend_env: dict[str, str] | None = None, timeout: int = 600) -> Built:
    """Produce the variant's handoff and install it into the recipient's instruction file in
    work.repo. The source session is deleted afterwards by the caller."""
    rcp = get_recipient(profile)
    target = rcp.install_path(str(work.repo))
    if v.kind == "none":
        return Built(text="")
    if v.kind in ("raw", "ideal"):
        text = raw_transcript(work.session_file, source) if v.kind == "raw" else sc.ideal
        rcp.install(str(work.repo), {"HANDOFF.md": text})
        return Built(text=installed_block(target))
    out_dir = work.root / f"handoff-out-{v.name}"
    cmd = [sys.executable, "-m", "handoff.cli", "create", "--to", profile, "--path", str(work.repo),
           "--from", source, "--install", "--out", str(out_dir), *v.args]
    env = {**os.environ, "PYTHONPATH": str(tool_checkout(v)), "HANDOFF_HOME_OVERRIDE": str(work.source_home)}
    for k in ("HANDOFF_BASE_URL", "HANDOFF_MODEL", "HANDOFF_API_KEY"):
        env.pop(k, None)
    if v.needs_backend:
        if not backend_env:
            return Built(text="", error="variant needs a backend (HANDOFF_BASE_URL / HANDOFF_MODEL)")
        env.update(backend_env)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=work.root, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Built(text="", seconds=timeout, error="handoff create timed out")
    secs = round(time.time() - t0, 2)
    # Versions before the --out fix also write .handoff/ into the repo; the agent must see only the
    # installed block, as with every other variant.
    shutil.rmtree(work.repo / ".handoff", ignore_errors=True)
    if r.returncode:
        return Built(text="", seconds=secs, stderr=r.stderr, error=f"handoff create exit {r.returncode}")
    return Built(text=installed_block(target), seconds=secs, stderr=r.stderr)
