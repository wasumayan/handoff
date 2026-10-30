"""Print each customer's order total from data/orders.csv."""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "orders.csv"


def load(path=DATA) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def totals(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        out[r["customer_id"]] += float(r["amount"].replace(",", "."))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    args = ap.parse_args(argv)
    for cid, total in totals(load(args.data)).items():
        sys.stdout.write(f"{cid} {total:.2f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
