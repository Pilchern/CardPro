from src.models import Listing


def _listing(**overrides):
    defaults = dict(
        id="1", source="ebay-alert", title="Michael Jordan card", price=100.0,
        url="http://example.com/1", player="Michael Jordan", card_type="raw",
    )
    defaults.update(overrides)
    return Listing(**defaults)


def test_total_cost_falls_back_to_price_when_shipping_unknown():
    listing = _listing(price=100.0)  # shipping_price defaults to None
    assert listing.total_cost == 100.0


def test_total_cost_adds_known_shipping():
    listing = _listing(price=100.0, shipping_price=15.0)
    assert listing.total_cost == 115.0


def test_total_cost_handles_free_shipping():
    listing = _listing(price=100.0, shipping_price=0.0)
    assert listing.total_cost == 100.0


def test_total_cost_none_when_price_unknown():
    listing = _listing(price=None, shipping_price=10.0)
    assert listing.total_cost is None
