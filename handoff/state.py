"""Per-project handoff state, used to compute the delta on the next run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

STATE_FILE = ".handoff/state.json"


def _path(cwd: str) -> Path:
    return Path(cwd) / STATE_FILE


def load(cwd: str) -> dict:
    p = _path(cwd)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def baseline(cwd: str, recipient: str) -> dict:
    return load(cwd).get("recipients", {}).get(recipient, {})


def record(cwd: str, recipient: str, *, sha: str, source: str, session_id: str,
           last_message_uuid: str, output_dir: str) -> None:
    p = _path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load(cwd)
    data.setdefault("version", 1)
    data.setdefault("recipients", {})[recipient] = {
        "last_sha": sha,
        "source": source,
        "last_session_id": session_id,
        "last_message_uuid": last_message_uuid,
        "output_dir": output_dir,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    p.write_text(json.dumps(data, indent=2) + "\n")
