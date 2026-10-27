import json
import urllib.error
import urllib.request

RATES_URL = "https://rates.example/rate/{currency}"


class RateError(Exception):
    pass


def get_rate(currency: str) -> float:
    url = RATES_URL.format(currency=currency)
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RateError(f"could not fetch rate for {currency}: {e}") from e
    try:
        return float(json.loads(body)["rate"])
    except (ValueError, KeyError, TypeError) as e:
        raise RateError(f"bad rate payload for {currency}") from e
