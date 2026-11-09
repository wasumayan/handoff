# Eval design: context usage, simple tasks, and handoff types

What to run next, in a sandbox, to answer three questions:

1. **Context usage.** What does each kind of handoff cost the recipient in context, and what does it
   save?
2. **Simple tasks.** On short, well-specified tasks, does a handoff help, do nothing, or get in the
   way?
3. **Handoff types.** Which kind of handoff (none, raw transcript, summary, structured dump,
   model-adapted, partial) works best for which recipient and situation?

This builds on `eval/` as it stands (see `eval/README.md`). It proposes new scenarios, variants and
metrics, the harness changes they need, and an order to run them in, cheapest first. Nothing here
has been run against a paid agent yet.

## 1. What the free run already shows

`uv run python eval/run.py score` (no model, seconds) on the current five scenarios, both sources:

| variant | mean chars | ~tokens | recall (facts / corrections / dead ends) |
|---|---|---|---|
| none | 0 | 0 | 0 / 0 / 0 |
| ideal | 799 | 200 | 100 / 100 / 100 |
| dump-2k | 3,169 | 790 | 80 / 100 / 80 |
| dump-8k | 4,722 | 1,180 | 100 / 100 / 100 |
| dump (profile default) | 7,076 | 1,770 | 100 / 100 / 100 |
| dump-64k | 11,309 | 2,830 | 100 / 100 / 100 |
| dump-base (tool at `39231ee`) | 7,006 | 1,750 | 90 / 100 / 100 |
| raw | 22,294 | 5,570 | 100 / 100 / 100 |

Four things follow:

- **Recall is saturated.** Every `dump` budget above 2k reaches 100%, so the content score cannot
  rank variants. The only misses are `dump-2k`, which drops scenario 03's dead end
  (`urlopen(url, 5)` sent a POST) and one fact each in 03 and 04, and `dump-base`, which drops one
  fact in 04. That's a small sign the parser and compaction fixes since `39231ee` helped. The
  scenarios hide too little, too visibly. Harder content tests are in §3A and §3C.
- **Handoffs are small next to the recipient's own context.** A Claude Code or Codex system prompt
  plus tool definitions is likely over 10k tokens before the agent reads a file (B3 measures the
  exact figure). A 1-6k token handoff is a small share of that, so total input tokens will mostly measure how much the
  agent explored, not how big the handoff was. That changes what "context usage" should measure
  (§3A).
- **`--budget` was not a cap on what the recipient reads.** It limited content chars (instruction
  docs plus the session). Headers, `next.md`, `delta.md` and the git snapshot were not counted, and
  the first human turn and the last two messages were never dropped. A 2,000 budget produced
  2,900-3,500 installed chars. *Since fixed:* the budget now caps the installed block, and only
  human turns that don't fit can exceed it (with a note). Still plot budget sweeps against measured
  handoff size: at very small budgets the human turns set a floor.
- **Only scenario 04 is big enough for size to matter.** Its raw transcript is 76k chars (Claude
  Code) and 107k (Codex), roughly 19k-27k tokens. All the others are under 8k chars raw.

## 2. Sandbox: needed before any numbers

Today the eval detects leaks but doesn't prevent them. The agent runs as a subprocess on the same
filesystem as `eval/scenarios/`, which holds the solutions and hidden tests, and the `leaked` flag
greps the transcript after the fact. Claude Code runs with `acceptEdits` and Bash and is not
sandboxed. Codex uses its own workspace-write sandbox.

**Proposal: one container per agent invocation, while the harness stays on the host.**

- The harness builds the handoff and prepares `work/repo` and `work/agent-home` on the host, as it
  does now.
- `_CLI.invoke` wraps the agent command in
  `docker run --rm -v <repo>:<repo> -v <agent-home>:<agent-home> -w <repo> -e <API keys> --network <egress-limited> <image> <cmd>`.
  Mounting at the same paths keeps transcripts and `paths_read` comparable with today. Nothing else
  from the host is visible, so a leak becomes impossible, not just flagged.
