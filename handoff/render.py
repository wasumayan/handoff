"""Render a Bundle (+ optional Composed) into the layered markdown files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import Bundle, Composed, Message, Session

if TYPE_CHECKING:
    from .recipients.base import Profile


def _bullets(items: list[str], empty: str = "_not provided_") -> str:
    return "\n".join(f"- {x}" for x in items) if items else empty


def _is_native(path: str, cwd: str, patterns: list[str]) -> bool:
    rel = path[len(cwd):].lstrip("/") if cwd and path.startswith(cwd) else path
    for pat in patterns:
        if pat.endswith("/") and rel.startswith(pat):
            return True
        if rel == pat:
            return True
    return False


def _doc_section(docs, empty: str, cwd: str = "", elide: list[str] | None = None) -> str:
    if not docs:
        return empty
    out = []
    for d in docs:
        if elide and _is_native(d.path, cwd, elide):
            out.append(f"### `{d.path}`\n\n_(you read this file natively; not duplicated here)_\n")
            continue
        out.append(f"### `{d.path}`\n\n{d.content.rstrip()}\n")
    return "\n".join(out)


def _message_md(m: Message) -> str:
    who = "User" if m.role == "user" else "Assistant"
    parts = [f"**{who}**" + (f" ({m.timestamp[:16].replace('T', ' ')})" if m.timestamp else "")]
    if m.text:
        parts.append(m.text.rstrip())
    for t in m.tool_calls:
        line = f"- `{t.name}` {t.input_summary}".rstrip()
        if t.result_summary:
            tag = "ERROR" if t.is_error else "result"
            one = t.result_summary.replace("\n", " ⏎ ")
            line += f"\n  - {tag}: {one}"
        parts.append(line)
    return "\n\n".join(parts)


def session_md(s: Session) -> str:
    head = [f"## Session `{s.id}`"]
    meta = []
    if s.branch:
        meta.append(f"branch `{s.branch}`")
    if s.started:
        meta.append(f"{s.started[:16].replace('T', ' ')} → {s.ended[:16].replace('T', ' ')}")
    meta.append(f"{len(s.messages)} messages" + (", truncated" if s.truncated else ""))
    head.append("_" + "; ".join(meta) + "_")
    body = []
    if s.summaries:
        body.append("### Source agent's own notes\n\n" + "\n\n".join(s.summaries))
    body.append("### Transcript (compacted)\n\n" + "\n\n---\n\n".join(_message_md(m) for m in s.messages))
    return "\n\n".join(head + body)


def delta_md(bundle: Bundle) -> str:
    d = bundle.delta
    if d is None:
        return "_no git repository detected_"
    if d.first_handoff:
        out = ["First handoff to this recipient: no baseline, so no delta. "
               "Treat everything in project.md and session.md as new."]
        if d.status:
            out.append("Uncommitted changes right now:\n```\n" + d.status + "\n```")
        return "\n\n".join(out)
    out = [f"Since the last handoff to **{bundle.recipient}** "
           f"(baseline `{d.baseline_sha[:10]}`{', ' + d.baseline_at if d.baseline_at else ''}; "
           f"now `{d.head_sha[:10]}` on `{d.branch}`):"]
    out.append("Commits:\n" + (_bullets([f"`{c}`" for c in d.commits], "_none_")))
    if d.diff_stat:
        out.append("Diff stat:\n```\n" + d.diff_stat + "\n```")
    if d.status:
        out.append("Uncommitted changes right now:\n```\n" + d.status + "\n```")
    out.append(f"New source-agent messages since baseline: {d.new_messages}")
    return "\n\n".join(out)


def next_md(composed: Composed | None, bundle: Bundle) -> str:
    card = f"## From the session log\n\n{bundle.card}" if bundle.card else ""
    if composed is None and card:
        return "_Generated without a model (`--no-llm`): copied from the session log._\n\n" + card
    if composed is None:
        last = ""
        for s in bundle.sessions[:1]:
            for m in reversed(s.messages):
                if m.role == "user" and m.text:
                    last = m.text
                    break
        return (
            "_Generated without a model (`--no-llm`). No synthesized next steps._\n\n"
            "Last thing the user asked for:\n\n> " + last.replace("\n", "\n> ")
            if last
            else "_Generated without a model (`--no-llm`). No synthesized next steps._"
        )
    return "\n\n".join(
        [
            "## Current state\n\n" + (composed.current_state or "_not provided_"),
            "## Decisions already made\n\n" + _bullets(composed.decisions),
            "## Open problems\n\n" + _bullets(composed.open_problems),
            "## Next steps\n\n" + "\n".join(f"{i+1}. {x}" for i, x in enumerate(composed.next_steps))
            if composed.next_steps
            else "## Next steps\n\n_not provided_",
            "## Gotchas\n\n" + _bullets(composed.gotchas),
            "## Preferences observed in the session\n\n" + _bullets(composed.preferences_observed),
            *([card.replace("## From the session log", "## From the session log (not model-written)")]
              if card else []),
        ]
    )


def render_layers(bundle: Bundle, composed: Composed | None, profile: Profile,
                  elide: list[str] | None = None, layers_dir: str = ".handoff/latest") -> dict[str, str]:
    files: dict[str, str] = {}
    files["identity.md"] = (
        "# Identity and lasting preferences\n\n"
        "_User-level instruction files from the source agent. These apply to every project._\n\n"
        + _doc_section(bundle.identity, "_no user-level instruction file found_")
    )
    files["project.md"] = (
        "# Project context\n\n"
        "_Project-level instruction files and memory from the source agent, plus repo snapshot._\n\n"
        + _doc_section(bundle.project, "_no project-level instruction file found_", bundle.cwd, elide)
    )
    files["session.md"] = (
        "# Session history\n\n"
        f"_From {bundle.source}. Newest session first. Tool output is clipped._\n\n"
        + ("\n\n".join(session_md(s) for s in bundle.sessions) or "_no sessions found_")
    )
    files["delta.md"] = "# What changed\n\n" + delta_md(bundle)
    files["next.md"] = "# Where things stand and what to do next\n\n" + next_md(composed, bundle)

    mode = f"composed by `{composed.model}`" if composed and composed.model else "no model used"
    header = (
        f"# Handoff: {bundle.source} → {profile.display}\n\n"
        f"_Generated {bundle.generated_at} by `handoff` ({mode}). "
        f"Project: `{bundle.cwd}`. Full layers in `{layers_dir}/`._\n"
    )
    if bundle.notes:
        header += "\n" + "\n".join(f"> note: {n}" for n in bundle.notes) + "\n"
    order = ["next.md", "delta.md", "project.md", "identity.md", "session.md"]
    combined = [header] + [files[k] for k in order]
    files["HANDOFF.md"] = "\n\n".join(combined).rstrip() + "\n"
    pointer = (f"_The transcript (`session.md`) and the instruction layers (`project.md`, `identity.md`) "
               f"are in `{layers_dir}/`. Read them only if the above is not enough._")
    files["BRIEF.md"] = "\n\n".join([header, files["next.md"], files["delta.md"], pointer]) + "\n"
    return files
