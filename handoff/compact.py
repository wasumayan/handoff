"""Deterministic, budgeted compaction. No model involved.

Rules (in priority order):
  1. Never drop the first human turn or the last two messages of the newest session.
  2. Drop whole older sessions before trimming the newest one.
  3. Within a session, trim oldest messages first: first shorten assistant prose, then drop
     tool-call detail, then drop the message.
  4. Instruction docs (identity/project) are trimmed last and only from the end of each doc,
     because the recipient will usually read the originals from disk anyway.
"""

from __future__ import annotations

from dataclasses import replace

from .model import Bundle, Message, Session

ASSISTANT_TRIM_CHARS = 600
DOC_MIN_CHARS = 1500


def _trim_message(m: Message, level: int) -> Message:
    """level 1: shorten assistant prose. level 2: also drop tool results/inputs."""
    text = m.text
    if m.role == "assistant" and level >= 1 and len(text) > ASSISTANT_TRIM_CHARS:
        text = text[: ASSISTANT_TRIM_CHARS - 1].rstrip() + "…"
    tools = m.tool_calls
    if level >= 2:
        tools = [replace(t, input_summary=t.input_summary[:80], result_summary="") for t in tools]
    return replace(m, text=text, tool_calls=tools)


def compact_session(s: Session, budget: int) -> Session:
    if s.chars <= budget:
        return s
    msgs = list(s.messages)
    n = len(msgs)
    protected = set()
    first_user = next((i for i, m in enumerate(msgs) if m.role == "user"), None)
    if first_user is not None:
        protected.add(first_user)
    protected.update(range(max(0, n - 2), n))

    def total() -> int:
        return sum(m.chars for m in msgs if m is not None) + sum(len(x) for x in s.summaries)

    for level in (1, 2):
        for i in range(n):
            if total() <= budget:
                break
            if i in protected:
                continue
            msgs[i] = _trim_message(msgs[i], level)
    dropped = 0
    i = 0
    while total() > budget and i < n:
        if i not in protected:
            msgs[i] = None  # type: ignore[call-overload]
            dropped += 1
        i += 1
    out = [m for m in msgs if m is not None]
    # Protected messages may still be over budget; trim their tool detail as a last resort.
    if sum(m.chars for m in out) > budget:
        out = [_trim_message(m, 2) if m.role == "assistant" else m for m in out]
    new = replace(s, messages=out, truncated=True)
    if dropped:
        new.summaries = [f"[handoff: {dropped} earlier message(s) dropped to fit budget]"] + list(
            s.summaries
        )
    return new


def compact(bundle: Bundle, budget: int) -> Bundle:
    """Fit the bundle into roughly `budget` characters. Mutates a copy."""
    b = replace(bundle, sessions=list(bundle.sessions), identity=list(bundle.identity),
                project=list(bundle.project))
    if b.chars <= budget:
        return b
    # 2. drop older sessions
    while len(b.sessions) > 1 and b.chars > budget:
        gone = b.sessions.pop()  # sessions are newest-first
        b.notes.append(f"dropped older session {gone.id} ({gone.chars} chars) to fit budget")
    # 3. trim newest session, leaving room for docs
    docs_chars = sum(d.chars for d in b.identity) + sum(d.chars for d in b.project)
    if b.sessions:
        session_budget = max(budget // 2, budget - docs_chars)
        b.sessions[0] = compact_session(b.sessions[0], session_budget)
    # 4. trim docs from the end, largest first, never below DOC_MIN_CHARS
    for layer in (b.project, b.identity):
        for idx, d in sorted(enumerate(layer), key=lambda t: -t[1].chars):
            if b.chars <= budget:
                return b
            over = b.chars - budget
            keep = max(DOC_MIN_CHARS, d.chars - over)
            if keep < d.chars:
                layer[idx] = replace(
                    d, content=d.content[:keep].rstrip() + "\n\n[handoff: truncated; read the original]"
                )
    return b
