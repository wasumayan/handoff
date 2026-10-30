import pytest
from calc.api import percent_of_total
from calc.parse import parse_amounts
from calc.stats import total


def test_parse():
    assert parse_amounts("1, 2.5,,3") == [1.0, 2.5, 3.0]


def test_total():
    assert total([1.0, 2.0]) == 3.0


def test_percent_of_total():
    assert percent_of_total("25, 75", 0) == pytest.approx(25.0)
    assert percent_of_total("1, 3", 1) == pytest.approx(75.0)
