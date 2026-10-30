def parse_amounts(text: str) -> list[float]:
    """Parse comma-separated amounts like '1,2.5, 3'. Blank entries are skipped."""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    return out
