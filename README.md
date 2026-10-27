# handoff

Move coding-agent context from one model or app to another, in the form the recipient reads best.

Built for one concrete situation: Claude Code credits run out mid-task, Codex (or a local model, or
Cursor) is available, and "paste the old chat" is not enough. The next agent needs the project's
state, what changed, the user's standing preferences, and what to do next, in its own instruction
file format.

**Honest framing.** The output is markdown files. The work is in (a) parsing session formats a
human cannot read (a short Claude Code session is ~500 KB of JSONL), (b) keeping identity /
project / session / delta as separate layers instead of one blob, (c) computing what changed since
the recipient last saw the project, and (d) writing into the recipient's native instruction file
without clobbering what is already there. Whether the model-adapted step beats a plain structured
dump is an open question; `eval/` exists to answer it, not to assume it.

## Install

```bash
uv tool install git+https://github.com/wasumayan/handoff   # or: pipx install git+...
# or from a checkout:
uv tool install .
```

Python 3.11+, no runtime dependencies.

## Use

```bash
cd ~/code/myproject            # the directory the source agent was working in
handoff sources                # what state exists here (Claude Code, Codex, Cursor)
handoff create --to codex --install
```

That reads the newest Claude Code session for this directory plus `~/.claude/CLAUDE.md`,
`CLAUDE.md`, `.claude/rules/`, project memory, and the git state; writes
`.handoff/<timestamp>-codex/{next,delta,project,identity,session}.md` and a combined `HANDOFF.md`;
and splices `HANDOFF.md` into `AGENTS.md` between `<!-- handoff:begin -->` / `<!-- handoff:end -->`
markers. Content outside the markers is never touched. Re-running replaces only the block.

Recipients: `codex` (AGENTS.md), `claude` (CLAUDE.md), `cursor` (`.cursor/rules/handoff.mdc`),
`chat` (one paste-able prompt for ChatGPT / a local chat UI).

Sources (auto-detected, or `--from`): `claude_code`, `codex`, `cursor` (rules files only).

### With or without a model

```bash
handoff create --to codex --no-llm                      # deterministic, nothing leaves the machine
handoff create --to codex --backend ollama --model qwen3 # local
HANDOFF_BASE_URL=https://api.openai.com/v1 HANDOFF_MODEL=gpt-5 HANDOFF_API_KEY=... handoff create --to codex
handoff create --to codex --dry-run                     # print exactly what would be sent, write nothing
```

Any OpenAI-compatible `/v1/chat/completions` endpoint works (OpenAI, OpenRouter, Ollama, LM Studio,
vLLM). With no backend configured you get the `--no-llm` output. The model step writes only
`next.md` (current state, decisions, open problems, next steps, gotchas, preferences observed in
conversation). Everything else is deterministic.

Before anything is sent or written, obvious secrets (API keys, tokens, `*_SECRET=` lines, private
keys) are replaced with `[REDACTED:...]`. This is a regex pass, not a guarantee. Use `--dry-run`
if the session touched anything sensitive.

### Delta

`.handoff/state.json` records the commit and last message seen at each handoff, per recipient. The
next run's `delta.md` lists commits, diff stat, uncommitted changes and new messages since then. The
first run says so and has no delta. Add `.handoff/` to `.gitignore` if you do not want this shared.

### Budgets and compaction

Each recipient profile has a character budget (Cursor's is small because `alwaysApply` rules cost
context on every message). Over budget, `handoff` drops older sessions first, then trims the newest
session oldest-first (shorten assistant prose, then drop tool detail, then drop the message), and
only then trims instruction files from the end. The first human turn and the last two messages are
never dropped. `--budget N` overrides.

## Recipient profiles

`handoff/recipients/profiles/*.toml` hold every claim about what a recipient wants: instruction
file, budget, style, what to emphasize, what to drop. They are data, not code, so they are easy to
argue with. `handoff profiles -v` prints them. There is deliberately no runtime web lookup of model
cards: that would make output nondeterministic and untestable for a small, slowly changing set of
facts.

## Layout of a handoff

```
next.md      where things stand, what to do next (model-written, or last human ask if --no-llm)
delta.md     what changed since the last handoff to this recipient
project.md   project-level instruction files, memory, git snapshot
identity.md  user-level instruction files (apply to every project)
session.md   compacted transcript: human turns in full, tool calls as one line each, errors kept
HANDOFF.md   all of the above, next.md first
```

## Evaluation

See `eval/README.md`. Three fixture scenarios, each handed off at a midpoint, continued by the
recipient under three arms (raw transcript, `--no-llm` structured dump, model-adapted), scored by
hidden tests plus a short rubric. Small n; it is a smoke test for "is adaptation worth it", not a
benchmark.

## Formats this depends on

These are not public APIs and drift between releases.

- Claude Code: `~/.claude/projects/<cwd with non-alphanumerics as "-">/<session>.jsonl`. Records of
  type `user` / `assistant` carry `message.content` as a string or blocks (`text`, `thinking`,
  `tool_use`, `tool_result`). `isSidechain` marks subagent transcripts, `isMeta` harness-injected
  turns, `isCompactSummary` Claude's own compaction notes. Verified against 2.1.x.
- Codex CLI: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` with `session_meta` /
  `response_item` / `event_msg` records, plus an older flat shape. Written from documentation and
  examples, not yet verified against a live install. Unknown record types are ignored.
- Cursor: rules files only. Chat history is in an undocumented SQLite schema and is not read.

## Not in scope

App-to-app transfers (dating profiles, playlists, calendars). Different access problem, different
trust problem.

## Development

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
pytest -q && ruff check .
```
