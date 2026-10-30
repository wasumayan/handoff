"""Repository state via git. Always on when cwd is inside a git work tree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..model import Delta

# `git status` otherwise refreshes .git/index; reading a repo must not write to it.
_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def _git(cwd: str, *args: str, max_chars: int = 20_000) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20, check=False,
            env=_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = r.stdout if r.returncode == 0 else ""
    return out[:max_chars]


def is_repo(cwd: str) -> bool:
    return _git(cwd, "rev-parse", "--is-inside-work-tree").strip() == "true"


def repo_root(cwd: str) -> str:
    return _git(cwd, "rev-parse", "--show-toplevel").strip() or str(Path(cwd).resolve())


def head_sha(cwd: str) -> str:
    return _git(cwd, "rev-parse", "HEAD").strip()


def branch(cwd: str) -> str:
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()


def snapshot(cwd: str, recent: int = 15) -> str:
    """Human-readable repo snapshot for the project layer."""
    if not is_repo(cwd):
        return ""
    parts = [f"branch: {branch(cwd) or '(detached/unborn)'}"]
    log = _git(cwd, "log", "--oneline", f"-{recent}")
    if log.strip():
        parts.append("recent commits:\n" + log.rstrip())
    status = _git(cwd, "status", "--short")
    if status.strip():
        parts.append("uncommitted changes:\n" + status.rstrip())
    tracked = _git(cwd, "ls-files", max_chars=6000)
    if tracked.strip():
        files = tracked.split("\n")
        top = sorted({f.split("/")[0] for f in files if f})
        parts.append(f"top-level entries ({len(files)} tracked files): " + ", ".join(top[:40]))
    return "\n\n".join(parts)


def delta(cwd: str, baseline_sha: str, baseline_at: str = "") -> Delta:
    d = Delta(baseline_sha=baseline_sha, baseline_at=baseline_at)
    if not is_repo(cwd):
        return d
    d.head_sha = head_sha(cwd)
    d.branch = branch(cwd)
    d.status = _git(cwd, "status", "--short").rstrip()
    if not baseline_sha:
        d.first_handoff = True
        return d
    if not _git(cwd, "cat-file", "-e", baseline_sha).strip() and _git(
        cwd, "rev-parse", "--verify", "--quiet", baseline_sha
    ).strip() == "":
        d.first_handoff = True  # baseline commit no longer exists (rebase, different clone)
        return d
    log = _git(cwd, "log", "--oneline", f"{baseline_sha}..HEAD")
    d.commits = [ln for ln in log.split("\n") if ln.strip()]
    d.diff_stat = _git(cwd, "diff", "--stat", baseline_sha).rstrip()
    return d
