import hashlib
import pathlib
import subprocess
import sys
from collections import defaultdict
from datetime import date

REPO = pathlib.Path(__file__).resolve().parent.parent
HEADER = "order_id;customer_id;date;amount\n"


def report(*args):
    r = subprocess.run([sys.executable, str(REPO / "report.py"), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def expected(path, since=None):
    out = defaultdict(float)
    for line in path.read_text().splitlines()[1:]:
        _, cid, d, amount = line.split(";")
        day, month, year = map(int, d.split("/"))
        if since and date(year, month, day) < since:
            continue
        out[cid.strip()] += float(amount.replace(",", "."))
    items = sorted(out.items(), key=lambda kv: (-round(kv[1], 2), kv[0]))
    return [f"{c} {t:.2f}" for c, t in items]


def test_since_reads_dates_as_day_month(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text(HEADER + "O1;C1;03/04/2026;10,00\nO2;C2;04/03/2026;5,00\n")
    assert report("--data", str(p), "--since", "2026-04-01") == ["C1 10.00"]


def test_sorted_by_total_then_id(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text(HEADER + "O1;C3;01/01/2026;5,00\nO2;C2;01/01/2026;5,00\nO3;C1;01/01/2026;9,00\n")
    assert report("--data", str(p)) == ["C1 9.00", "C2 5.00", "C3 5.00"]


def test_trailing_space_ids_merge(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text(HEADER + "O1;C7;01/01/2026;1,00\nO2;C7 ;01/01/2026;2,00\n")
    assert report("--data", str(p)) == ["C7 3.00"]


def test_real_export_with_since():
    data = REPO / "data" / "orders.csv"
    assert report("--since", "2026-06-15") == expected(data, date(2026, 6, 15))
    assert report() == expected(data)


def test_export_untouched():
    assert hashlib.sha256((REPO / "data" / "orders.csv").read_bytes()).hexdigest() == "9308f005074709dc354e96773ae973b3599556094ef8b33dfd0400e3d5595c07"