- Run hidden tests in the same image (a second `docker run`, mounting `hidden/` only at grading
  time), so the Python the agent used is the one that grades it.
- **Network: model APIs only.** Without that, `pip install pandas` succeeds and scenario 04's dead
  end ("pandas is unavailable") stops being a dead end. Today it's caught only by the `no_pandas`
  rule check. Use a Docker network whose only exit is a proxy that allows the Anthropic and OpenAI
  API hosts (plus whatever Codex auth needs, to confirm with `doctor --live`).
- **Codex's own sandbox inside a container.** Codex's workspace-write sandbox relies on OS-level
  features that an unprivileged container may block. Confirm `doctor --live` passes inside the
  image. If it doesn't, the container is the sandbox, and Codex runs with its own sandbox turned
  off.
- **Image:** Python 3.11+, uv, Node, the `claude` and `codex` CLIs at pinned versions, a non-root
  user, no pandas. Record the CLI versions in each run's `config.json`, since the transcript
  formats and agent behaviour drift between releases.
- **Parallel runs.** `cmd_run` runs cells one at a time. Cells share nothing (each has its own temp
  dirs), so a worker pool of N containers divides wall time by about N, up to the API rate limits.
  A 120-run matrix at a few minutes per run goes from most of a day to about an hour.

A cloud microVM or container service (Vercel Sandbox, E2B, Modal and others) can replace local
Docker for wider parallelism. The adapter is the same one-line command wrapper. Docker Desktop is
installed on this machine, but its daemon wasn't running when this was written.

## 3. Eval families

Each family lists the question, the scenarios and variants it needs, and what it measures.
Hypotheses are written down so a result can contradict them.

### 3A. Context usage

**A1. Exploration avoided (main context metric).** A handoff's value in context terms is the
reading it saves the agent, not its own size. Measure, per run:

- `calls_to_first_write`: tool calls before the first file edit
- `tokens_to_first_write`: cumulative input tokens before the first file edit
- `files_read_before_first_write`

Hypothesis: `none` reads the most before its first edit. `raw` and `dump` read less. `ideal` reads
the least. On scenario 04, `raw` may cost more in total than `dump`, because its prefix is re-read
on every turn.

Harness change: the Claude parser already keys usage per call (`per_call`). Record the index of the
first `Write`/`Edit` `tool_use` and sum `per_call` up to it. For Codex, do the same from the rollout
`token_count` events.

**A2. Exact handoff cost in the recipient's tokenizer.** `handoff_tokens_est` is `chars × 0.25`.
The exact cost is the first model call's input tokens under a variant minus the same under `none`,
for the same scenario and agent. The first call has read nothing yet, so the difference is the
handoff alone. Free once A1's per-call data is kept.

**A3. Cost, not just tokens.** The installed handoff sits in a prefix that is re-read on every
turn, mostly as cached tokens, which are billed far below fresh input. A large handoff can look
expensive in `input tok` and cheap in dollars. Report `cost/pass` as the headline efficiency
number. Codex reports no cost: add a per-model price table to the config. Check the prices when
adding them; they change.

**A4. Small-context recipient (where size really matters).** The README's use case is "credits ran
out, switch to a local model". The RUNBOOK's example local model runs with a 32k window. Scenario
04's raw Codex transcript (about 27k tokens) plus a system prompt and tool output will not fit.

- Run: `--agent standin` against the local model, on scenario 04 and the A5 variants of it, with
  variants `none`, `raw`, `dump`, `dump-8k` and `ideal`.
- Measure: pass, `peak ctx / window`, and how often a call fails on context length (record it as
  its own error kind rather than a generic failure).
- Confound: `standin` and `adapted` use the same backend model. Leave `adapted` out of this run, or
  use a second endpoint for it.
- Hypothesis: `raw` collapses here while `dump-8k` holds. This is the run most likely to show the
  tool doing something a pasted transcript can't.

