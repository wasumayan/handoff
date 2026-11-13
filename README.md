# handoff

Hand a coding session from one AI agent to another without losing the thread.

You hit your Claude Code usage limit halfway through a task. Codex, Cursor or a local model is
available, but it starts from zero: it doesn't know what you asked for, what was tried and failed,
which corrections you made, or what's half-done. `handoff` reads the old session from disk and
writes that context into the file the next agent loads at startup (`AGENTS.md` for Codex,
`CLAUDE.md` for Claude Code, a rules file for Cursor). Then you start the new agent and keep going.

It works offline with no model and no API key. Python 3.11+, no dependencies.

## Quick start: Claude Code → Codex

```bash
# 1. Install (uv: https://docs.astral.sh/uv/; pipx works too)
uv tool install git+https://github.com/wasumayan/handoff

# 2. Go to the project the old agent was working in, and check what handoff finds
cd ~/code/myproject
handoff sources -v

# 3. Write the handoff into AGENTS.md
handoff create --to codex --install

# 4. Start Codex and point it at the handoff
codex "Read the handoff section in AGENTS.md and continue the work from where it stopped."
```

Step 2 lists the recent sessions for this directory, newest first, with each one's first request.
`create` uses the newest. To pick a different one, pass `--session <id>`, for example
`handoff create --to codex --install --session 54cbb32f`.

## What happens after the handoff

**Codex** reads `AGENTS.md` from the repo root at the start of every session, so running `codex`
in the project is enough to load it. It still waits for a prompt, so start with one like the
example above. By default Codex reads only the first 32 KiB of `AGENTS.md`, which is why the Codex
handoff is capped at 24,000 characters.

**Claude Code** (`--to claude`) reads `CLAUDE.md` the same way:

```bash
handoff create --from codex --to claude --install
claude "Read the handoff section in CLAUDE.md and continue the work from where it stopped."
```

**Cursor** (`--to cursor`) gets `.cursor/rules/handoff.mdc` with `alwaysApply: true`, so every
Agent chat in the project sees it. Open a chat and ask it to continue.

**A chat model with no file access** (ChatGPT, a local chat UI): run
`handoff create --to chat --stdout` and paste the output.

### What the new agent sees

The installed block sits between `<!-- handoff:begin -->` and `<!-- handoff:end -->`. Anything
else already in the file stays as it was, and running `create` again replaces only the block. In
order:

1. **Where things stand:** the latest request, files edited, the last test run and its result, and
   errors that were never fixed, all copied from the session log. With a model configured, its
   summary of decisions, open problems and next steps comes first.
2. **What changed** since the last handoff to this agent: new commits, the diff stat and
   uncommitted changes.
3. **Project context:** the old agent's instruction files and memory, plus a git snapshot.
4. **Your user-level preferences** (`~/.claude/CLAUDE.md`).
5. **The transcript, compacted:** your messages in full, each tool call on one line, and errors
   kept. Older material is trimmed first when it doesn't fit.

If the new agent's context is tight, `--brief` installs only items 1 and 2 plus a pointer to the
full files.

### When you're done

- **Don't commit the block.** It contains your session (secrets redacted, see below). Delete
  everything between the two markers, or delete the file if `handoff` created it. Add `.handoff/`
  to `.gitignore`.
- **Switching back** works the same way in reverse, for example
  `handoff create --from codex --to claude --install` once your Claude Code usage resets. The
  "what changed" section then lists what Codex did since the last handoff to Claude.

## Options

```bash
handoff create --to codex --dry-run         # print what would be sent to a model; write nothing
handoff create --to codex --out /tmp/h      # write the files outside the project; touch nothing in it
handoff create --to codex --sessions 3      # include the last three sessions, not just the newest
handoff create --to codex --budget 50000    # character budget (defaults: Codex 24k, Claude 32k, Cursor 16k)
handoff profiles -v                         # what each recipient gets, and why
```

Sources are detected automatically: `claude_code` and `codex` sessions, and `cursor` rules files
(Cursor's chat history isn't readable). Use `--from` to choose when several have data.

### With a model (optional)

Without a model, the handoff is deterministic: everything in it is copied or trimmed from the logs.
With one, the model also writes a summary of the state, decisions, open problems, next steps and
gotchas. Any OpenAI-compatible endpoint works:

