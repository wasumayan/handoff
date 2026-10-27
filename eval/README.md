# Evaluation harness

Question: does a recipient-adapted handoff let the next agent finish the job better than (A) the raw
transcript or (B) a deterministic structured dump? "Better" here is measured, not asserted.

## Scenarios

Each scenario is a small repo with a task, a planted preference in its instruction file, a source
session (Claude Code JSONL, authored to look like the agent stopped at a midpoint), hidden tests,
and a reference solution.

| id | task | midpoint | planted preference | hidden tests check |
|---|---|---|---|---|
| 01_simple | add `--verbose` to a one-file CLI | flag parsed, not wired | never `print`, use `logging` | `--verbose` emits DEBUG via logging; no `print(` in app.py |
| 02_medium | fix a bug spanning 3 modules | root cause found in session, fix not applied | run `uv run pytest`, not `pytest` | the failing test passes, others still pass |
| 03_instructions | 3-step refactor under a long CLAUDE.md | step 1 of 3 done | no `requests` dep; commits prefixed `refactor:` | steps 2 and 3 done; no `requests` import |

## Arms

- **A raw**: every user/assistant message and full tool output from the source session, dumped
  verbatim into the recipient's instruction file. The "just copy the chat" baseline.
- **B dump**: `handoff create --no-llm --install`. Layered, compacted, no model.
- **C adapted**: `handoff create --install` with a backend. Same as B plus a model-written `next.md`.

## Recipients

- `fake`: copies the reference solution in. Validates the harness and hidden tests, not a model.
- `agent`: a minimal tool-calling loop (`eval/agent.py`) against the configured OpenAI-compatible
  backend, with the instruction file as system prompt and `read_file` / `write_file` / `run` tools,
  capped at `--max-turns`. A stand-in for Codex when `codex` is not installed.
- `cmd:<command>`: run any command in the work tree, e.g.
  `cmd:codex exec --full-auto "Continue the task described in AGENTS.md"` or
  `cmd:claude -p "Continue the task in CLAUDE.md" --dangerously-skip-permissions`.

## Metrics

- `pass`: hidden tests green (0/1)
- `pref`: planted-preference checks, computed from the diff and command log where possible
  (no `print(`, used `uv run`, no `requests`), plus a judge-model rubric when a backend is set
- `turns`, `tool_calls`, `prompt_chars` (size of the instruction file the recipient saw)

## Run

```bash
python eval/run.py --recipient fake                 # harness self-check, no model
HANDOFF_BASE_URL=... HANDOFF_MODEL=... python eval/run.py --recipient agent --runs 3
python eval/run.py --recipient "cmd:codex exec --full-auto 'Continue the task in AGENTS.md'" --to codex
```

Results land in `eval/out/<timestamp>/results.json` and `results.md`.

## Caveats, stated up front

n is 3 scenarios x a few runs. Source sessions are authored, not recorded (recording needs a live
Claude Code run per scenario; `eval/scenarios/build_sessions.py` documents the shape so recorded
ones can be dropped in). The judge model shares failure modes with the recipient model. Treat the
table as a smoke test on "is adaptation worth its cost", not as a benchmark result.
