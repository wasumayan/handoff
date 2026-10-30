import pytest
from shop.cart import Cart, CartError


def test_total():
    c = Cart()
    c.add("a", 100)
    c.add("b", 250)
    assert c.total_cents() == 350
    assert c.total() == "$3.50"


def test_negative():
    with pytest.raises(CartError):
        Cart().add("x", -1)
