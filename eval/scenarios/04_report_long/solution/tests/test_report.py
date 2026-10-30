from report import totals


def test_totals_sum_amounts():
    rows = [{"customer_id": "C1", "amount": "1,50"}, {"customer_id": "C1", "amount": "2,00"}]
    assert totals(rows) == {"C1": 3.5}
