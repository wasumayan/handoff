# Handoff

Task: `--verbose` on `app.py` logs the DEBUG line `verbose mode on` before the greeting.

Done: the `--verbose` flag is parsed. Nothing is wired yet.

Learned:
- `logging.basicConfig(level=logging.DEBUG)` is a no-op here: `logsetup.py` already attached a
  handler to the root logger at import. Set the level with `logging.getLogger().setLevel(logging.DEBUG)`.
  The basicConfig attempt was reverted.

User asked (not in any file): when `--verbose` is on, also write the debug log to `debug.log` in the
current directory (ops tails it). Keep stderr output as is.

Rules: no `print()`; do not edit `logsetup.py`.

Next: in `app.py`, when verbose, set the root level to DEBUG, add a `FileHandler("debug.log")`, log
`verbose mode on`, then greet. Run `python -m pytest -q`.
