from decimal import Decimal
from unittest import mock

import pytest
from shop import rates


def _resp(body: bytes):
    r = mock.MagicMock()
    r.read.return_value = body
    r.__enter__.return_value = r
    return r


def test_get_rate():
    with mock.patch("urllib.request.urlopen", return_value=_resp(b'{"rate": "1.5"}')):
        assert rates.get_rate("EUR") == Decimal("1.5")


def test_rate_error():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
        with pytest.raises(rates.RateError):
            rates.get_rate("EUR")
