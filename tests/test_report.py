from datetime import date

from src.models import Listing
from src import report


def make_listing(**overrides) -> Listing:
    defaults = dict(
        id="1",
        source="ebay",
        title="Michael Jordan PSA 9",
        price=100.0,
        url="http://example.com/1",
        player="Michael Jordan",
        card_type="graded",
        grader="PSA",
        grade="9",
        comp_median=200.0,
        comp_sample_size=5,
        pct_under_market=50.0,
    )
    defaults.update(overrides)
    return Listing(**defaults)


def test_empty_deals_gives_nothing_today_email():
    subject, body = report.build_report([], 30, date(2026, 8, 10))
    assert "No deals today" in subject
    assert "August 10, 2026" in body


def test_report_ranks_by_pct_under_descending():
    low = make_listing(id="low", pct_under_market=35.0)
    high = make_listing(id="high", pct_under_market=60.0)
    ranked = report.rank_deals([low, high])
    assert [d.id for d in ranked] == ["high", "low"]


def test_report_includes_key_fields():
    listing = make_listing()
    subject, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "1 card deal found" in subject
    assert "$100.00" in body
    assert "$200.00" in body
    assert "50% under market" in body
    assert "eBay" in body
    assert listing.url in body


def test_fallback_comp_is_labeled_in_report():
    listing = make_listing(comp_is_fallback=True)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "active-listing proxy" in body