```bash
handoff create --to codex --backend ollama --install       # local model through Ollama (qwen3.5 by default)
HANDOFF_BASE_URL=https://api.openai.com/v1 HANDOFF_MODEL=gpt-5 HANDOFF_API_KEY=... \
  handoff create --to codex --install
```

A local model fits the out-of-usage case, since nothing is sent to a paid API. A 10B model takes
20-30 s per handoff on a laptop. With Ollama the context window is sized to the prompt, up to
`HANDOFF_NUM_CTX` (default 32768). If the model fails or returns something unusable, you get the
model-free handoff instead.

## Privacy

Nothing leaves your machine unless you configure a model. Before anything is written or sent,
`handoff` replaces API keys, tokens, private keys, `*_SECRET=`-style values, URL passwords, emails
and phone numbers with `[REDACTED:...]`, and rewrites your home directory to `~`. This is
pattern matching, not a guarantee: your username can still appear in other paths (for example
`/tmp/claude-<user>/` or Claude Code's project folder names). If the session touched anything
sensitive, check the output, or the `--dry-run` text, before sharing it.

## How it works

- **Parsing.** It reads Claude Code's and Codex's session logs (JSONL, often megabytes). Thinking
  blocks, subagent transcripts and records the harness writes are skipped: background-task
  reports, `!` shell commands and their output, built-in slash commands like `/model`, and system
  reminders. What remains is your messages, the agent's replies and one line per tool call.
  Claude Code's own compaction summaries replace the history they cover.
- **Compaction.** It cuts to fit a per-recipient character budget, in a fixed order: older
  sessions first, then repeated tool calls, tool detail and long assistant prose, then whole
  assistant messages, and your messages last. Your first message and the last two messages are
  always kept.
- **Delta.** `.handoff/state.json` records the commit and last message at each handoff, per
  recipient, so the next handoff can say what changed.
- **Recipient profiles.** `handoff/recipients/profiles/*.toml` hold each recipient's instruction
  file, budget and style, as data rather than code.

## Does it help?

Not settled yet. A first SWE-bench sanity check, with 3 tasks and one run each, showed no
difference in tasks solved; the handoff cost about 1k tokens of context. Details are in
[`eval/swe/RESULTS.md`](eval/swe/RESULTS.md). A larger eval, with five scenarios built so the
next agent can't succeed without the earlier session, is ready to run but has no results yet:
see [`eval/README.md`](eval/README.md).

## Formats this depends on

None of these are public APIs, and they change between releases.

- **Claude Code** (verified against 2.1.x): `~/.claude/projects/<cwd with non-alphanumerics as
  "-">/<session>.jsonl`.
  - Conversation is in `user` / `assistant` records, whose `message.content` is a string or a list
    of blocks (`text`, `thinking`, `tool_use`, `tool_result`).
  - `isSidechain` marks subagent transcripts and `isMeta` marks harness-injected turns.
    `isCompactSummary` marks Claude's own compaction notes.
  - A message typed while the agent is working is an `attachment` of type `queued_command` with
    `commandMode: "prompt"`.
  - Harness records are skipped: `user` records with `origin.kind` `task-notification` or `peer`,
    and `user` records starting with `<bash-input>`, `<bash-stdout>`, `<bash-stderr>`,
    `<task-notification>` or `<local-command-caveat>`.
  - A slash command (`<command-name>`) followed by `<local-command-stdout>` ran inside the harness
    and is dropped. One without local output, such as a skill, is kept as `/name args`.
  - A `system` record with subtype `compact_boundary` marks an auto-compaction. The summary after
    it replaces the assistant turns before it, except those in
    `compactMetadata.preservedMessages`.
- **Codex CLI** (verified against 0.142-0.146): `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`.
  - Records are `session_meta` / `response_item` / `event_msg`, plus an older flat shape.
  - Each turn is written twice, once as `response_item` and once as `event_msg`.
  - Tool calls are `function_call` (`shell`, `exec_command`, `wait`) or `custom_tool_call`
    (`apply_patch`, `exec`).
  - Sub-agent threads are skipped. Codex's `compacted` records keep user messages verbatim.
- **Cursor:** rules files only. Its chat history is in an undocumented SQLite schema.

## Development

```bash
git clone https://github.com/wasumayan/handoff && cd handoff
uv sync --extra dev
uv run pytest -q && uv run ruff check .
```

## License

[MIT](LICENSE)
