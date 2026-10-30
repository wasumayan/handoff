from .parse import parse_amounts
from .stats import percent, total


def percent_of_total(text: str, index: int) -> float:
    xs = parse_amounts(text)
    return round(percent(xs[index], total(xs)), 2)
