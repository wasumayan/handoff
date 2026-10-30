"""Print each customer's order total from data/orders.csv."""
import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "orders.csv"


def load(path=DATA) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def parse_date(text: str) -> date:
    day, month, year = (int(x) for x in text.split("/"))  # DD/MM/YYYY (EU export)
    return date(year, month, day)


def totals(rows: list[dict], since: date | None = None) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if since is not None and parse_date(r["date"]) < since:
            continue
        out[r["customer_id"].strip()] += float(r["amount"].replace(",", "."))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--since", type=date.fromisoformat, default=None)
    args = ap.parse_args(argv)
    items = sorted(totals(load(args.data), args.since).items(), key=lambda kv: (-round(kv[1], 2), kv[0]))
    for cid, total in items:
        sys.stdout.write(f"{cid} {total:.2f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
