from .money import format_price
from .rates import get_rate


class CartError(Exception):
    pass


class Cart:
    def __init__(self) -> None:
        self.items: list[tuple[str, int]] = []

    def add(self, name: str, cents: int) -> None:
        if cents < 0:
            raise CartError("negative price")
        self.items.append((name, cents))

    def total_cents(self) -> int:
        return sum(c for _, c in self.items)

    def total(self) -> str:
        return format_price(self.total_cents())

    def total_in(self, currency: str) -> str:
        rate = get_rate(currency)
        return format_price(round(self.total_cents() * rate))
