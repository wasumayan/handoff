from decimal import Decimal
from pathlib import Path

from invoice import format_invoice

SNAP = Path(__file__).parent / "snapshots"
LINES = [("Widget", Decimal("1234.5")), ("Gadget", Decimal("0.5"))]


def test_invoice_usd():
    assert format_invoice(LINES, "USD") == (SNAP / "invoice_usd.txt").read_text()


def test_invoice_eur():
    assert format_invoice(LINES, "EUR") == (SNAP / "invoice_eur.txt").read_text()