**A5. Fact position and session length (tests compaction directly).** Compaction keeps the first
human turn and the last two messages and trims everything else oldest first. So the fact most at
risk is one discovered early-to-mid session, in a tool result, and never repeated.

- Generate variants of one scenario with `build_sessions.py`, parameterized by session length
  (about 10k, 50k and 200k chars of filler: unrelated reads, passing test runs) and by where the
  key fact appears (10%, 50% or 90% of the way through).
- Free first: run `score` on the grid with `dump-2k`, `dump-8k` and `dump` to find where
  compaction loses the fact.
- Then paid: run the agent only on the cells where recall differs.
- This also fixes the recall saturation, because some cells will fall below 100%.

**A6. Pointer handoff (lazy context).** The Codex profile already says "keep the installed block
short; link to `.handoff/` files for detail". Test that literally with a variant that installs only
`next.md` plus a pointer to `.handoff/session.md` left in the repo. Measure whether the agent opens
the file (`paths_read`), and the outcome against `dump`. If agents reliably follow pointers, a
small installed block is close to free. If they don't, the pointer is dead weight.

### 3B. Simple tasks: overhead and harm

Scenarios 01 and 05 are already small, one-file tasks. Tag each scenario with a size in
`checks.json` (`simple`, `multi-file`, `long`) and report results per tier. That gives
"performance on simple tasks, by handoff type" from the existing matrix at no extra cost. The
cases below add what the current scenarios can't show.

Every current scenario is built so that passing needs information from the session. So the eval
can show that a handoff helps, but never that it costs anything. Add the cases where it shouldn't
matter:

**B1. Control scenarios (one or two).** The task is fully specified by `task.md` plus the repo: a
small, local edit with the tests in place. The previous session exists but holds nothing
necessary. Expected: `none` ≈ `ideal` on pass. What to look for is any variant doing worse than
`none`: lower pass, more turns, more time, more tokens. That is overhead or distraction from the
handoff. Report it as a **harm rate**: the share of paired cells where a variant does worse than
`none` on a task that didn't need it.

**B2. Distracting session.** Same as B1, but the session also explored an unrelated idea at length
and never rejected it. Does the recipient pick up the abandoned direction? Count it with a redo
pattern on the distractor's file or symbol.

