Refactor `shop/` in three steps:
1. Extract price formatting into `shop/money.py` with `format_price(cents: int) -> str` and use it from `shop/cart.py`. (done)
2. Replace the hand-rolled HTTP fetch in `shop/rates.py` with `urllib.request` and make `get_rate(currency)` return a float, with a `RateError` on failure.
3. Add `Cart.total_in(currency)` that uses `get_rate` and `format_price`.
All tests in `tests/` must pass. Add tests for steps 2 and 3.
