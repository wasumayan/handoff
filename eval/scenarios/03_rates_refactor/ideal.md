# Handoff

Task: three-step refactor of `shop/` (see the task). Step 1 (`shop/money.py`, `format_price`) is done
and committed. Steps 2 and 3 remain.

Learned:
- The rates API returns the rate as a JSON *string*: `{"rate": "1.0842", "as_of": "2026-10-27"}`.
- `urllib.request.urlopen(url, 5)` is wrong: the second positional parameter is `data`, so it sent a
  POST. Pass `timeout=5` as a keyword. That attempt was reverted.

User changed the plan (not in any file): `get_rate` returns `decimal.Decimal`, not float (finance
needs exact values), and `RateError` carries the currency code as `.currency`.

Rules: stdlib only (never `requests`), mock `urllib.request.urlopen` in tests, one commit per step
prefixed `refactor:`, do not touch `shop/legacy.py`.

Next: step 2 in `shop/rates.py` (urllib, Decimal(str(rate)), RateError(currency)), tests in
`tests/test_rates.py`; step 3 `Cart.total_in(currency)`: Decimal cents * rate, round half-up to int
cents, `format_price`.
