"""Deterministic, budgeted compaction. No model involved.

Rules (in priority order):
  1. Never drop the first human turn or the last two messages of the newest session.
  2. Drop whole older sessions before trimming the newest one.
  3. Within a session: collapse repeated identical tool calls, clip tool calls, then drop
     successful tool calls (failures stay: they say what did not work), then shorten assistant
     prose (the agent's own reports summarize its tool output), then drop assistant messages, and
     only then human turns. Short human corrections ("use the staging server",
     "don't stop after step 1") carry the preferences the next agent most needs; tool lines
     were 90% of a real Codex session.
  4. Instruction docs (identity/project) are trimmed last and only from the end of each doc,
     because the recipient will usually read the originals from disk anyway.
  5. If the protected messages and the source agent's own summaries still do not fit, their middles
     are cut, so the budget holds.
"""

from __future__ import annotations

from dataclasses import replace

from .model import Bundle, Message, Session

ASSISTANT_TRIM_CHARS = 600
ERROR_KEEP_CHARS = 200
DOC_MIN_CHARS = 1500
CLIP_MIN_CHARS = 400  # what a protected message or summary keeps at least when its middle is cut


def _collapse_repeats(m: Message) -> Message:
    """The same tool call run several times in one turn (re-running tests, polling) keeps only its
    last run, marked with the count."""
    counts: dict[tuple[str, str], int] = {}
    for t in m.tool_calls:
        counts[(t.name, t.input_summary)] = counts.get((t.name, t.input_summary), 0) + 1
    if all(n == 1 for n in counts.values()):
        return m
    last = {(t.name, t.input_summary): t for t in m.tool_calls}
    tools = [replace(t, input_summary=f"{t.input_summary} (run {counts[k]}x)" if counts[k] > 1
                     else t.input_summary)
             for k, t in last.items()]
    return replace(m, tool_calls=tools)


def _clip_middle(text: str, keep: int) -> str:
    if len(text) <= keep:
        return text
    half = max(1, (keep - 40) // 2)
    return f"{text[:half].rstrip()}\n[handoff: {len(text) - 2 * half} chars cut]\n{text[-half:].lstrip()}"


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


def compact_session(s: Session, budget: int, keep_users: bool = False) -> Session:
    """keep_users=True never drops a human turn (long ones may still lose their middle)."""
    if s.chars <= budget:
        return s
    msgs: list[Message | None] = [_collapse_repeats(m) for m in s.messages]
    summaries = list(s.summaries)
    n = len(msgs)
    protected = set()
    first_user = next((i for i, m in enumerate(s.messages) if m.role == "user"), None)
    if first_user is not None:
        protected.add(first_user)
    protected.update(range(max(0, n - 2), n))

    def total() -> int:
        return sum(m.chars for m in msgs if m is not None) + sum(len(x) for x in summaries)

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

    def clip_rest() -> None:
        """Last resort: cut the middles of protected messages and summaries, largest first."""
        while total() > budget:
            sizes = [(len(m.text), "m", i) for i, m in enumerate(msgs) if m is not None]
            sizes += [(len(x), "s", i) for i, x in enumerate(summaries)]
            size, kind, i = max(sizes, default=(0, "", 0))
            keep = max(CLIP_MIN_CHARS, size - (total() - budget))
            if size <= keep:
                return
            if kind == "m":
                msgs[i] = replace(msgs[i], text=_clip_middle(msgs[i].text, keep))
            else:
                summaries[i] = _clip_middle(summaries[i], keep)

    # Each step costs the recipient less than the next one.
    unprotected = [i for i in range(n) if i not in protected]
    trim(1, range(n))  # clip tool lines everywhere
    trim(2, unprotected)  # drop successful tool calls
    trim(3, unprotected)  # shorten assistant prose
    dropped = drop(users=False)
    trim(2, sorted(protected))
    if not keep_users:
        dropped += drop(users=True)
    if dropped:
        summaries.insert(0, f"[handoff: {dropped} earlier message(s) dropped to fit budget]")
    clip_rest()
    return replace(s, messages=[m for m in msgs if m is not None], summaries=summaries, truncated=True)


def compact(bundle: Bundle, budget: int, keep_users: bool = False) -> Bundle:
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
        b.sessions[0] = compact_session(b.sessions[0], session_budget, keep_users)
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
