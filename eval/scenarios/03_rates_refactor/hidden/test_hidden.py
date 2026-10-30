import contextlib
from decimal import Decimal
from unittest import mock

import pytest
from shop import rates
from shop.cart import Cart


def _resp(body: bytes):
    r = mock.MagicMock()
    r.read.return_value = body
    r.__enter__.return_value = r
    return r


@contextlib.contextmanager
def urlopen(**kw):
    """Patch urlopen wherever the implementation reaches it."""
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch("urllib.request.urlopen", **kw))
        if hasattr(rates, "urlopen"):
            st.enter_context(mock.patch.object(rates, "urlopen", **kw))
        yield


def test_rate_is_exact_decimal():
    with urlopen(return_value=_resp(b'{"rate": "1.0842", "as_of": "2026-10-27"}')):
        r = rates.get_rate("EUR")
    assert isinstance(r, Decimal) and r == Decimal("1.0842")


def test_rate_error_carries_currency():
    with urlopen(side_effect=OSError("down")):
        with pytest.raises(rates.RateError) as ei:
            rates.get_rate("JPY")
    assert ei.value.currency == "JPY"


def test_total_in():
    c = Cart()
    c.add("a", 1000)
    c.add("b", 250)
    with urlopen(return_value=_resp(b'{"rate": "1.0842"}')):
        assert c.total_in("EUR") == "$13.55"


def test_no_requests():
    import pathlib
    src = "".join(p.read_text() for p in pathlib.Path(rates.__file__).parent.glob("*.py"))
    assert "import requests" not in src
