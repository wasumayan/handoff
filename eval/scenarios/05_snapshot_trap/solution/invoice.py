"""Plain-text invoices."""
from decimal import Decimal

SYMBOLS = {"USD": "$", "EUR": "€"}


def money(amount: Decimal, currency: str) -> str:
    if currency == "EUR":
        digits = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{digits} €"
    return f"{SYMBOLS[currency]}{amount:,.2f}"


def format_invoice(lines: list[tuple[str, Decimal]], currency: str) -> str:
    out = [f"{name:<20}{money(amount, currency):>16}" for name, amount in lines]
    total = sum((a for _, a in lines), Decimal("0"))
    out.append(f"{'Total':<20}{money(total, currency):>16}")
    return "\n".join(out) + "\n"
