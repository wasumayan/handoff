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

## Current status (for whoever runs the eval)

**Done:**
- The tool works on real Claude Code and Codex sessions.
- The eval harness is built: five synthetic scenarios, eight handoff variants, and the Claude Code
  and Codex agents. All tests pass.
- Each CLI was smoke-tested once. That shows the plumbing works; it is not a result.

**Not done:** the actual eval runs. That's the next step.

**Run these, in order** (details in `eval/README.md`):

```bash
git clone https://github.com/wasumayan/handoff && cd handoff
git checkout claude/happy-mayer-tnvnhz
uv sync --extra dev && uv run pytest -q          # needs Python 3.11+ and uv; all tests should pass

# install the claude and codex CLIs, then:
export ANTHROPIC_API_KEY=...  OPENAI_API_KEY=...
export HANDOFF_BASE_URL=https://api.openai.com/v1 HANDOFF_MODEL=<an OpenAI model> HANDOFF_API_KEY=$OPENAI_API_KEY

uv run python eval/run.py doctor --live          # 1. checks setup; makes one tiny paid call per CLI
uv run python eval/run.py score                  # 2. free: what each handoff variant contains
uv run python eval/run.py run --agent claude --scenarios 01 --variants dump --out eval/out/try
                                                 # 3. one run, to see the real cost per run
uv run python eval/run.py run --agent claude --runs 3 --out eval/out/claude   # 4. full runs
uv run python eval/run.py run --agent codex  --runs 3 --out eval/out/codex
```

Then send back `eval/out/claude/results.md` and `eval/out/codex/results.md`.

The agents edit files and run commands without asking, so use a VM or container. Each full run is
about 120 agent runs. Claude Code calls are capped at $2 each (`--max-budget-usd`). If a run is
interrupted, rerun the same command: finished runs are skipped.

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

See `eval/README.md`. Five synthetic scenarios, each stopped at a midpoint of a session (Claude Code
and Codex formats). Each hides facts, a user correction and a dead end that exist only in that
session. A recipient agent (Claude Code, Codex, or a stand-in) continues under each handoff variant:
none, raw transcript, ideal, `--no-llm` at several budgets, an older version of the tool, or
model-adapted. Measured: hidden tests (first try and after nudges), nudges and questions (manual
input), rule compliance, repeated dead ends, tokens, peak context, time, and cost. A model-free score
covers handoff size and recall. Small n: read it as a comparison between variants, not as a benchmark.

## Formats this depends on

These are not public APIs and drift between releases.

- Claude Code: `~/.claude/projects/<cwd with non-alphanumerics as "-">/<session>.jsonl`. Records of
  type `user` / `assistant` carry `message.content` as a string or blocks (`text`, `thinking`,
  `tool_use`, `tool_result`). `isSidechain` marks subagent transcripts, `isMeta` harness-injected
  turns, `isCompactSummary` Claude's own compaction notes. Verified against 2.1.x.
- Codex CLI: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` with `session_meta` /
  `response_item` / `event_msg` records, plus an older flat shape. Verified against real 0.142-0.146
  sessions: every turn is written twice (response_item and event_msg); tool calls are
  `function_call` (`shell`, `exec_command`, `wait`) or `custom_tool_call` (`apply_patch`, the
  JavaScript `exec` tool); sub-agent threads (`thread_source: "subagent"`) are skipped. Unknown
  record types are ignored.
- Cursor: rules files only. Chat history is in an undocumented SQLite schema and is not read.

## Not in scope

App-to-app transfers (dating profiles, playlists, calendars). Different access problem, different
trust problem.

## Development

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
pytest -q && ruff check .
```
