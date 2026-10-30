# shop — project rules

## Dependencies
- Standard library only. **Never add `requests`** or any third-party HTTP client; this ships inside an environment without network package installs. Use `urllib.request`.
- No new runtime dependencies without an ADR in `docs/adr/`.

## Code style
- Type hints on every public function.
- Money is always `int` cents internally; format only at the edge via `shop/money.py`.
- Errors: define a module-level exception class per module (`RateError`, `CartError`); never raise bare `Exception`.
- No bare `except:`.

## Tests
- `python -m pytest -q`. Every behaviour change ships with a test in `tests/`.
- Network calls must be mocked in tests (patch `urllib.request.urlopen`).

## Git
- One commit per refactor step. Subject line prefixed `refactor:` for refactors, `test:` for test-only commits.
- Never amend or force-push.

## Process
- Before step N+1, confirm step N's tests pass.
- Do not touch `shop/legacy.py`; it is scheduled for deletion separately.
