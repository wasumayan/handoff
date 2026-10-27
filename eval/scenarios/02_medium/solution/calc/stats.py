def total(xs: list[float]) -> float:
    return sum(xs)


def percent(part: float, whole: float) -> float:
    if whole == 0:
        return 0.0
    return part / whole * 100
