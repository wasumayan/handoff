# SWE-bench handoff experiment

A small, real-task check of the one question that matters: when an agent runs out of usage
partway through a task, does a smart handoff help the next agent finish, and what does it cost in
context compared with just asking the next agent to take over?

Basic metrics only. Three tasks, one run each: read it as a sanity check of the method, not a
benchmark score.

## What runs

Tasks: three `pytest-dev/pytest` instances from SWE-bench Verified (`instances.json`): pure Python,
so they run natively on arm64 and x86. Every agent command runs in a throwaway Docker container that
sees only the task repo and the agent's own config directory.

For each task:

1. **Source.** Codex works on the GitHub issue to the end; whether it would have solved it alone is
   recorded. It is then rewound to the moment just before its first file edit, as if its usage ran
   out there: its session file is cut at that record and the repo is reset to match. At that point
   it has investigated and diagnosed the bug, and that knowledge exists only in its session.
   (A first pilot cut the live run three tool calls after the first edit instead: the source had
   already fixed the bug by then, so there was nothing left to hand off.)
2. **Takeover**, twice, from that same state at the same path, each by a fresh Codex with no
   memory of the source session:
   - `plain`: the issue, plus "another agent ran out of usage partway; any changes it made are
     uncommitted; take over".
   - `smart`: the same prompt, plus `handoff create --brief` written by a local model (`qwen3.5`
     through Ollama) installed in `AGENTS.md`, which Codex reads on start.
3. **Grading**, as in SWE-bench: apply the instance's test patch to a copy and run its test files.
   Resolved means every `FAIL_TO_PASS` and `PASS_TO_PASS` test passes.

## Metrics

| metric | meaning |
|---|---|
| resolved | yes or no, by the hidden tests |
| source: solved uncut | whether the source would have resolved the task had it not been cut |
| source ctx at cut | the source's largest model input before the cut: how full its context was |
| handoff chars | size of what `smart` adds to `AGENTS.md` |
| ctx at start | input tokens of the recipient's first model call: system prompt + `AGENTS.md` + prompt |
| peak ctx | the recipient's largest model input |
| total input | input tokens over the whole run, cached included |
| turns, tool calls, seconds | how much work the takeover took |

## Run

```bash
# Docker running; Ollama running with the model pulled (ollama pull qwen3.5);
# OPENAI_API_KEY set, or Codex logged in with an API key
uv run python eval/swe/swe.py run --out eval/out/swe/run1
uv run python eval/swe/swe.py report eval/out/swe/run1
```

Each task makes three Codex runs (source, plain, smart) on your OpenAI key. Rerunning with the same
`--out` skips finished tasks. Per task, the output directory keeps the source's and each condition's
`events.jsonl` and session files, `diff.patch`, `grade.log`, and, for `smart`, the handoff itself.

## Limits

- One run per condition: agent runs vary, so a single difference can be noise.
- These tasks are easy for current models; expect both conditions to resolve most of them. The
  informative numbers are then the context and work each takeover needed.
- Source and recipient are both Codex. Claude Code as the source needs credentials a container can
  use (`claude setup-token`); the experiment otherwise works the same.
- Containers have normal network access (the agents need the API). An agent could install packages.
- `--keep-pii` is used for the handoff so its absolute paths stay valid inside the container, where
  `~` is not the host's home.
