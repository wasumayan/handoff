"""Deterministic, budgeted compaction. No model involved.

Rules (in priority order):
  1. Never drop the first human turn or the last two messages of the newest session.
  2. Drop whole older sessions before trimming the newest one.
  3. Within a session, trim oldest messages first: clip tool calls, then drop successful tool
     calls (failures stay: they say what did not work), then shorten assistant prose (the
     agent's own reports summarize its tool output), then drop assistant messages, and only
     then human turns. Short human corrections ("use the staging server",
     "don't stop after step 1") carry the preferences the next agent most needs; tool lines
     were 90% of a real Codex session.
  4. Instruction docs (identity/project) are trimmed last and only from the end of each doc,
     because the recipient will usually read the originals from disk anyway.
"""

from __future__ import annotations

from dataclasses import replace

from .model import Bundle, Message, Session

ASSISTANT_TRIM_CHARS = 600
ERROR_KEEP_CHARS = 200
DOC_MIN_CHARS = 1500


def _trim_message(m: Message, level: int) -> Message:
    """level 1: clip tool inputs, keep only error results. level 2: drop successful tool calls,
    leaving a count. level 3: shorten assistant prose."""
    text = m.text
    tools = m.tool_calls
    if level >= 1:
        tools = [replace(t, input_summary=t.input_summary[:80],
                         result_summary=t.result_summary[:ERROR_KEEP_CHARS] if t.is_error else "")
                 for t in tools]
    if level >= 2 and any(not t.is_error for t in tools):
        gone = sum(not t.is_error for t in tools)
        tools = [t for t in tools if t.is_error]
        note = f"[handoff: {gone} tool call(s) omitted]"
    else:
        note = ""
    if m.role == "assistant" and level >= 3 and len(text) > ASSISTANT_TRIM_CHARS:
        text = text[: ASSISTANT_TRIM_CHARS - 1].rstrip() + "…"
    return replace(m, text=(text + "\n" + note).strip() if note else text, tool_calls=tools)


def compact_session(s: Session, budget: int) -> Session:
    if s.chars <= budget:
        return s
    msgs: list[Message | None] = list(s.messages)
    n = len(msgs)
    protected = set()
    first_user = next((i for i, m in enumerate(s.messages) if m.role == "user"), None)
    if first_user is not None:
        protected.add(first_user)
    protected.update(range(max(0, n - 2), n))

    def total() -> int:
        return sum(m.chars for m in msgs if m is not None) + sum(len(x) for x in s.summaries)

    def trim(level: int, indices) -> None:
        for i in indices:
            if total() <= budget:
                return
            if msgs[i] is not None:
                msgs[i] = _trim_message(msgs[i], level)

    def drop(users: bool) -> int:
        k = 0
        for i in range(n):
            if total() <= budget:
                break
            m = msgs[i]
            if i not in protected and m is not None and (m.role == "user") == users:
                msgs[i] = None
                k += 1
        return k

    # Each step costs the recipient less than the next one.
    unprotected = [i for i in range(n) if i not in protected]
    trim(1, range(n))  # clip tool lines everywhere
    trim(2, unprotected)  # drop successful tool calls
    trim(3, unprotected)  # shorten assistant prose
    dropped = drop(users=False)
    trim(2, sorted(protected))
    dropped += drop(users=True)
    out = [m for m in msgs if m is not None]
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
