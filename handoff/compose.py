"""The one model call: turn a compacted bundle into recipient-shaped guidance.

Everything the model sees is also what `--dry-run` prints. The output is strict JSON with
fixed keys; a bad response is retried once and then abandoned in favour of the --no-llm render.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .backend import Backend, BackendError
from .model import Bundle, Composed
from .render import render_layers

if TYPE_CHECKING:
    from .recipients.base import Profile

KEYS = ["current_state", "decisions", "open_problems", "next_steps", "gotchas",
        "preferences_observed"]


def system_prompt(profile: Profile, source: str) -> str:
    emph = "\n".join(f"- {x}" for x in profile.emphasize)
    drop = "\n".join(f"- {x}" for x in profile.drop)
    return f"""You write handoff notes between coding agents. A session with {source} is ending; the work continues in {profile.display}. You will receive the user's instruction files and a compacted transcript. Produce notes that let {profile.display} continue without re-reading the transcript.

Style for {profile.display}: {profile.style}

Emphasize:
{emph}

Leave out:
{drop}

Rules:
- Only state what the transcript or files support. If something was attempted but not verified, say so. Never invent file names, commands or test results.
- Do not repeat the instruction files; they are given to the recipient separately. Do extract preferences the user expressed in conversation that are NOT already in those files.
- Keep each list item to one or two sentences. Prefer file paths and commands over prose.
- Respond with a single JSON object and nothing else, with exactly these keys:
  "current_state" (string, one paragraph), "decisions" (list), "open_problems" (list),
  "next_steps" (ordered list, most urgent first), "gotchas" (list), "preferences_observed" (list).
"""


def user_content(bundle: Bundle, profile: Profile) -> str:
    files = render_layers(bundle, None, profile)
    parts = [
        "## Identity layer (user-level instruction files)\n\n" + files["identity.md"],
        "## Project layer\n\n" + files["project.md"],
        "## Delta\n\n" + files["delta.md"],
        "## Session transcript (compacted, newest session first)\n\n" + files["session.md"],
    ]
    return "\n\n".join(parts)


def messages_for(bundle: Bundle, profile: Profile) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt(profile, bundle.source)},
        {"role": "user", "content": user_content(bundle, profile)},
    ]


def _parse(text: str) -> dict | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not any(k in obj for k in KEYS):
        return None
    return obj


def _truncated(backend: Backend, msgs: list[dict]) -> str:
    """Why the backend's answer can't be trusted, or "". A server whose context window is smaller
    than the prompt may drop the start of it and still answer; the reported prompt size gives it away."""
    sent = (backend.last_usage or {}).get("prompt_tokens") or 0
    expected = sum(len(m["content"]) for m in msgs) // 4
    if 0 < sent < expected // 2:
        return (f"{backend.host} read only {sent:,} of about {expected:,} prompt tokens: its context "
                "window is too small. Raise it (Ollama on a non-default port: start it with "
                "OLLAMA_CONTEXT_LENGTH=32768).")
    return ""


def compose(bundle: Bundle, profile: Profile, backend: Backend) -> tuple[Composed | None, str]:
    """Returns (composed, warning). composed is None when the model failed twice."""
    msgs = messages_for(bundle, profile)
    last_err = ""
    for attempt in range(2):
        try:
            raw = backend.chat(msgs)
        except BackendError as e:
            return None, str(e)
        cut = _truncated(backend, msgs)
        if cut:
            return None, cut
        obj = _parse(raw)
        if obj is not None:
            c = Composed.from_dict(obj)
            c.model = f"{backend.model}@{backend.host}"
            return c, ""
        last_err = f"model returned non-JSON (attempt {attempt + 1}): {raw[:200]!r}"
        msgs = msgs + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "That was not a single JSON object with the required keys. "
                                        "Reply with only the JSON object."},
        ]
    return None, last_err
