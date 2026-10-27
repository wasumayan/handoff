import io
import json
import pathlib
from unittest import mock

import pytest
from shop.cart import Cart
from shop.rates import RateError, get_rate

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(payload, exc=None):
    def f(url, timeout=None):
        if exc:
            raise exc
        return _Resp(json.dumps(payload).encode())
    return f


def test_get_rate_float():
    with mock.patch("urllib.request.urlopen", _urlopen({"rate": "1.5"})):
        assert get_rate("EUR") == 1.5
        assert isinstance(get_rate("EUR"), float)


def test_get_rate_error():
    import urllib.error
    with mock.patch("urllib.request.urlopen", _urlopen(None, urllib.error.URLError("down"))):
        with pytest.raises(RateError):
            get_rate("EUR")


def test_total_in():
    c = Cart()
    c.add("a", 1000)
    with mock.patch("urllib.request.urlopen", _urlopen({"rate": 2.0})):
        assert c.total_in("EUR") == "$20.00"


def test_no_requests_and_legacy_untouched():
    for p in (ROOT / "shop").glob("*.py"):
        assert "import requests" not in p.read_text(), p
        assert "from requests" not in p.read_text(), p
    assert (ROOT / "shop" / "legacy.py").read_text().startswith("# Scheduled for deletion.")
