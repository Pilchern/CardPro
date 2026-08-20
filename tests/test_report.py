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
        dollar_savings=100.0,
    )
    defaults.update(overrides)
    return Listing(**defaults)


def test_empty_deals_gives_nothing_today_email():
    subject, body = report.build_report([], 30, date(2026, 8, 10))
    assert "No deals today" in subject
    assert "August 10, 2026" in body


def test_report_ranks_by_dollar_savings_descending():
    # "low" has the higher percent-off but the smaller dollar amount --
    # ranking must go by $ saved, not percent, per the "worth your time" ask.
    low = make_listing(id="low", pct_under_market=90.0, dollar_savings=9.0)
    high = make_listing(id="high", pct_under_market=25.0, dollar_savings=250.0)
    ranked = report.rank_deals([low, high])
    assert [d.id for d in ranked] == ["high", "low"]


def test_report_includes_key_fields():
    listing = make_listing()
    subject, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "1 card deal found" in subject
    assert "$100.00" in body
    assert "$200.00" in body
    assert "50% under market" in body
    assert "$100.00 saved" in body
    assert "eBay" in body
    assert listing.url in body


def test_fallback_comp_is_labeled_in_report():
    listing = make_listing(comp_is_fallback=True)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "active-listing proxy" in body


def test_craigslist_links_included_with_deals():
    listing = make_listing()
    links = {"Michael Jordan": "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"}
    _, body = report.build_report([listing], 30, date(2026, 8, 10), links)
    assert "Craigslist quick check" in body
    assert links["Michael Jordan"] in body


def test_craigslist_links_included_with_no_deals():
    links = {"Michael Jordan": "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"}
    subject, body = report.build_report([], 30, date(2026, 8, 10), links)
    assert "No deals today" in subject
    assert "Craigslist quick check" in body
    assert links["Michael Jordan"] in body


def test_no_craigslist_section_when_links_omitted():
    listing = make_listing()
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "Craigslist" not in body


def test_ebay_disabled_gives_not_configured_email_even_with_deals_passed():
    links = {"Michael Jordan": "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"}
    subject, body = report.build_report([], 30, date(2026, 8, 10), links, ebay_enabled=False)
    assert "eBay not configured" in subject
    assert "Craigslist quick check" in body
    assert links["Michael Jordan"] in body


def test_ebay_enabled_defaults_to_true_for_backwards_compatibility():
    listing = make_listing()
    subject, _ = report.build_report([listing], 30, date(2026, 8, 10))
    assert "eBay not configured" not in subject


def test_truncated_title_shows_grade_uncertain_caveat():
    listing = make_listing(title_truncated=True)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "grade uncertain" in body
    assert "eBay truncated the title" in body


def test_non_truncated_title_has_no_caveat():
    listing = make_listing(title_truncated=False)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "grade uncertain" not in body


def test_min_savings_dollars_shown_in_threshold_footer():
    listing = make_listing()
    _, body = report.build_report([listing], 30, date(2026, 8, 10), min_savings_dollars=15)
    assert "$15.00 saved" in body


def test_min_savings_dollars_shown_in_nothing_today_email():
    _, body = report.build_report([], 30, date(2026, 8, 10), min_savings_dollars=15)
    assert "$15.00" in body


def test_young_core_tag_shown():
    listing = make_listing(player_tier="young_core")
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "[YOUNG CORE]" in body


def test_rookie_card_tag_shown():
    listing = make_listing(is_rookie_card=True)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "[ROOKIE CARD]" in body


def test_both_tags_shown_together():
    listing = make_listing(player_tier="young_core", is_rookie_card=True)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "[YOUNG CORE + ROOKIE CARD]" in body


def test_legend_tier_has_no_tag():
    listing = make_listing(player_tier="legend", is_rookie_card=False)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    # the tag legend/header line always mentions these words to explain what
    # they mean when they DO show up -- what must be absent is the actual
    # bracketed per-entry tag
    assert "[YOUNG CORE]" not in body
    assert "[ROOKIE CARD]" not in body


def test_card_identity_line_shown_when_fields_known():
    from src.card_identity import CardIdentity, Field

    identity = CardIdentity(
        year=Field(2024, "high"),
        manufacturer=Field("Panini", "high"),
        set_name=Field("Prizm", "high"),
        parallel=Field("Silver", "high"),
        card_number=Field("123", "high"),
        serial_number=Field("23/99", "high"),
    )
    listing = make_listing(card_identity=identity)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "Card: 2024 Panini Prizm Silver #123 (23/99)" in body


def test_no_card_identity_line_when_nothing_extracted():
    from src.card_identity import CardIdentity

    listing = make_listing(card_identity=CardIdentity())
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "Card:" not in body


def test_no_card_identity_line_when_identity_absent():
    listing = make_listing(card_identity=None)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "Card:" not in body


def test_auto_tag_shown_when_identity_flags_autograph():
    from src.card_identity import CardIdentity, Field

    identity = CardIdentity(is_autograph=Field(True, "high"))
    listing = make_listing(card_identity=identity)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "[AUTO]" in body


def test_mem_tag_shown_when_identity_flags_memorabilia():
    from src.card_identity import CardIdentity, Field

    identity = CardIdentity(is_memorabilia=Field(True, "high"))
    listing = make_listing(card_identity=identity)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "[MEM]" in body


def test_comp_confidence_shown_when_set():
    listing = make_listing(comp_level_matched="exact", comp_confidence="high")
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "HIGH confidence -- exact card match" in body


def test_comp_confidence_omitted_when_not_set():
    """The eBay-API path's flag_deals doesn't set these fields -- the
    comp line must look exactly like it did before this feature existed."""
    listing = make_listing(comp_level_matched=None, comp_confidence=None)
    _, body = report.build_report([listing], 30, date(2026, 8, 10))
    assert "confidence" not in body
    assert "(n=5)" in body


def test_no_top_picks_section_when_few_deals():
    """With only a handful of deals, the full numbered list below is
    already short enough to skim -- a separate summary would just repeat it."""
    deals = [make_listing(id=str(i), dollar_savings=float(i)) for i in range(1, 4)]
    _, body = report.build_report(deals, 30, date(2026, 8, 10))
    assert "TOP PICKS" not in body


def test_top_picks_section_shown_with_many_deals_and_lists_top_three_by_savings():
    deals = [make_listing(id=str(i), player=f"Player {i}", dollar_savings=float(i)) for i in range(1, 6)]
    _, body = report.build_report(deals, 30, date(2026, 8, 10))

    assert "TOP PICKS:" in body
    top_section = body.split("TOP PICKS:")[1].split("\n\n")[0]
    assert "Player 5" in top_section
    assert "Player 4" in top_section
    assert "Player 3" in top_section
    assert "Player 2" not in top_section  # 4th-ranked, outside the top 3
    # still appears in the full numbered list below, just not the summary
    assert "Player 2" in body
