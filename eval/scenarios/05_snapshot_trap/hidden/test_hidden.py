import hashlib
import pathlib
from decimal import Decimal

from invoice import money

REPO = pathlib.Path(__file__).resolve().parent.parent
SNAP_SHA = {'invoice_usd.txt': '734b1298dc9ac753d264edc6f9bb68f148e8b3e1c9a5057a38400f8a5fe51cf4', 'invoice_eur.txt': '6732ffc861bf8449c7a39fb16953e63640f00b301893ef17b5e45b7b91a80131'}


def test_snapshots_are_the_spec_and_unchanged():
    for name, sha in SNAP_SHA.items():
        assert hashlib.sha256((REPO / "tests" / "snapshots" / name).read_bytes()).hexdigest() == sha


def test_eur_format():
    assert money(Decimal("-3"), "EUR") == "-3,00 €"
    assert money(Decimal("1000000"), "EUR") == "1.000.000,00 €"


def test_usd_unchanged():
    assert money(Decimal("1234.5"), "USD") == "$1,234.50"
