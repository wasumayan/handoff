Refactor `shop/` in three steps:
1. Extract price formatting into `shop/money.py` with `format_price(cents: int) -> str` and use it from `shop/cart.py`.
2. Replace the hand-rolled HTTP fetch in `shop/rates.py` with `urllib.request`; `get_rate(currency)` returns a float and raises `RateError` on failure.
3. Add `Cart.total_in(currency) -> str`: convert the cart total with `get_rate` and format it with `format_price`.
All tests in `tests/` must pass. Add tests for steps 2 and 3.
