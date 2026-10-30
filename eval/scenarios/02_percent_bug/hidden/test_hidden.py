import inspect

import pytest
from calc import api
from calc.api import percent_of_total


def test_fixed():
    assert percent_of_total("25, 75", 0) == pytest.approx(25.0)
    assert percent_of_total("2, 2, 4", 2) == pytest.approx(50.0)


def test_rounded_to_two_decimals():
    assert percent_of_total("1, 2", 0) == 33.33
    assert percent_of_total("1, 2", 1) == 66.67


def test_signature_unchanged():
    assert list(inspect.signature(api.percent_of_total).parameters) == ["text", "index"]
