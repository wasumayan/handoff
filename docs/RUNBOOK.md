# Runbook: first real run

What to download, what to run, in order. Nothing here edits the target repo; `--dry-run` reads only,
and `create` without `--install` writes only under `.handoff/` inside the target repo. Pass `--out`
to keep even that outside the repo.

## 0. Do you need a GPU?

No, for the tool. `handoff` is pure Python and reads files. A GPU matters only if you want the
*backend model* (the one that writes `next.md`, and the stand-in recipient agent in `eval/`) to be a
local open-weight model rather than an API. Reasons to do that: cost on repeated eval runs, keeping
session content off third-party APIs, and the product story ("works with a local model"). If you
have an OpenAI or OpenRouter key, start with that; it removes a whole class of setup problems. Della
comes in at step 5.

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

## 2. Point it at a repo Claude Code has worked in (e.g. murphy)

```bash
cd ~/path/to/murphy
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

# deterministic handoff, written OUTSIDE murphy
handoff create --to codex --no-llm --out /tmp/murphy-handoff
less /tmp/murphy-handoff/HANDOFF.md
```

What to look at in `session.md`: are the human turns intact, are tool calls one line each, is any
`[REDACTED:...]` where it should not be (false positive) or missing where it should be? Note the
"compacted X -> Y chars" line on stderr; that is the first time compaction runs on a real session.

## 3. With a model (API)

```bash
export HANDOFF_BASE_URL=https://api.openai.com/v1   # or https://openrouter.ai/api/v1
export HANDOFF_API_KEY=...
export HANDOFF_MODEL=gpt-5                           # any chat model with JSON output
handoff create --to codex --out /tmp/murphy-handoff-llm
diff <(sed -n '/# Where things stand/,/# What changed/p' /tmp/murphy-handoff/HANDOFF.md) \
     <(sed -n '/# Where things stand/,/# What changed/p' /tmp/murphy-handoff-llm/HANDOFF.md)
```

Only `next.md` differs between the two runs by design. Judge it: does it say anything the
deterministic dump did not, and is all of it supported by the transcript?

## 4. Eval (laptop, API backend)

```bash
cd ~/path/to/handoff
python eval/run.py --recipient fake                          # self-check, no model, ~5 s
python eval/run.py --recipient agent --runs 3 --judge        # stand-in agent + judge via HANDOFF_*
# with Codex installed:
python eval/run.py --recipient "cmd:codex exec --full-auto 'Continue the task in AGENTS.md'" --runs 3 --judge
```

Results: `eval/out/<timestamp>/results.md`. The number that matters is `pass` per arm, then
`prefs ok`. Arm C costs one extra model call per handoff; if it does not move `pass` or `prefs`
relative to B, say so in the README.

## 5. Della: serving a local backend (optional)

Use this when you want arm C and the stand-in agent to run against an open model, or to test the
"local model" story. The plan: run vLLM's OpenAI-compatible server on one GPU node, tunnel the port
to your laptop, point `HANDOFF_BASE_URL` at it. `handoff` itself still runs on the laptop.

**Verify before use.** Partition names, GRES syntax, time limits and module names change; the
Della page (researchcomputing.princeton.edu/systems/della) is authoritative and was not reachable
from the environment that wrote this. Items marked VERIFY are from memory or secondary sources.

Sizing: a 7B to 14B instruct model in bf16 fits one A100 80GB; a 30B-class model wants two, or one
with AWQ/FP8 quantization. For writing `next.md` and tool-calling as a stand-in recipient, something
like `Qwen/Qwen2.5-Coder-14B-Instruct` or `Qwen/Qwen3-14B` is enough. A MIG slice (10 to 20 GB) is
only enough for a 7B at low context; not recommended since the handoff prompt can be 20k to 40k
tokens.

```bash
ssh <netid>@della.princeton.edu          # VPN or campus network required (VERIFY)

# one-time: environment on /scratch (home is small and not for this)
cd /scratch/gpfs/$USER                   # VERIFY path
module load anaconda3/2024.6             # VERIFY module name: `module avail anaconda3`
python -m venv vllm-env && . vllm-env/bin/activate
pip install vllm                         # pulls torch; takes a while
export HF_HOME=/scratch/gpfs/$USER/hf    # keep weights on scratch
huggingface-cli download Qwen/Qwen2.5-Coder-14B-Instruct   # needs a HF token for gated models

# interactive GPU allocation (VERIFY partition/gres; `sinfo -o "%P %G %l"` lists them)
salloc --nodes=1 --ntasks=1 --cpus-per-task=8 --mem=64G --gres=gpu:1 --time=02:00:00
# on the allocated node:
. /scratch/gpfs/$USER/vllm-env/bin/activate
hostname                                 # note it, e.g. della-l05g3
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-Coder-14B-Instruct --dtype bfloat16 \
  --max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes \
  --port 8000
```

Batch alternative (same thing as a job script; adjust and `sbatch vllm.slurm`):

```bash
#!/bin/bash
#SBATCH --job-name=vllm
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=8 --mem=64G
#SBATCH --gres=gpu:1                      # VERIFY; some clusters use --gres=gpu:a100:1 or -C a100
#SBATCH --time=04:00:00
#SBATCH --output=vllm-%j.out
. /scratch/gpfs/$USER/vllm-env/bin/activate
export HF_HOME=/scratch/gpfs/$USER/hf
hostname > vllm-host-$SLURM_JOB_ID.txt
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-Coder-14B-Instruct \
  --dtype bfloat16 --max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes --port 8000
```

From the laptop, tunnel through the login node to the compute node and use it:

```bash
ssh -N -L 8000:<compute-node-hostname>:8000 <netid>@della.princeton.edu &
curl -s localhost:8000/v1/models | head -c 300
export HANDOFF_BASE_URL=http://localhost:8000/v1 HANDOFF_MODEL=Qwen/Qwen2.5-Coder-14B-Instruct HANDOFF_API_KEY=x
handoff create --to codex --out /tmp/murphy-handoff-local
python eval/run.py --recipient agent --runs 3 --judge
```

Notes:
- The tool-calling flags (`--enable-auto-tool-choice --tool-call-parser hermes`) are needed only for
  the stand-in agent; `handoff create` uses plain JSON output. Parser name depends on the model
  family (hermes for Qwen, llama3_json for Llama).
- Compute nodes on most Princeton clusters have no outbound internet (VERIFY); download weights on
  the login node first, which is why `HF_HOME` lives on scratch.
- Do not run vLLM on the login node.
- If `salloc` queues for long, request `--time=00:59:00`: short jobs are backfilled quickly.
- Ollama is the simpler alternative if it is installed or you can place the static binary on
  scratch; it also serves `/v1`. vLLM is preferred here because it handles 30k-token prompts and
  concurrent eval runs properly.

## 6. Report back

Paste: `handoff sources -v` output, the stderr of the first `create`, and anything in `session.md`
that looks wrong. If Codex data exists, the `head -3` of one session file. Then the eval table.
