# Continuation prompt for Claude Code (local)

Paste the block below into Claude Code, started from a checkout of this repo on the branch
`claude/happy-mayer-tnvnhz`. It assumes you have a sibling repo that Claude Code has worked in
(murphy); adjust the path.

---

You are continuing work on `handoff`, a Python CLI that transfers coding-agent context (Claude Code,
Codex, Cursor) into the form another agent reads best. State of the repo on this branch:

- Package in `handoff/`: sources (`sources/claude_code.py` verified against real 2.1.x JSONL;
  `sources/codex.py` written from documented shapes, NOT yet verified against a live `~/.codex`;
  `sources/cursor.py` rules only), recipients as TOML profiles in `recipients/profiles/`,
  deterministic compaction in `compact.py`, optional OpenAI-compatible model step in `compose.py`
  + `backend.py`, regex redaction in `redact.py`, marker-block install in `recipients/base.py`,
  per-recipient delta state in `state.py`. `pytest -q` passes (35 tests), `ruff check .` clean.
- Eval harness in `eval/`: three authored midpoint scenarios, arms A/B/C, recipients `fake` /
  `agent` / `cmd:`. Only the `fake` recipient and a mock server have been exercised. No real model
  has produced or consumed a handoff yet.
- `docs/RUNBOOK.md` has the step-by-step for the first real run, including an optional Della vLLM
  backend.

Do these, in order, and do not modify anything inside the murphy repo:

1. `cd ../murphy && handoff sources -v`. Report what it finds. If `claude_code sessions=0`, stop and
   ask me for another repo path.
2. `handoff create --to codex --dry-run --path ../murphy > /tmp/dry.md` and read it. Then
   `handoff create --to codex --no-llm --path ../murphy --out /tmp/murphy-handoff`. Read
   `/tmp/murphy-handoff/session.md` end to end. Fix, in this repo only, anything the real session
   breaks: records the parser skipped that carried content, tool calls that lost their results,
   redaction false positives/negatives, compaction dropping the wrong things. Add a sanitized
   excerpt of any record shape that broke the parser to `tests/fixtures/` and a test for it.
3. If `handoff sources -v` showed Codex sessions anywhere (try a few repos), check
   `sources/codex.py` against `head -3` of a real session file, fix the parser, add a fixture.
4. If `HANDOFF_BASE_URL` and `HANDOFF_MODEL` are set in my environment, run
   `handoff create --to codex --path ../murphy --out /tmp/murphy-handoff-llm` and compare `next.md`
   with the `--no-llm` one. Tell me whether the model added anything the transcript supports that
   the dump lacked. Do not tune prompts to make it look better; report what you see.
5. Run `python eval/run.py --recipient fake`, then if a backend is set,
   `python eval/run.py --recipient agent --runs 3 --judge`. Put the resulting `results.md` table in
   your final message with one paragraph of interpretation. If arm C does not beat arm B, say so.
6. Commit each fix separately with a plain subject line. Do not push until I say so.

Preferences: no emojis, challenge my assumptions, skip the fluff, accuracy over polish. Never
commit session transcripts or anything from `~/.claude` into this repo.
