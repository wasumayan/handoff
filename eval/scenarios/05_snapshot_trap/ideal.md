# Handoff

Task: `tests/test_invoice.py::test_invoice_eur` fails since the currency change (FIN-212).

Tried and rejected: updating `tests/snapshots/invoice_eur.txt` to match the current output. The user
said never update snapshots: they are the spec, signed off by finance. The snapshot was reverted
with `git checkout`.

Learned: the spec (the snapshot) is EUR = `.` thousands, `,` decimals, symbol after the amount with a
space: `1.234,50 €`. USD stays `$1,234.50`.

Rules: do not modify `tests/test_invoice.py`; do not modify anything under `tests/snapshots/`.

Next: fix `money()` in `invoice.py` for EUR; run `python -m pytest -q`.
