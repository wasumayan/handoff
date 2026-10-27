import io
import json
from unittest import mock

import pytest
from shop.cart import Cart
from shop.rates import RateError, get_rate


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_get_rate():
    with mock.patch("urllib.request.urlopen", lambda url, timeout=None: _Resp(json.dumps({"rate": 1.25}).encode())):
        assert get_rate("EUR") == 1.25


def test_get_rate_error():
    def boom(url, timeout=None):
        raise OSError("down")
    with mock.patch("urllib.request.urlopen", boom):
        with pytest.raises(RateError):
            get_rate("EUR")


def test_total_in():
    c = Cart()
    c.add("a", 400)
    with mock.patch("urllib.request.urlopen", lambda url, timeout=None: _Resp(b'{"rate": 0.5}')):
        assert c.total_in("GBP") == "$2.00"
