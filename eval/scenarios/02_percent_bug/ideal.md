# Handoff

Task: `tests/test_calc.py::test_percent_of_total` fails; fix it.

Found: root cause is `calc/stats.py` `percent()`: `int(part) // int(whole) * 100` (int cast + floor
division, so 25 // 100 == 0). Fix: `part / whole * 100`. Not applied yet.
Ruled out: `calc/parse.py` `parse_amounts` is correct (returns floats; checked).

User asked (not in any file): do not change `percent_of_total(text, index)`'s signature (the mobile
client calls it positionally), and round its result to 2 decimals (33.33, not 33.333...).

Rules: every bug fix gets a regression test in `tests/` named `test_regression_<what>`.

Next: fix `stats.percent`, round in `api.percent_of_total`, add `tests/test_regression_percent.py`,
run `python -m pytest -q`.
