import inspect

import pytest
from calc import api
from calc.api import percent_of_total


def test_fixed():
    assert percent_of_total("25, 75", 0) == pytest.approx(25.0)
    assert percent_of_total("1, 3", 1) == pytest.approx(75.0)
    assert percent_of_total("2, 2, 4", 2) == pytest.approx(50.0)


def test_signature_unchanged():
    sig = inspect.signature(api.percent_of_total)
    assert list(sig.parameters) == ["text", "index"]
