# Handoff

Task: `--since YYYY-MM-DD` on `report.py`, counting orders on or after the date; deterministic output.

Found, not fixed yet: 4 rows in the export have customer id `C017 ` with a trailing space, which
makes C017 appear twice; strip ids when summing. `--since` and sorting are not done either.

Learned: `data/orders.csv` is `;`-delimited with comma decimals (`12,50`); dates look ambiguous
(every day value is <= 12). pandas is not installed and there is no network; use `csv`.

User said (not in any file):
- Dates in the export are DD/MM/YYYY (EU system; checked with the data owner). Don't infer from data.
- Sort by total descending, ties by customer_id ascending.

Rules: never modify `data/orders.csv`; `report.py` stays one file, stdlib only.

Next: strip ids, parse `date` as DD/MM/YYYY, filter `>= --since` (`date.fromisoformat`), sort, run tests.
