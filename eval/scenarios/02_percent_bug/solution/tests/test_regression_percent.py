from calc.api import percent_of_total


def test_regression_percent_floor_division():
    assert percent_of_total("25, 75", 0) == 25.0
