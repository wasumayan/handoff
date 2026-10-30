# Runbook: first real run

What to download, what to run, in order. Nothing here edits the target repo; `--dry-run` reads only,
and `create` without `--install` writes only under `.handoff/` inside the target repo. Pass `--out`
to keep even that outside the repo.

## 0. Do you need a GPU?

No, for the tool. `handoff` is pure Python and reads files. A GPU matters only if you want the
*backend model* (the one that writes `next.md`, and the stand-in recipient agent in `eval/`) to be a
local open-weight model rather than an API. Reasons to do that: cost on repeated eval runs, keeping
session content off third-party APIs, and the product story ("works with a local model"). If you
have an OpenAI or OpenRouter key, start with that; it removes a whole class of setup problems. A
self-hosted GPU server is step 5.

## 1. Install (laptop)

```bash
# uv if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# the tool, from the branch, into an isolated environment
uv tool install "git+https://github.com/wasumayan/handoff@claude/happy-mayer-tnvnhz"
handoff --version
```

To hack on it instead:

```bash
git clone https://github.com/wasumayan/handoff && cd handoff
git checkout claude/happy-mayer-tnvnhz
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
pytest -q
```

## 2. Point it at a repo Claude Code has worked in

```bash
cd ~/path/to/your-project
handoff sources -v
```

Expected: a `*` next to `claude_code` with `sessions=N` where N > 0, and the JSONL paths listed.
If `sessions=0`, Claude Code has not been run from exactly this directory on this machine (the
slug is the absolute path), so pick a repo where it has. `codex` with `sessions>0` means the
Codex parser has real data to be checked against; send me the first three lines of one of its
files (`head -3 <path>`) and the `handoff sources -v` output.

```bash
# read-only: prints exactly what a model would receive, writes nothing
handoff create --to codex --dry-run | less

# deterministic handoff, written OUTSIDE the project
handoff create --to codex --no-llm --out /tmp/project-handoff
less /tmp/project-handoff/HANDOFF.md
```

What to look at in `session.md`: are the human turns intact, are tool calls one line each, is any
`[REDACTED:...]` where it should not be (false positive) or missing where it should be? Note the
"compacted X -> Y chars" line on stderr; that is the first time compaction runs on a real session.

## 3. With a model (API)

```bash
export HANDOFF_BASE_URL=https://api.openai.com/v1   # or https://openrouter.ai/api/v1
export HANDOFF_API_KEY=...
export HANDOFF_MODEL=gpt-5                           # any chat model with JSON output
handoff create --to codex --out /tmp/project-handoff-llm
diff <(sed -n '/# Where things stand/,/# What changed/p' /tmp/project-handoff/HANDOFF.md) \
     <(sed -n '/# Where things stand/,/# What changed/p' /tmp/project-handoff-llm/HANDOFF.md)
```

Only `next.md` differs between the two runs by design. Judge it: does it say anything the
deterministic dump did not, and is all of it supported by the transcript?

## 4. Eval

See `eval/README.md` for setup, keys and what each metric means. In short:

```bash
uv run python eval/run.py doctor --live                       # prerequisites, one tiny call per CLI
uv run python eval/run.py score                               # handoff content per variant, no model
uv run python eval/run.py run --agent claude --scenarios 01 --variants dump --out eval/out/try
uv run python eval/run.py run --agent claude --runs 3 --out eval/out/claude
uv run python eval/run.py run --agent codex  --runs 3 --out eval/out/codex
```

## 5. Serving a local backend (optional)

Use this when you want the `adapted` variant and the stand-in agent to run against an open model. Run vLLM's
OpenAI-compatible server on any machine with a GPU (a 7B-14B instruct model in bf16 fits one 80 GB
GPU), tunnel the port to your laptop, and point `HANDOFF_BASE_URL` at it. `handoff` itself still
runs on the laptop.

```bash
# on the GPU machine
python -m venv vllm-env && . vllm-env/bin/activate && pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-Coder-14B-Instruct --dtype bfloat16 \
  --max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes --port 8000

# on the laptop
ssh -N -L 8000:localhost:8000 <user>@<gpu-host> &
export HANDOFF_BASE_URL=http://localhost:8000/v1 HANDOFF_MODEL=Qwen/Qwen2.5-Coder-14B-Instruct HANDOFF_API_KEY=x
```

The tool-calling flags are needed only for the stand-in agent; the parser name depends on the model
family (hermes for Qwen, llama3_json for Llama). On a SLURM cluster, run the server inside an
allocation, not on the login node, and download weights where the compute nodes can read them.

## 6. Report back

Paste the `handoff sources -v` output, the stderr of the first `create`, and anything in
`session.md` that looks wrong. Then the eval tables from `results.md`.
