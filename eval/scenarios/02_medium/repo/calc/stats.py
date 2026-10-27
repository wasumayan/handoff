def total(xs: list[float]) -> float:
    return sum(xs)


def percent(part: float, whole: float) -> float:
    # BUG: integer division when both inputs are whole numbers passed as ints upstream
    if whole == 0:
        return 0.0
    return int(part) // int(whole) * 100
