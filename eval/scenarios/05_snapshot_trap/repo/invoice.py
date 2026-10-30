"""Plain-text invoices."""
from decimal import Decimal

SYMBOLS = {"USD": "$", "EUR": "€"}


def money(amount: Decimal, currency: str) -> str:
    # FIN-212 moved EUR to the European format; not implemented yet.
    return f"{SYMBOLS[currency]}{amount:,.2f}"


def format_invoice(lines: list[tuple[str, Decimal]], currency: str) -> str:
    out = [f"{name:<20}{money(amount, currency):>16}" for name, amount in lines]
    total = sum((a for _, a in lines), Decimal("0"))
    out.append(f"{'Total':<20}{money(total, currency):>16}")
    return "\n".join(out) + "\n"
