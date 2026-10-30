import json
import urllib.request
from decimal import Decimal

RATES_URL = "http://rates.example/rate/{currency}"


class RateError(Exception):
    def __init__(self, currency: str, reason: str) -> None:
        super().__init__(f"rate for {currency}: {reason}")
        self.currency = currency


def get_rate(currency: str) -> Decimal:
    try:
        with urllib.request.urlopen(RATES_URL.format(currency=currency), timeout=5) as resp:
            body = json.loads(resp.read())
        return Decimal(str(body["rate"]))
    except (OSError, ValueError, KeyError) as e:
        raise RateError(currency, str(e)) from e