**B3. Fixed-overhead probe.** One cheap call per variant with a trivial request ("print the test
command for this repo and stop"). This measures handoff ingestion cost and time with no task noise,
the baseline that A2 subtracts from.

**B4. Handoff QA (cheap comprehension test, no sandbox needed).** Give the recipient model the
installed handoff and three to five questions drawn from each scenario's `checks.json`. Example:
"What date format did the user ask for?" Grade the answers with the existing regexes or a judge.
It's one call per cell with no tools, so it needs no sandbox and can run on a local model. It
measures whether the recipient extracts the information, which is what regex recall can't: a
paraphrase that recall misses still answers the question. Burying a fact in the middle of a long
`raw` transcript is the expected failure mode.

### 3C. Handoff types

Variants to add to `variants.toml`, grouped by the question they answer:

**C1. The baseline users actually try: "summarize this for the next agent".** The real competitor
to this tool is asking a model for a summary before switching. Add `kind = "summary"`: send the raw
transcript to the backend with a short generic prompt ("Write a handoff note so another coding
agent can continue this task") and install the result. If `dump` doesn't beat `summary` on pass or
cost/pass, the deterministic layering isn't earning its keep. This variant is the most important
one missing.

**C2. Source-native compaction.** Claude Code writes its own compaction summaries
(`isCompactSummary`). For recorded sessions that include one, a variant can install that summary
alone. It's free and the user already has it, so it is a second realistic baseline.

**C3. Layer ablations (which layer carries the value).** No CLI change needed: build once with
`--out`, then install a subset of the five files. Add `layers = ["next", "session"]` to the
variant schema in `variants.py`. Suggested set:

- `next` only
- `session` only
- `next` + `session`
- everything except `session`

Run these on a subset (scenarios 02, 04 and 05, one source) to keep cost down.

**C4. Profile mismatch (do recipient profiles matter?).** Build with `--to claude` and install into
`AGENTS.md` for Codex, and the reverse. Today the profile changes the budget, style and emphasis
text. If mismatched profiles score the same as matched ones, the profile layer is decoration, and
the README already asks this ("whether the model-adapted step beats a plain structured dump is an
open question").

**C5. Adapted, by model size.** Run `adapted` with a small local model and with a frontier API
model writing `next.md`. This shows whether the model step needs a strong model, and whether a weak
one is worse than no model step (`dump`).

**C6. Placement.** The same text installed in the instruction file versus prepended to the first
user message. Agents treat the two differently: one reads like standing instructions, the other
like the request. Cheap, and it may matter as much as content.

**C7. Retracted facts (precision, not just recall).** Add a check category, `retracted`: things the
session believed and later corrected. Example: "the rate is a float" until the API turned out to
return a string. A handoff that carries the old claim without the correction is harmful, and the
current recall checks reward it, because they only ask whether the right fact appears. Also report
**noise**: the share of handoff chars that match none of the scenario's facts, corrections or dead
ends. Use it as a rough precision measure, comparing variants rather than as an absolute.

### 3D. Over time: stale handoffs and multiple hops

Every run today is a first handoff, so `delta.md` and `.handoff/state.json` are never tested.

**D1. Stale handoff.** Build the handoff, then commit a change that makes part of it false (rename
the function it points to, fix the bug it describes). Then run the agent. Measure whether the agent
checks before acting, and whether runs that `delta.md` would have warned about do better. Use a
second handoff after the change to compare.

**D2. Two-hop chain (A → B → A).** The first agent works part of the way, a handoff goes to the
second agent, that agent works part of the way, and a handoff goes back. Measure information decay
(recall and QA after each hop), whether the delta is correct on the second hop, and the final pass.
This needs a harness mode that runs an agent for a bounded number of turns and then hands off its
real transcript. The sessions are then recorded, not authored, which also tests the parsers on live
output.

## 4. Metrics: what exists and what to add

| metric | exists | change |
|---|---|---|
| pass 1st, pass, nudges, questions | yes | none |
| prefs ok, redo | yes | add redo patterns for B2 distractors and the D1 stale target |
| input tok, output tok, peak ctx | yes | none |
| cost, cost/pass | Claude only | price table for Codex (A3) |
| handoff tokens | estimate (chars × 0.25) | exact first-call difference (A2) |
| calls / tokens / files read before first write | no | A1, from per-call usage already parsed |
| context-length failures | no | separate error kind (A4) |
| peak ctx / window | no | needs the model's window in config (A4) |
| harm rate on controls | no | B1, computed in `report.py` from paired cells |
| QA score | no | B4 |
| retracted-fact carry, noise share | no | C7, in `checks.json` and `score` |
| CLI versions | no | record in `config.json` (§2) |

## 5. Statistics: what this n can detect

With `--agent claude` and sessions crossing agents (the default), one variant against `none` gives
5 scenarios × 1 source × 3 repeats = 15 paired cells. On pass/fail, a two-sided sign test at p <
0.05 needs at least 6 discordant pairs, all in the same direction. In practice a variant must turn
6 of 15 cells from fail to pass, with none going the other way: an improvement of about 40 points.
Repeats of the same scenario are correlated, so the honest unit is closer to 5 scenarios than to 15
cells.

What this means:

- "Some handoff beats none" is detectable: the scenarios are built for it.
- "`dump` vs `dump-8k` vs `adapted` vs `summary`" is not, unless the gaps are large. Expect overlap,
  and report it as such.
- **More scenarios beat more repeats.** Between-scenario variation dominates. Ten to fifteen
  scenarios at two repeats is worth more than five at six.
- Pick scenarios where `none` passes sometimes (about 20-50%). At 0% or 100% a scenario has no room
  to show a difference. Check this on the calibration run, and retune or drop scenarios that sit at
  a ceiling or floor.
- Report bootstrapped intervals resampled by scenario, not standard errors over cells.

## 6. Order of work

Cheapest first. Run counts are per agent.

| step | what | cost | runs |
|---|---|---|---|
| 1 | Sandbox wrapper, egress proxy, parallel workers (§2) | engineering only | 0 |
| 2 | A1/A2 per-call metrics, C7 checks, budget-semantics decision | engineering only | 0 |
| 3 | A5 position/length grid with `score` | free | 0 (seconds) |
| 4 | B4 handoff QA on all scenarios × variants × 3 | one call per cell, no sandbox | about 300 single calls |
| 5 | A4 small-context run with `standin` on a local model | GPU time only | 5 variants × 3 scenarios × 3 = 45 |
| 6 | Paid calibration: one cell (`--scenarios 01 --variants dump`) | 1 run, capped at $2 | 1 |
| 7 | Core matrix: about 12 scenarios (5 now, plus 2 controls, 3 position cells, 1 stale, 1 distractor) × 7 variants (`none`, `raw`, `ideal`, `summary`, `dump`, `dump-8k`, `adapted`) × 2 repeats | paid, in parallel | about 168 |
| 8 | Ablations (C3, C4, C6) on 3 scenarios × 2 repeats | paid | about 50 |
| 9 | D2 two-hop chain | paid, needs a harness mode | about 30 |

Steps 3 to 5 decide which variants are worth paying for in step 7. If B4 and A5 show two variants
carrying the same information, drop one before the paid matrix.

## Appendix: harness changes, by file

- `eval/harness/agents.py`: command wrapper for the container (§2); first-write index and
  per-call token prefix (A1/A2); context-length error kind (A4).
- `eval/run.py`: `--sandbox docker|none`, `--workers N` with a thread pool over cells; record CLI
  versions.
- `eval/harness/variants.py`: `kind = "summary"` (C1), `layers = [...]` (C3), `profile = "<other>"`
  for mismatch (C4), `placement = "prompt"` (C6), pointer variant (A6).
- `eval/harness/scenario.py`: `retracted` category and noise share in `handoff_recall` (C7).
- `eval/scenarios/build_sessions.py`: length and position parameters (A5), control and distractor
  scenarios (B1/B2), stale-handoff setup step (D1).
- `eval/harness/report.py`: harm rate, cost/pass with a price table, scenario-clustered bootstrap
  intervals, a pass-vs-cost frontier per variant.
- New `eval/qa.py`: the B4 comprehension probe.

## 7. Measured: a local model writing the handoff (Ollama, qwen3.5 9.7B, 32 GB Mac)

`adapted` on all five scenarios (Codex source), scoring recall of `next.md` alone. Scoring the
whole handoff hides the model's contribution, because the deterministic `session.md` is still in it.

| scenario | Ollama default window (4,096) | window raised to 32,768 |
|---|---|---|
| 01, 02, 03, 05 | 100% except one miss (02's dead end) | 100% except one miss (05's dead end) |
| 04 (long) | **0 of 6**: prompt cut from 7,220 to 2,050 tokens | 5 of 6 (1,789 chars) |

One run per cell. Only 04's prompt was truncated. The others fit in 2,050 tokens, so they got the
same input at both window sizes, and their two single misses are run-to-run noise, not a window
effect. About 20-30 s per handoff with a warm model.

After the fixes (native Ollama API, `--brief`, state card), `score` on all ten scenario/source pairs:

| variant | chars | recall: facts / corrections / dead ends | build |
|---|---|---|---|
| `brief` (no model: state card + delta) | 1,624 | 10 / 100 / 20 | 0.2 s |
| `adapted-brief` (qwen3.5 notes + card + delta) | 3,022 | 80 / 100 / 60 | 18 s |
| `dump` (full, default budget) | 7,496 | 100 / 100 / 100 | 0.2 s |

The model is what carries the agent's own discoveries into a small handoff. Without one, a brief
keeps the user's words and the test and error facts, but not what the agent found out.

- At the default window, Ollama cut the 04 prompt silently (`truncating input prompt` in its log).
  The model wrote "No transcript received… no work completed", and `handoff` installed that as the
  first section the recipient reads. *Since fixed:* Ollama is now called through its native API
  with `num_ctx` sized to the prompt. With the default setup, scenario 04 went from 0 of 6 to 5 of
  6, in a 12k window. For other servers, a reported `usage.prompt_tokens` under half the expected
  count makes `handoff` discard the answer and fall back to `--no-llm`.
- With a large enough window, a 10B local model wrote a usable brief about a tenth the size of
  the deterministic dump (one scenario, one run). That supports installing the brief and leaving the transcript on disk
  (§3A A6).
- The eval needs a `next`-only recall column, or every `adapted` variant scores 100%. The
  `adapted-brief` variant, which installs only the model's notes, the state card and the delta,
  avoids the problem: its recall is the model's.

## 8. Real-task benchmarks with a simulated "out of usage" cut

The synthetic scenarios test whether preferences and corrections survive the handoff. Real
benchmarks test whether work state survives, in large codebases where orientation is expensive.

**SWE-bench Verified (or a fresher set such as SWE-bench Pro or SWE-rebench, to limit
training-data contamination).**

Protocol:
1. Run the source agent on an instance with a hard cut: a turn count, a token count, or
   `--max-budget-usd`.
2. Keep its real transcript and the dirty working tree at the cut.
3. Build each handoff variant from that transcript.
4. Start the recipient agent in the same instance container.
5. Grade with the official harness (FAIL_TO_PASS and PASS_TO_PASS tests in its per-instance Docker
   image, which is also the sandbox).

Design points:
- **Choose cut points with progress behind them.** Keep instances the source solves when run
  uncut, and cut at about 30%, 60% and 90% of its median turns. Drop instances the recipient solves
  from scratch almost every time: they can't show a difference.
- **"The code is the handoff" baseline.** In SWE-bench the half-done diff is in the tree. `none`
  with the dirty tree visible is a strong baseline that the synthetic scenarios lack, because they
  commit the midpoint.
- **Upper bound:** the source continuing without the cut.
- **Localization cost: a metric only real benchmarks give.** The gold patch names the files that
  matter. Measure input tokens and tool calls until the recipient first opens a gold-patch file.
  This is "where are we in the repo" made measurable, and the place a handoff should save the most
  on a large codebase.
- **What it doesn't test:** SWE-bench issues have no mid-session user corrections or preferences,
  so preference transfer still needs the synthetic scenarios (or added constraints, as below).
- **Cost:** two agent runs per cell (source and recipient). Start with 20-30 instances.

**Other benchmarks that fit:**
- **Terminal-Bench:** containerized terminal tasks with tests. Same cut protocol, and it goes
  beyond Python repos.
- **Aider Polyglot:** small, multi-language exercises. Cheap, good for "simple tasks", and a broad
  check that the handoff doesn't harm small tasks.
- **τ-bench / τ²-bench:** policy and user-constraint adherence over long dialogues. The closest
  public test of "a rule stated mid-conversation survives the handoff".
- **Constraint injection on SWE-bench:** add a scripted mid-session user message ("don't touch
  `tests/`", "keep the public signature") to the source run, and check it in the recipient's final
  diff. This makes preference transfer measurable on real repos.
- **Long-conversation memory sets (LongMemEval, LoCoMo):** not coding, but they are a ready source
  of fact-at-position questions for the cheap QA probe (§3B B4).

**Also worth measuring:**
- **Faithfulness:** claims in a model-written handoff that the transcript doesn't support. Check
  file paths and commands against the transcript deterministically, then use a judge for the rest.
- **Total cost of switching:** handoff build plus recipient run, against the recipient from
  scratch.
- **Parser robustness:** recorded sessions from several CLI versions.
- **Redaction:** what it catches and what it wrongly redacts.
