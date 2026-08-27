"""Tests for the decision-first report.

Two things are being protected here, and they are different:

* **Classification** -- which section a listing lands in. This is the part
  most likely to be wrong and the part a human will never notice is wrong,
  so it is tested through ``classify_sections`` directly rather than by
  grepping rendered text.
* **Honesty of the rendered text** -- that a bid is never called a price,
  that a missing comp produces no discount, that unknown shipping is never
  $0, that asking comps are labelled as asking comps. These are grep tests
  on purpose: the exact words are the product.
"""
from datetime import date
from types import SimpleNamespace

from src import card_identity, desirability, focus, reasons, report
from src.card_identity import CardIdentity, Field
from src.comps import CompMatch, CompStatsV2
from src.models import Listing

RUN_DATE = date(2026, 8, 10)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def make_stats(**overrides) -> CompStatsV2:
    defaults = dict(
        median=200.0,
        mean=205.0,
        sample_size=7,
        trimmed_count=0,
        minimum=180.0,
        maximum=230.0,
        newest_date="2026-08-07",
        oldest_date="2026-07-01",
        age_days_newest=3,
        age_days_oldest=40,
        dispersion=0.05,
        basis="asking",
        is_stale=False,
        is_dispersed=False,
    )
    defaults.update(overrides)
    return CompStatsV2(**defaults)


def make_match(level="exact", confidence="medium", flag_eligible=True, blocked=(), **stat_overrides):
    return CompMatch(
        stats=make_stats(**stat_overrides),
        level=level,
        flag_eligible=flag_eligible,
        confidence=confidence,
        blocked_reasons=tuple(blocked),
    )


def make_listing(**overrides) -> Listing:
    """A plain fixed-price opportunity: $100 all-in against a $200 exact
    comp. With the default immediate thresholds ($150 / 40%) it clears the
    discount but not the dollar figure, so it lands in TOP OPPORTUNITIES --
    the least surprising place for a default fixture to sit.
    """
    defaults = dict(
        id="1",
        source="ebay",
        title="Michael Jordan 1986 Fleer #57 PSA 9",
        price=100.0,
        url="http://example.com/1",
        player="Michael Jordan",
        card_type="graded",
        grader="PSA",
        grade="9",
        listing_type="fixed_price",
        matched_players=("Michael Jordan",),
        comp_match=make_match(),
        market_value=200.0,
        pct_under_market=50.0,
        dollar_savings=100.0,
        is_opportunity=True,
    )
    defaults.update(overrides)
    listing = Listing(**defaults)
    # The pipeline fills desirable_attributes in for every listing before the
    # report ever sees one (src/main.py), so the fixture does too -- deriving
    # it here rather than hand-listing attributes keeps the tag row tested
    # against the same definition of "interesting" the deal gate uses. Pass
    # ``desirable_attributes=`` explicitly to test a specific combination.
    if "desirable_attributes" not in overrides:
        listing.desirable_attributes = desirability.attributes_of(listing)
    return listing


def make_economics(**overrides):
    from src import economics

    acquisition = economics.Acquisition(
        overrides.pop("price", 100.0), overrides.pop("shipping", 5.0), 0.0
    )
    return economics.evaluate(acquisition, overrides.pop("market", 200.0), economics.FeeModel.ebay_default())


def make_target_hit(band="great_buy", threshold=95.0, label="Kyle Teel Chrome Refractor"):
    from src import targets

    return targets.TargetHit(
        target=targets.TargetCard(label=label, player="Kyle Teel", buy_zone=110.0, great_buy=95.0),
        band=band,
        threshold=threshold,
    )


def body_of(deals, **kwargs):
    kwargs.setdefault("threshold_pct", 30)
    threshold = kwargs.pop("threshold_pct")
    return report.build_report(deals, threshold, RUN_DATE, **kwargs)[1]


def flat(text: str) -> str:
    """Collapse whitespace so a phrase assertion is testing the words, not
    where textwrap happened to break the line. Line breaks are a layout
    decision that should be free to change; the wording is the contract."""
    return " ".join(text.split())


def section_of(body: str, key: str) -> str:
    """The rendered text of one section, so a test can assert a card is in
    THIS section rather than merely somewhere in the email."""
    title = report.SECTION_TITLES[key]
    assert title in body, "section {!r} is not in the report".format(key)
    after = body.split(title, 1)[1]
    for other in report.SECTION_ORDER:
        other_title = report.SECTION_TITLES[other]
        if other_title != title and other_title in after:
            after = after.split(other_title, 1)[0]
    return after


# ---------------------------------------------------------------------------
# ranking (unchanged behaviour, still the right primary key)
# ---------------------------------------------------------------------------


def test_rank_deals_ranks_by_dollar_savings_descending():
    # "low" has the higher percent-off but the smaller dollar amount --
    # ranking must go by $ saved, not percent, per the "worth your time" ask.
    low = make_listing(id="low", pct_under_market=90.0, dollar_savings=9.0)
    high = make_listing(id="high", pct_under_market=25.0, dollar_savings=250.0)
    assert [d.id for d in report.rank_deals([low, high])] == ["high", "low"]


def test_rank_deals_treats_missing_savings_as_zero():
    known = make_listing(id="known", dollar_savings=10.0)
    unknown = make_listing(id="unknown", dollar_savings=None, market_value=None, comp_match=None)
    assert [d.id for d in report.rank_deals([unknown, known])] == ["known", "unknown"]


def test_sections_are_ordered_by_dollars_saved_within_a_section():
    # Both stay under the ACT NOW dollar threshold so they share a section.
    small = make_listing(id="small", dollar_savings=20.0)
    big = make_listing(id="big", dollar_savings=140.0)
    sections = report.classify_sections([small, big], threshold_pct=30)
    assert [d.id for d in sections[report.SECTION_TOP_OPPORTUNITIES]] == ["big", "small"]


def test_build_report_does_not_mutate_the_list_it_is_given():
    deals = [make_listing(id="a", dollar_savings=10.0), make_listing(id="b", dollar_savings=99.0)]
    report.build_report(deals, 30, RUN_DATE)
    assert [d.id for d in deals] == ["a", "b"]


def test_report_is_byte_identical_for_identical_input():
    deals = [make_listing()]
    assert report.build_report(deals, 30, RUN_DATE) == report.build_report(deals, 30, RUN_DATE)


# ---------------------------------------------------------------------------
# classify_sections: which section, and only one of them
# ---------------------------------------------------------------------------


def test_classify_sections_always_returns_every_key_in_order():
    sections = report.classify_sections([], threshold_pct=30)
    assert list(sections) == list(report.SECTION_ORDER)
    assert all(value == [] for value in sections.values())


def test_act_now_requires_savings_and_discount_and_confidence():
    deal = make_listing(dollar_savings=300.0, pct_under_market=55.0)
    sections = report.classify_sections(
        [deal], threshold_pct=30, immediate_min_savings=150.0, immediate_min_discount_pct=40.0
    )
    assert sections[report.SECTION_ACT_NOW] == [deal]
    assert sections[report.SECTION_TOP_OPPORTUNITIES] == []


def test_act_now_not_triggered_below_the_dollar_threshold():
    deal = make_listing(dollar_savings=100.0, pct_under_market=55.0)
    sections = report.classify_sections([deal], threshold_pct=30, immediate_min_savings=150.0)
    assert sections[report.SECTION_ACT_NOW] == []
    assert sections[report.SECTION_TOP_OPPORTUNITIES] == [deal]


def test_act_now_not_triggered_below_the_discount_threshold():
    deal = make_listing(dollar_savings=300.0, pct_under_market=35.0)
    sections = report.classify_sections(
        [deal], threshold_pct=30, immediate_min_savings=150.0, immediate_min_discount_pct=40.0
    )
    assert sections[report.SECTION_ACT_NOW] == []
    assert sections[report.SECTION_TOP_OPPORTUNITIES] == [deal]


def test_low_confidence_opportunity_never_reaches_act_now_or_top():
    deal = make_listing(
        dollar_savings=300.0, pct_under_market=60.0, comp_match=make_match(confidence="low")
    )
    sections = report.classify_sections([deal], threshold_pct=30)
    assert sections[report.SECTION_ACT_NOW] == []
    assert sections[report.SECTION_TOP_OPPORTUNITIES] == []
    assert sections[report.SECTION_WATCH] == [deal]


def test_a_young_core_card_with_a_real_comp_keeps_its_numbers():
    """It goes to WATCH, not YOUNG CORE.

    YOUNG CORE prints no market value, no discount and no confidence -- that
    is what makes it safe. It is therefore the wrong home for a card that
    HAS a comp worth standing behind. This one is 50% under market against a
    flag-eligible comp; filing it under a tier label would print none of
    that, under a header saying no comp CardPro will stand behind was found.
    """
    deal = make_listing(player_tier="young_core", comp_match=make_match(confidence="low"))
    sections = report.classify_sections([deal], threshold_pct=30)
    assert sections[report.SECTION_INVESTMENT] == []
    assert sections[report.SECTION_WATCH] == [deal]


def test_strong_young_core_opportunity_stays_in_top_opportunities():
    deal = make_listing(player_tier="young_core")
    sections = report.classify_sections([deal], threshold_pct=30)
    assert sections[report.SECTION_TOP_OPPORTUNITIES] == [deal]
    assert sections[report.SECTION_INVESTMENT] == []


def test_auctions_are_their_own_section():
    deal = make_listing(listing_type="auction", is_opportunity=False, bid_count=3, time_left_text="2d 04h")
    sections = report.classify_sections([deal], threshold_pct=30)
    assert sections[report.SECTION_AUCTIONS] == [deal]


def test_auctions_sort_by_time_left_presence_then_soonest():
    known_soon = make_listing(id="soon", listing_type="auction", is_opportunity=False, time_left_text="0d 03h")
    known_late = make_listing(id="late", listing_type="auction", is_opportunity=False, time_left_text="4d 00h")
    unknown = make_listing(id="unknown", listing_type="auction", is_opportunity=False, time_left_text=None)
    sections = report.classify_sections([unknown, known_late, known_soon], threshold_pct=30)
    assert [d.id for d in sections[report.SECTION_AUCTIONS]] == ["soon", "late", "unknown"]


def test_offer_section_requires_best_offer_no_opportunity_and_a_real_comp():
    offerable = make_listing(id="offer", has_best_offer=True, is_opportunity=False, pct_under_market=5.0)
    no_comp = make_listing(
        id="nocomp", has_best_offer=True, is_opportunity=False, comp_match=None, market_value=None,
        pct_under_market=None, dollar_savings=None,
    )
    already_a_deal = make_listing(id="deal", has_best_offer=True, is_opportunity=True)
    sections = report.classify_sections([offerable, no_comp, already_a_deal], threshold_pct=30)
    assert [d.id for d in sections[report.SECTION_OFFERS]] == ["offer"]
    assert already_a_deal in sections[report.SECTION_TOP_OPPORTUNITIES]
    assert no_comp in sections[report.SECTION_NEEDS_REVIEW]


def test_offer_section_refuses_a_context_only_comp():
    """Suggesting "offer 30% below market" off a price-bracket estimate
    would be the circular valuation wearing a negotiation hat."""
    deal = make_listing(
        has_best_offer=True, is_opportunity=False, pct_under_market=5.0,
        comp_match=make_match(level="price_tier", confidence="low", flag_eligible=False,
                              blocked=("context_only_level",)),
    )
    sections = report.classify_sections([deal], threshold_pct=30)
    assert sections[report.SECTION_OFFERS] == []
    assert sections[report.SECTION_NEEDS_REVIEW] == [deal]


def test_watch_catches_close_but_under_threshold():
    close = make_listing(id="close", is_opportunity=False, pct_under_market=26.0, dollar_savings=52.0)
    far = make_listing(id="far", is_opportunity=False, pct_under_market=4.0, dollar_savings=8.0)
    sections = report.classify_sections([close, far], threshold_pct=30)
    assert [d.id for d in sections[report.SECTION_WATCH]] == ["close"]
    assert far not in sections[report.SECTION_WATCH]


def test_watch_catches_big_dollar_savings_at_a_modest_percentage():
    """$500 off a $5,000 slab is 10% and obviously worth a look."""
    deal = make_listing(id="big", is_opportunity=False, price=4500.0, market_value=5000.0,
                        pct_under_market=10.0, dollar_savings=500.0)
    sections = report.classify_sections([deal], threshold_pct=30, immediate_min_savings=150.0)
    assert sections[report.SECTION_WATCH] == [deal]


def test_a_modest_dollar_saving_does_not_turn_watch_into_a_dumping_ground():
    """Measured against the ACT NOW dollar bar, not the ordinary minimum --
    at a $25 minimum the dollar rule swallowed every mildly discounted
    listing in the run, price drops included."""
    deal = make_listing(id="drop", is_opportunity=False, is_price_drop=True, previous_price=260.0,
                        pct_under_market=12.5, dollar_savings=30.0)
    sections = report.classify_sections([deal], threshold_pct=30, immediate_min_savings=150.0)
    assert sections[report.SECTION_WATCH] == []
    assert sections[report.SECTION_PRICE_DROPS] == [deal]


def test_needs_review_catches_context_only_comps():
    deal = make_listing(
        is_opportunity=False,
        comp_match=make_match(level="price_tier", confidence="low", flag_eligible=False,
                              blocked=("context_only_level",)),
        market_value=44.5,
        pct_under_market=60.0,
        dollar_savings=26.5,
    )
    sections = report.classify_sections([deal], threshold_pct=30)
    assert sections[report.SECTION_NEEDS_REVIEW] == [deal]


def test_needs_review_catches_listings_with_no_comp_at_all():
    deal = make_listing(
        is_opportunity=False, comp_match=None, market_value=None, pct_under_market=None, dollar_savings=None
    )
    assert report.classify_sections([deal], threshold_pct=30)[report.SECTION_NEEDS_REVIEW] == [deal]


def test_needs_review_catches_untrustworthy_identity():
    deal = make_listing(is_opportunity=False, pct_under_market=1.0, title_truncated=True)
    assert report.classify_sections([deal], threshold_pct=30)[report.SECTION_NEEDS_REVIEW] == [deal]


def test_price_drops_section():
    deal = make_listing(is_opportunity=False, is_price_drop=True, previous_price=140.0,
                        pct_under_market=2.0, dollar_savings=4.0)
    assert report.classify_sections([deal], threshold_pct=30)[report.SECTION_PRICE_DROPS] == [deal]


def test_a_listing_is_claimed_by_exactly_one_section():
    """A price-dropped auction that is also an opportunity must not be
    reported three times -- the first section wins."""
    deal = make_listing(
        dollar_savings=300.0, pct_under_market=60.0, is_price_drop=True, previous_price=500.0,
        has_best_offer=True,
    )
    sections = report.classify_sections([deal], threshold_pct=30, immediate_min_savings=150.0)
    appearances = [key for key in report.SECTION_ORDER if deal in sections[key]]
    assert appearances == [report.SECTION_ACT_NOW]


def test_the_same_listing_id_arriving_twice_is_reported_once():
    """Two saved searches can surface the same eBay item in one run."""
    first = make_listing(id="dup", dollar_savings=100.0)
    second = make_listing(id="dup", dollar_savings=100.0)
    sections = report.classify_sections([first, second], threshold_pct=30)
    assert len(sections[report.SECTION_TOP_OPPORTUNITIES]) == 1


def test_target_hits_may_duplicate_another_section():
    """"The card you asked for" and "this is underpriced" are different
    questions, so a card may legitimately answer both."""
    deal = make_listing(dollar_savings=300.0, pct_under_market=60.0, target_hit=make_target_hit())
    sections = report.classify_sections([deal], threshold_pct=30, immediate_min_savings=150.0)
    assert deal in sections[report.SECTION_ACT_NOW]
    assert deal in sections[report.SECTION_TARGET_HITS]


def test_target_hits_put_buy_zone_cards_before_above_zone_cards():
    above = make_listing(id="above", is_opportunity=False, pct_under_market=1.0,
                         target_hit=make_target_hit(band=None, threshold=None))
    inside = make_listing(id="inside", is_opportunity=False, pct_under_market=1.0,
                          target_hit=make_target_hit())
    sections = report.classify_sections([above, inside], threshold_pct=30)
    assert [d.id for d in sections[report.SECTION_TARGET_HITS]] == ["inside", "above"]


def test_target_hit_alone_does_not_claim_a_listing_out_of_needs_review():
    deal = make_listing(
        is_opportunity=False, comp_match=None, market_value=None, pct_under_market=None,
        dollar_savings=None, target_hit=make_target_hit(),
    )
    sections = report.classify_sections([deal], threshold_pct=30)
    assert deal in sections[report.SECTION_TARGET_HITS]
    assert deal in sections[report.SECTION_NEEDS_REVIEW]


# ---------------------------------------------------------------------------
# sections appearing / not appearing in the rendered email
# ---------------------------------------------------------------------------


def test_empty_sections_are_omitted_entirely():
    body = body_of([make_listing()])
    assert report.SECTION_TITLES[report.SECTION_TOP_OPPORTUNITIES] in body
    for key in (report.SECTION_ACT_NOW, report.SECTION_AUCTIONS, report.SECTION_OFFERS,
                report.SECTION_WATCH, report.SECTION_NEEDS_REVIEW, report.SECTION_PRICE_DROPS,
                report.SECTION_TARGET_HITS, report.SECTION_INVESTMENT):
        assert report.SECTION_TITLES[key] not in body


def test_each_section_renders_when_it_has_content():
    deals = [
        make_listing(id="act", dollar_savings=400.0, pct_under_market=60.0),
        make_listing(id="top", dollar_savings=100.0, pct_under_market=50.0),
        make_listing(id="target", is_opportunity=False, pct_under_market=1.0, dollar_savings=2.0,
                     target_hit=make_target_hit()),
        make_listing(id="invest", player_tier="young_core", is_opportunity=False,
                     comp_match=None, market_value=None, pct_under_market=None,
                     dollar_savings=None, price=30.0,
                     title="2024 Panini Prizm Caleb Williams Silver Prizm #301",
                     card_identity=card_identity.extract_card_identity(
                         "2024 Panini Prizm Caleb Williams Silver Prizm #301"),
                     card_type="raw", grader=None, grade=None, is_rookie_card=False),
        make_listing(id="auction", listing_type="auction", is_opportunity=False, bid_count=2,
                     time_left_text="0d 05h", max_rational_bid=150.0),
        make_listing(id="offer", has_best_offer=True, is_opportunity=False, pct_under_market=5.0),
        # No comp: a card with one belongs in a section that prints it.
        make_listing(id="cool", is_opportunity=False, comp_match=None, market_value=None,
                     pct_under_market=None, dollar_savings=None,
                     title="2024 Panini Prizm Caleb Williams Auto RC #301 /99",
                     card_identity=card_identity.extract_card_identity(
                         "2024 Panini Prizm Caleb Williams Auto RC #301 /99"),
                     is_rookie_card=True, price=60.0, card_type="raw", grader=None, grade=None),
        make_listing(id="cheap", is_opportunity=False, comp_match=None, market_value=None,
                     pct_under_market=None, dollar_savings=None,
                     title="2024 Panini Prizm Caleb Williams Silver Prizm #301",
                     card_identity=card_identity.extract_card_identity(
                         "2024 Panini Prizm Caleb Williams Silver Prizm #301"),
                     price=4.0, card_type="raw", grader=None, grade=None),
        make_listing(id="watch", is_opportunity=False, pct_under_market=25.0, dollar_savings=50.0),
        make_listing(id="review", is_opportunity=False, pct_under_market=1.0,
                     comp_match=make_match(flag_eligible=False, blocked=("context_only_level",))),
        make_listing(id="drop", is_opportunity=False, is_price_drop=True, previous_price=140.0,
                     pct_under_market=1.0, dollar_savings=2.0),
    ]
    body = body_of(deals)
    for key in report.SECTION_ORDER:
        assert report.SECTION_TITLES[key] in body, key


def test_sections_render_in_the_declared_order():
    deals = [
        make_listing(id="act", dollar_savings=400.0, pct_under_market=60.0),
        make_listing(id="auction", listing_type="auction", is_opportunity=False),
        make_listing(id="drop", is_opportunity=False, is_price_drop=True, previous_price=140.0,
                     pct_under_market=1.0),
    ]
    body = body_of(deals)
    positions = [
        body.index(report.SECTION_TITLES[key])
        for key in report.SECTION_ORDER
        if report.SECTION_TITLES[key] in body
    ]
    assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# the per-card thesis
# ---------------------------------------------------------------------------


def test_link_line_names_the_source():
    body = body_of([make_listing(source="ebay-alert")])
    assert "http://example.com/1  (eBay (saved-search alert))" in body


def test_thesis_block_shows_cost_market_discount_and_link():
    listing = make_listing(price=100.0, shipping_price=5.0)
    body = body_of([listing])
    assert "$100.00 + $5.00 shipping = $105.00 total" in flat(body)
    assert "$200.00" in body
    assert "$100.00 below market (50.0%)" in body
    assert listing.url in body


def test_market_line_discloses_level_sample_basis_range_and_recency():
    body = body_of([make_listing()])
    text = flat(body)
    assert "exact card + grade" in text
    assert "7 asking comps" in text
    assert "range $180.00-$230.00" in text
    assert "newest 3 days old" in text
    assert "oldest 40 days old" in text


def test_comp_level_is_translated_into_english_never_printed_raw():
    body = body_of([make_listing(comp_match=make_match(level="same_set", flag_eligible=False),
                                 is_opportunity=False, pct_under_market=1.0)])
    assert "same year/set, parallel unconfirmed" in body
    assert "same_set" not in body


def test_asking_basis_is_called_out_in_the_confidence_line():
    """The single most important caveat in the product right now."""
    body = body_of([make_listing()])
    assert "asking comps" in flat(body)
    assert "asking-price comps only (what sellers want, not what buyers paid)" in flat(body)


def test_sold_basis_says_sold_and_drops_the_asking_caveat():
    body = body_of([make_listing(comp_match=make_match(basis="sold", confidence="high"))])
    text = flat(body)
    assert "7 sold comps" in text
    assert "based on confirmed sold prices" in text
    assert "what sellers want, not what buyers paid" not in text


def test_confidence_states_the_word_and_the_reasons():
    body = body_of([make_listing(comp_match=make_match(confidence="medium", sample_size=3))])
    assert "Confidence  MEDIUM --" in body
    assert "n=3" in flat(body)
    assert "thin sample" in flat(body)


def test_no_comp_prints_no_discount_line_and_says_so_plainly():
    listing = make_listing(
        comp_match=None, market_value=None, pct_under_market=None, dollar_savings=None,
        is_opportunity=False,
    )
    body = body_of([listing])
    assert "no comparable listing was found" in flat(body)
    assert "Discount" not in body
    assert "below market" not in body


def test_no_comp_confidence_line_says_there_is_nothing_to_be_confident_about():
    listing = make_listing(comp_match=None, market_value=None, pct_under_market=None,
                           dollar_savings=None, is_opportunity=False)
    body = body_of([listing])
    assert "no comp backs this listing" in flat(body)


def test_discount_percent_shows_one_decimal_and_never_contradicts_the_threshold():
    """29.8% under a 30% threshold printed "(30%)" directly above "close to
    your 30% threshold without clearing it"."""
    near_miss = make_listing(price=245.0, shipping_price=None, market_value=349.0,
                             is_opportunity=False, pct_under_market=29.8, dollar_savings=104.0)
    body = body_of([near_miss], threshold_pct=30)
    text = flat(body)
    assert "$104.00 below market (29.8%)" in text
    assert "(30%)" not in text


def test_card_line_does_not_repeat_the_manufacturer_inside_the_set_name():
    identity = CardIdentity(
        year=Field(2024, "high"),
        manufacturer=Field("Topps", "high"),
        set_name=Field("Topps Chrome", "high"),
        card_number=Field("150", "high"),
    )
    body = body_of([make_listing(card_identity=identity)])
    assert "2024 Topps Chrome #150" in body
    assert "Topps Topps" not in body


def test_manufacturer_is_kept_when_it_adds_a_word():
    identity = CardIdentity(
        year=Field(2024, "high"),
        manufacturer=Field("Panini", "high"),
        set_name=Field("Prizm", "high"),
    )
    assert "2024 Panini Prizm" in body_of([make_listing(card_identity=identity)])


def test_economics_line_shown_with_assumptions_footer():
    body = body_of([make_listing(economics=make_economics())])
    assert "Economics" in body
    assert "profit" in body
    assert "ROI)" in body
    assert "ECONOMICS ASSUMPTIONS" in body
    assert "Selling fees assumed at 13.25%" in body


def test_economics_line_omitted_when_there_are_no_economics():
    body = body_of([make_listing(economics=None)])
    assert "Economics" not in body
    assert "ECONOMICS ASSUMPTIONS" not in body


def test_assumptions_are_deduped_across_cards():
    deals = [make_listing(id="a", economics=make_economics()),
             make_listing(id="b", economics=make_economics())]
    body = body_of(deals)
    assert body.count("Outbound shipping assumed $5.00 and supplies $1.00 per sale.") == 1


def test_assumptions_footer_holds_no_per_card_dollar_specifics():
    """Four different "Inbound shipping of $X" lines in one footer read as a
    contradiction, not as four facts."""
    deals = [
        make_listing(id="a", shipping_price=3.0, economics=make_economics(shipping=3.0, market=200.0)),
        make_listing(id="b", shipping_price=9.0, economics=make_economics(shipping=9.0, market=800.0)),
        make_listing(id="c", shipping_price=None, economics=make_economics(shipping=None, market=50.0)),
    ]
    footer = flat(body_of(deals).split("ECONOMICS ASSUMPTIONS")[1])
    assert "Inbound shipping of $" not in footer
    assert "Inbound shipping is UNKNOWN" not in footer
    assert "Selling fees assumed at 13.25%" in footer
    assert "Per-card figures" in footer


def test_assumptions_footer_keeps_the_haircut_percentage_when_shared():
    from src import economics

    def with_haircut(price, market):
        return economics.evaluate(
            economics.Acquisition(price, 5.0, 0.0), market, economics.FeeModel.ebay_default(),
            resale_haircut_pct=10.0,
        )

    deals = [make_listing(id="a", economics=with_haircut(100.0, 200.0)),
             make_listing(id="b", economics=with_haircut(400.0, 900.0))]
    footer = flat(body_of(deals).split("ECONOMICS ASSUMPTIONS")[1])
    assert "A resale haircut of 10.00% is applied to every expected sale price" in footer
    assert "$330" not in footer


def test_assumptions_footer_is_the_same_shape_on_a_one_card_and_a_three_card_day():
    one = body_of([make_listing(id="a", economics=make_economics())])
    three = body_of([
        make_listing(id="a", economics=make_economics()),
        make_listing(id="b", shipping_price=9.0, economics=make_economics(shipping=9.0, market=800.0)),
        make_listing(id="c", shipping_price=None, economics=make_economics(shipping=None, market=50.0)),
    ])
    assert (
        one.split("ECONOMICS ASSUMPTIONS")[1].split("---")[0]
        == three.split("ECONOMICS ASSUMPTIONS")[1].split("---")[0]
    )


# ---------------------------------------------------------------------------
# cheap cards: a card that cannot be flipped, and why a $4 card is here
# ---------------------------------------------------------------------------


def test_uneconomic_resale_replaces_the_economics_figures_with_a_collector_buy_line():
    """At $4-$8 the postage and fees exceed the whole spread. A "-10% ROI"
    there reads as a warning about the card when it is only arithmetic about
    cheap cards, so the line says what is actually true instead."""
    deal = make_listing(
        price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0,
        is_cheap=True, resale_uneconomic=True, economics=make_economics(price=4.0, market=8.0),
    )
    body = flat(body_of([deal]))
    assert (
        "Economics collector buy -- at this price postage and fees exceed the spread, "
        "so there is no flip here"
    ) in body
    assert "ROI)" not in body
    assert "profit" not in body


def test_the_discount_survives_on_a_card_that_cannot_be_flipped():
    """The card genuinely is below market -- that is the entire reason it is
    being shown. Only the flip figures are suppressed."""
    deal = make_listing(
        price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0,
        is_cheap=True, resale_uneconomic=True, economics=make_economics(price=4.0, market=8.0),
    )
    body = flat(body_of([deal]))
    assert "$4.00 below market (50.0%)" in body


def test_real_economics_line_is_untouched_when_the_card_is_flippable():
    body = flat(body_of([make_listing(economics=make_economics(), resale_uneconomic=False)]))
    assert "collector buy" not in body
    assert "ROI)" in body
    assert "profit" in body
    assert "[assumptions in footer]" in body


def test_a_cheap_card_says_in_one_line_why_it_is_not_noise():
    deal = make_listing(
        price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0, is_cheap=True,
        desirable_attributes=(desirability.ROOKIE, desirability.SERIAL_NUMBERED),
    )
    body = body_of([deal])
    # The raw line, columns and all -- this one is short enough not to wrap,
    # so the exact rendered form is worth pinning.
    assert (
        "   Cheap find  $4.00 card, but it is a rookie card, serial numbered -- not a base common"
    ) in body


def test_the_cheap_find_line_is_absent_at_or_above_the_price_ceiling():
    """A $100 card does not need to argue for itself; the price does that."""
    deal = make_listing(is_cheap=False, desirable_attributes=(desirability.ROOKIE,))
    assert "Cheap find" not in body_of([deal])


def test_no_cheap_find_line_when_desirability_established_nothing():
    """There is then no honest answer to "why is this worth $4", and half a
    sentence is worse than none. Such a listing is rejected upstream as a
    common card anyway."""
    deal = make_listing(
        price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0,
        is_cheap=True, desirable_attributes=(),
    )
    body = body_of([deal])
    assert "Cheap find" not in body
    assert "not a base common" not in body


def test_a_cheap_find_keeps_its_own_line_alongside_the_collector_buy_line():
    """The two answer different questions -- "can I flip this" and "why do I
    want it" -- so a cheap collector buy shows both."""
    deal = make_listing(
        price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0,
        is_cheap=True, resale_uneconomic=True, economics=make_economics(price=4.0, market=8.0),
        desirable_attributes=(desirability.ROOKIE,),
    )
    body = flat(body_of([deal]))
    assert "collector buy --" in body
    assert "Cheap find $4.00 card, but it is a rookie card -- not a base common" in body


# ---------------------------------------------------------------------------
# shipping honesty (unknown is never $0)
# ---------------------------------------------------------------------------


def test_shipping_unknown_is_stated_never_assumed_free():
    body = body_of([make_listing()])  # shipping_price defaults to None
    assert "shipping unknown -- actual cost may be higher" in flat(body)
    assert "free shipping" not in body


def test_known_shipping_shows_the_arithmetic():
    body = body_of([make_listing(price=100.0, shipping_price=12.50)])
    assert "$100.00 + $12.50 shipping = $112.50 total" in flat(body)


def test_free_shipping_is_stated_as_free_only_when_it_is_actually_zero():
    body = body_of([make_listing(shipping_price=0.0)])
    assert "free shipping" in body
    assert "shipping unknown" not in body


# ---------------------------------------------------------------------------
# the risks line
# ---------------------------------------------------------------------------


def test_risks_line_assembles_everything_that_is_true():
    listing = make_listing(
        shipping_price=None,
        title_truncated=True,
        listing_type="unknown",
        matched_players=("Michael Jordan", "Scottie Pippen"),
        negative_signals=("damaged",),
        comp_match=make_match(sample_size=3, is_stale=True, is_dispersed=True, dispersion=0.42,
                              age_days_newest=90, blocked=("thin_sample",)),
    )
    body = body_of([listing])
    risks = [line for line in body.splitlines() if "Risks" in line]
    assert risks, "expected a Risks line"
    text = flat(body)
    assert "thin sample (n=3)" in text
    assert "stale comps -- newest is 90 days old" in text
    assert "comps disagree with each other (dispersion 0.42)" in text
    assert "shipping unknown -- actual cost may be higher" in text
    assert "eBay truncated the title" in text
    assert "if this is an auction the price shown is a current bid, not an asking price" in text
    assert "2 watchlist players in the title" in text
    assert "condition problem stated in the title" in text


def test_risks_line_omitted_when_nothing_is_wrong():
    clean = make_listing(shipping_price=4.0, listing_type="fixed_price")
    body = body_of([clean])
    assert "Risks" not in body


def test_blocked_reasons_are_not_repeated_when_already_described():
    """A comp blocked for a thin sample already shows "thin sample (n=3)";
    re-printing the reasons.py phrasing of the same fact would just make the
    Risks line longer and less readable."""
    listing = make_listing(comp_match=make_match(sample_size=3, blocked=("thin_sample",)))
    body = body_of([listing])
    risks = [line for line in flat(body).split("Risks")[1].split("Title")[0].split("; ")]
    assert any(item.strip().startswith("thin sample (n=3)") for item in risks)
    assert flat(body).count("thin sample (n=3)") == 1
    assert "too few comparable observations to trust a median" not in flat(body)


def test_context_only_blocked_reason_is_surfaced_in_english():
    listing = make_listing(
        is_opportunity=False,
        pct_under_market=1.0,
        comp_match=make_match(level="price_tier", flag_eligible=False,
                              blocked=("context_only_level",)),
    )
    body = body_of([listing])
    assert "only context-only comps (card family or price tier)" in flat(body)


def test_negative_signals_are_labelled_in_english():
    listing = make_listing(negative_signals=("reprint",), is_opportunity=False, pct_under_market=1.0)
    body = body_of([listing])
    assert "title says REPRINT" in flat(body)
    assert "('reprint',)" not in body


# ---------------------------------------------------------------------------
# auctions: a bid is not a price
# ---------------------------------------------------------------------------


def test_auction_block_never_calls_the_bid_a_price():
    listing = make_listing(
        listing_type="auction", bid_count=7, time_left_text="0d 06h", price=120.0,
        max_rational_bid=150.0, is_opportunity=False, pct_under_market=40.0, dollar_savings=80.0,
    )
    body = body_of([listing])
    auction = section_of(body, report.SECTION_AUCTIONS)
    assert "Current bid" in auction
    assert "this is a CURRENT BID, not an asking price" in flat(auction)
    assert "Cost " not in auction
    assert "Asking " not in auction


def test_auction_block_shows_bids_time_left_and_max_rational_bid():
    listing = make_listing(listing_type="auction", bid_count=7, time_left_text="0d 06h",
                           max_rational_bid=150.0, is_opportunity=False)
    body = body_of([listing])
    assert "7 bids, 0d 06h remaining" in flat(body)
    assert "$150.00 -- the most you can pay and still keep your margin" in flat(body)


def test_auction_discount_is_scoped_to_the_current_bid_and_warned_about():
    listing = make_listing(listing_type="auction", price=120.0, is_opportunity=False,
                           pct_under_market=40.0, dollar_savings=80.0, max_rational_bid=150.0)
    body = body_of([listing])
    text = flat(body)
    assert "At this bid" in text
    assert "true only at the current bid, which will very likely rise" in text
    assert "This is not a confirmed deal." in text


def test_auction_without_a_market_value_says_no_ceiling_can_be_named():
    listing = make_listing(listing_type="auction", is_opportunity=False, comp_match=None,
                           market_value=None, pct_under_market=None, dollar_savings=None,
                           max_rational_bid=None)
    body = body_of([listing])
    assert "no rational ceiling to name" in flat(body)


def test_auction_with_unknown_bid_count_and_time_says_unknown():
    listing = make_listing(listing_type="auction", is_opportunity=False, bid_count=None,
                           time_left_text=None)
    body = body_of([listing])
    assert "bid count unknown" in flat(body)
    assert "time left unknown" in flat(body)


def test_an_opportunity_that_is_an_auction_still_renders_as_an_auction():
    """Even in ACT NOW, a bid must never be laid out like a price."""
    listing = make_listing(listing_type="auction", dollar_savings=400.0, pct_under_market=60.0,
                           bid_count=1, time_left_text="1d 00h", max_rational_bid=180.0)
    body = body_of([listing])
    act_now = section_of(body, report.SECTION_ACT_NOW)
    assert "this is a CURRENT BID, not an asking price" in flat(act_now)


def test_ending_soon_window_is_marked():
    soon = make_listing(id="soon", listing_type="auction", is_opportunity=False, time_left_text="0d 06h")
    body = body_of([soon], ending_soon_hours=24)
    assert "inside your 24h ending-soon window" in flat(body)


def test_ending_soon_window_not_marked_for_a_distant_auction():
    later = make_listing(id="later", listing_type="auction", is_opportunity=False, time_left_text="6d 00h")
    body = body_of([later], ending_soon_hours=24)
    assert "ending-soon window" not in body


# ---------------------------------------------------------------------------
# best offers
# ---------------------------------------------------------------------------


def test_offer_trio_anchors_on_the_threshold_ceiling():
    aggressive, fair, maximum = report.offer_trio(300.0, 25.0)
    assert maximum == 225.0  # market - the 25% discount threshold passed in
    assert round(fair, 2) == 213.75  # 5% below the ceiling
    assert round(aggressive, 2) == 191.25  # 15% below the ceiling


def test_offer_trio_is_monotonic_at_every_threshold():
    """The old market-anchored version produced a "fair" offer above the
    "maximum" at a 30% threshold, which is nonsense dressed as arithmetic."""
    for threshold in (0.0, 5.0, 20.0, 30.0, 50.0, 90.0):
        aggressive, fair, maximum = report.offer_trio(349.0, threshold)
        assert aggressive < fair < maximum, threshold


def test_offer_maximum_still_clears_the_users_own_threshold():
    market = 400.0
    _, _, maximum = report.offer_trio(market, 30.0)
    assert (market - maximum) / market * 100 == 30.0


def test_offer_block_shows_the_trio_and_its_arithmetic():
    listing = make_listing(has_best_offer=True, is_opportunity=False, price=280.0,
                           shipping_price=5.0, market_value=300.0, pct_under_market=5.0,
                           dollar_savings=15.0, comp_match=make_match(median=300.0))
    body = body_of([listing], threshold_pct=25)
    offers = section_of(body, report.SECTION_OFFERS)
    text = flat(offers)
    assert "aggressive $191.25 | fair $213.75 | maximum $225.00" in text
    assert "maximum is the highest price that still clears your 25% discount threshold" in text
    assert "market estimate of $300.00" in text
    assert "fair is 5% below it and aggressive 15% below it" in text


def test_offer_block_notes_known_shipping_in_the_basis_line():
    listing = make_listing(has_best_offer=True, is_opportunity=False, shipping_price=6.0,
                           market_value=300.0, pct_under_market=5.0, dollar_savings=15.0)
    body = body_of([listing])
    assert "subtract the $6.00 known shipping from what you actually offer" in flat(body)


def test_offer_block_treats_offers_as_ceilings_when_shipping_is_unknown():
    listing = make_listing(has_best_offer=True, is_opportunity=False, shipping_price=None,
                           market_value=300.0, pct_under_market=5.0, dollar_savings=15.0)
    body = body_of([listing])
    assert "shipping is unknown, so treat them as ceilings" in flat(body)


# ---------------------------------------------------------------------------
# tags and card description
# ---------------------------------------------------------------------------


def test_young_core_tag_shown():
    assert "YOUNG CORE" in body_of([make_listing(player_tier="young_core")])


def test_rookie_tag_shown():
    assert "ROOKIE" in body_of([make_listing(is_rookie_card=True)])


def test_tags_are_grouped_in_one_bracket():
    body = body_of([make_listing(player_tier="young_core", is_rookie_card=True)])
    assert "[YOUNG CORE + ROOKIE]" in body


def test_legend_tier_without_a_rookie_flag_has_no_attribute_tags():
    body = body_of([make_listing(player_tier="legend", is_rookie_card=False)])
    assert "[YOUNG CORE" not in body
    assert "[ROOKIE" not in body


def test_auto_and_mem_and_patch_tags_come_from_desirable_attributes():
    """The tag row renders whatever desirability established, using
    desirability's own short tags. It does not re-read the identity fields
    -- that second definition is what this change removed."""
    body = body_of([make_listing(desirable_attributes=(
        desirability.AUTOGRAPH, desirability.MEMORABILIA, desirability.PATCH,
    ))])
    assert "AUTO" in body
    assert "MEM" in body
    assert "PATCH" in body


def test_a_patch_card_is_tagged_patch_and_not_also_mem():
    """Behaviour inherited from desirability, deliberately: a patch IS
    memorabilia, and the report shows the more specific of the two rather
    than both. Previously the report derived its own tags from the identity
    and printed "MEM + PATCH" -- the exact drift this change removes."""
    identity = CardIdentity(
        is_autograph=Field(True, "high"),
        is_memorabilia=Field(True, "high"),
        is_patch=Field(True, "high"),
    )
    body = body_of([make_listing(card_identity=identity)])
    assert "AUTO" in body
    assert "PATCH" in body
    assert "MEM +" not in body


def test_tag_row_omits_graded_because_the_grade_is_already_in_the_headline():
    body = body_of([make_listing(card_type="graded", grader="PSA", grade="9")])
    assert "PSA 9" in body
    assert "GRADED" not in body


def test_unknown_attribute_names_still_get_a_tag_rather_than_vanishing():
    """desirability owns the vocabulary; a name the report has no tag for is
    shown raw rather than silently dropped, so a new attribute can never make
    a card appear in the report with nothing explaining why."""
    body = body_of([make_listing(desirable_attributes=("one_of_one",))])
    assert "ONE_OF_ONE" in body


def test_print_run_tag_shown_when_known():
    identity = CardIdentity(print_run=Field(99, "high"))
    assert "#'d /99" in body_of([make_listing(card_identity=identity)])


def test_target_tag_is_kept_separate_from_the_attribute_tags():
    body = body_of([make_listing(player_tier="young_core", target_hit=make_target_hit())])
    assert "[YOUNG CORE]  [TARGET: GREAT BUY]" in body


def test_card_description_is_built_from_identity_when_known():
    identity = CardIdentity(
        year=Field(2024, "high"),
        manufacturer=Field("Panini", "high"),
        set_name=Field("Prizm", "high"),
        parallel=Field("Silver", "high"),
        card_number=Field("123", "high"),
        serial_number=Field("23/99", "high"),
    )
    body = body_of([make_listing(card_identity=identity)])
    assert "2024 Panini Prizm Silver #123 (23/99)" in body


def test_card_description_falls_back_to_the_title_when_identity_is_empty():
    """No "Card:" line of empty fields, and no invented values -- the seller
    title is what we actually have."""
    listing = make_listing(card_identity=CardIdentity(), title="Some Untidy Seller Title")
    body = body_of([listing])
    assert "Some Untidy Seller Title" in body


def test_grade_is_shown_for_slabs_and_raw_is_labelled_raw():
    assert "PSA 9" in body_of([make_listing()])
    assert "raw/ungraded" in body_of([make_listing(card_type="raw", grader=None, grade=None)])


def test_truncated_title_is_flagged_as_a_risk():
    body = body_of([make_listing(title_truncated=True)])
    assert "eBay truncated the title, so the grade shown may not be the real grade" in flat(body)


def test_untruncated_title_has_no_truncation_caveat():
    body = body_of([make_listing(title_truncated=False)])
    assert "truncated" not in body


# ---------------------------------------------------------------------------
# header, subject, thresholds
# ---------------------------------------------------------------------------


def test_header_names_the_day_and_summarises_the_sections():
    body = body_of([make_listing()])
    assert body.startswith("CARDPRO DAILY -- Monday, August 10, 2026")
    assert "1 opportunity" in body


def test_subject_leads_with_act_now_when_there_is_one():
    deals = [make_listing(id="act", dollar_savings=400.0, pct_under_market=60.0),
             make_listing(id="top", dollar_savings=100.0, pct_under_market=50.0)]
    subject, _ = report.build_report(deals, 30, RUN_DATE)
    assert subject == "1 ACT NOW + 1 more (Aug 10)"


def test_subject_falls_back_to_opportunities_then_targets_then_auctions():
    opportunity = make_listing()
    assert report.build_report([opportunity], 30, RUN_DATE)[0] == "1 opportunity (Aug 10)"

    target = make_listing(is_opportunity=False, pct_under_market=1.0, target_hit=make_target_hit())
    assert report.build_report([target], 30, RUN_DATE)[0] == "1 target card hit (Aug 10)"

    auction = make_listing(listing_type="auction", is_opportunity=False, pct_under_market=1.0)
    assert report.build_report([auction], 30, RUN_DATE)[0] == "1 auction to review (Aug 10)"


def test_subject_says_no_opportunities_when_there_is_nothing():
    assert report.build_report([], 30, RUN_DATE)[0] == "No opportunities today (Aug 10)"


def test_thresholds_footer_shows_both_thresholds():
    body = body_of([make_listing()], min_savings_dollars=15)
    text = flat(body)
    assert "30%+ under market AND at least $15.00 saved" in text
    assert "ACT NOW additionally needs $150.00 saved and 40%+ off" in text


# ---------------------------------------------------------------------------
# the empty state
# ---------------------------------------------------------------------------


def test_empty_report_is_still_sent_and_does_not_read_as_a_failure():
    subject, body = report.build_report([], 30, RUN_DATE, min_savings_dollars=15)
    assert subject == "No opportunities today (Aug 10)"
    assert "August 10, 2026" in body
    assert "NOTHING CLEARED THE BAR TODAY." in body
    assert "That is a normal outcome, not a failure" in flat(body)
    assert "$15.00" in body


def test_empty_state_lists_the_top_rejection_reasons_from_run_stats():
    from src import observability

    stats = observability.RunStats(listings_matched_to_watchlist=40, unvalued=12)
    for _ in range(12):
        stats.rejections.record("no_comp_at_any_level")
    for _ in range(3):
        stats.rejections.record("below_discount_threshold")

    _, body = report.build_report([], 30, RUN_DATE, stats=stats)
    assert "Top reasons listings did not qualify:" in body
    assert "12 x no comparable listing at any level" in body
    assert "3 x discount is below your threshold" in body
    assert "Looked at 40 listings matched to your watchlist; 12 had no comp at any level." in flat(body)


def test_empty_state_renders_the_common_card_reason():
    """Cheap listings with nothing scarce about them are now the biggest
    rejection bucket on a quiet day, so the empty state has to name them in
    English. Nothing report-side special-cases it: the reason rendering is
    generic over stats.rejections, which is why a new reason works the day
    it is added."""
    from src import observability

    stats = observability.RunStats(listings_matched_to_watchlist=40, unvalued=2)
    for _ in range(9):
        stats.rejections.record(reasons.Reason.COMMON_CARD)

    _, body = report.build_report([], 30, RUN_DATE, stats=stats)
    assert "Top reasons listings did not qualify:" in body
    assert "9 x {}".format(reasons.label(reasons.Reason.COMMON_CARD)) in flat(body)
    assert "Cheap because it is common" in flat(body)


def test_empty_state_still_shows_auctions_and_needs_review():
    auction = make_listing(id="auction", listing_type="auction", is_opportunity=False,
                           pct_under_market=1.0, time_left_text="0d 02h")
    review = make_listing(id="review", is_opportunity=False, pct_under_market=1.0,
                          comp_match=make_match(flag_eligible=False, blocked=("context_only_level",)))
    body = body_of([auction, review])
    assert "NOTHING CLEARED THE BAR TODAY." in body
    assert report.SECTION_TITLES[report.SECTION_AUCTIONS] in body
    assert report.SECTION_TITLES[report.SECTION_NEEDS_REVIEW] in body
    assert "Everything CardPro did see is still below" in flat(body)


def test_empty_state_with_absolutely_nothing_says_so():
    _, body = report.build_report([], 30, RUN_DATE)
    assert "Nothing else reached the report either" in flat(body)


def test_needs_review_is_clearly_not_a_recommendation():
    review = make_listing(is_opportunity=False, pct_under_market=60.0, dollar_savings=26.5,
                          comp_match=make_match(level="price_tier", confidence="low",
                                                flag_eligible=False, blocked=("context_only_level",)))
    body = body_of([review])
    assert "NOT recommendations" in flat(body)
    assert "never allowed to declare a deal" in flat(body)


# ---------------------------------------------------------------------------
# footers: health, craigslist, search coverage
# ---------------------------------------------------------------------------


def test_system_health_footer_rendered_from_run_stats():
    from src import observability

    stats = observability.RunStats(
        alert_emails_scanned=2, listings_extracted=100, listings_matched_to_watchlist=80,
        identity_exact=10, identity_partial=40, identity_none=30, valued=60,
        valued_flag_eligible=12, unvalued=20, auctions=5, fixed_price=60,
        listing_type_unknown=15, shipping_known=50, shipping_unknown=30,
        opportunities_reported=1,
    )
    body = body_of([make_listing()], stats=stats)
    assert "SYSTEM HEALTH" in body
    for line in stats.health_lines():
        # Compared flattened: the footer wraps like every other block, and
        # where textwrap breaks a line is a layout decision, not a contract.
        assert flat(line) in flat(body)


def test_no_system_health_footer_when_no_stats_passed():
    assert "SYSTEM HEALTH" not in body_of([make_listing()])


def test_craigslist_links_included_with_deals():
    links = {"Michael Jordan": "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"}
    body = body_of([make_listing()], craigslist_links=links)
    assert "Craigslist quick check" in body
    assert links["Michael Jordan"] in body


def test_craigslist_links_included_when_there_are_no_deals():
    links = {"Michael Jordan": "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"}
    subject, body = report.build_report([], 30, RUN_DATE, links)
    assert subject == "No opportunities today (Aug 10)"
    assert "Craigslist quick check" in body
    assert links["Michael Jordan"] in body


def test_no_craigslist_section_when_links_omitted():
    assert "Craigslist" not in body_of([make_listing()])


def test_search_coverage_section_lists_suggestions_with_their_rationale():
    from src.search_terms import SuggestedSearch

    suggestions = {"Caleb Williams": [SuggestedSearch("Caleb Williams PSA 10", "graded top-pop market",
                                                      "Caleb Williams")]}
    body = body_of([make_listing()], search_suggestions=suggestions)
    assert "SEARCH COVERAGE" in body
    assert '"Caleb Williams PSA 10"  --  graded top-pop market' in body


def test_search_coverage_collapses_when_every_player_has_the_same_gaps():
    """20 players x 7 slices used to print ~140 lines and bury the cards."""
    from src.search_terms import PLAYER_SLICES, SuggestedSearch

    players = ["Player {:02d}".format(i) for i in range(1, 21)]
    suggestions = {
        player: [SuggestedSearch("{} {}".format(player, suffix), rationale, player)
                 for suffix, rationale in PLAYER_SLICES]
        for player in players
    }
    body = body_of([make_listing()], search_suggestions=suggestions)
    section = body.split("SEARCH COVERAGE")[1]
    assert len(section.strip().splitlines()) <= report.SEARCH_COVERAGE_MAX_LINES
    text = flat(section)
    assert "For each of these 20 players, add:" in text
    assert '"PSA 10"' in text
    assert '"refractor prizm"' in text
    assert "and 14 more" in text  # 6 named, 14 counted
    assert "no evidence of these saved searches for 20 players" in flat(body)
    # not a per-player dump
    assert "Player 20:" not in body


def test_search_coverage_lists_a_genuinely_different_gap_set_separately():
    from src.search_terms import SuggestedSearch

    suggestions = {
        "Alpha": [SuggestedSearch("Alpha PSA 10", "graded top-pop market", "Alpha")],
        "Beta": [SuggestedSearch("Beta PSA 10", "graded top-pop market", "Beta")],
        "Gamma": [SuggestedSearch("Gamma auto", "autographs are a different market", "Gamma")],
    }
    text = flat(body_of([make_listing()], search_suggestions=suggestions))
    assert "For each of these 2 players, add: \"PSA 10\"" in text
    assert "Gamma:" in text
    assert "autographs are a different market" in text


def test_search_coverage_is_capped_even_when_every_player_differs():
    from src.search_terms import SuggestedSearch

    suggestions = {
        "Player {:02d}".format(i): [SuggestedSearch("Player {:02d} slice {}".format(i, i),
                                                    "rationale {}".format(i),
                                                    "Player {:02d}".format(i))]
        for i in range(1, 31)
    }
    body = body_of([make_listing()], search_suggestions=suggestions)
    section = body.split("SEARCH COVERAGE")[1]
    assert len(section.strip().splitlines()) <= report.SEARCH_COVERAGE_MAX_LINES + 1
    assert "not listed)" in section


def test_no_search_coverage_section_when_none_passed():
    assert "SEARCH COVERAGE" not in body_of([make_listing()])


# ---------------------------------------------------------------------------
# eBay not configured
# ---------------------------------------------------------------------------


def test_ebay_disabled_gives_not_configured_email_and_keeps_craigslist():
    links = {"Michael Jordan": "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"}
    subject, body = report.build_report([], 30, RUN_DATE, links, ebay_enabled=False)
    assert "eBay not configured" in subject
    assert "This is expected, not an error" in flat(body)
    assert "Craigslist quick check" in body
    assert links["Michael Jordan"] in body


def test_ebay_enabled_defaults_to_true_for_backwards_compatibility():
    subject, _ = report.build_report([make_listing()], 30, RUN_DATE)
    assert "eBay not configured" not in subject


def test_old_positional_call_signature_still_works():
    """main.py has always called this with six positionals. Every new
    argument is keyword-only with a default so that stays true."""
    subject, body = report.build_report(
        [make_listing()], 30, RUN_DATE, None, True, 10.0
    )
    assert subject == "1 opportunity (Aug 10)"
    assert "CARDPRO DAILY" in body


# ---------------------------------------------------------------------------
# no black-box score, no buy instruction
# ---------------------------------------------------------------------------


def test_report_never_prints_a_blended_score_or_a_buy_instruction():
    body = body_of([make_listing(dollar_savings=400.0, pct_under_market=60.0,
                                 economics=make_economics())])
    lowered = body.lower()
    assert "score" not in lowered
    assert "buy now" not in lowered
    assert "you should buy" not in lowered


class TestWatchWhy:
    """The WATCH "Why here" line must name the actual reason with the actual
    numbers. It used to hedge ("close to your threshold, OR clearing it on
    percent but not dollars"), which read as nonsense next to a 5% discount
    that was really parked there for low confidence."""

    def test_below_threshold_states_the_gap(self):
        deal = make_listing(
            comp_match=make_match(),
            market_value=200.0,
            pct_under_market=14.2,
            dollar_savings=28.4,
            is_opportunity=False,
            rejection_reason=reasons.Reason.BELOW_DISCOUNT_THRESHOLD,
        )
        why = report._watch_why(deal, 30.0, 10.0)
        assert "14.2%" in why
        assert "30%" in why

    def test_below_min_savings_states_the_dollars(self):
        deal = make_listing(
            comp_match=make_match(),
            market_value=200.0,
            pct_under_market=32.0,
            dollar_savings=4.0,
            is_opportunity=False,
            rejection_reason=reasons.Reason.BELOW_MIN_SAVINGS,
        )
        why = report._watch_why(deal, 30.0, 10.0)
        assert "$4.00" in why
        assert "$10.00" in why

    def test_low_confidence_opportunity_says_so(self):
        deal = make_listing(
            comp_match=make_match(confidence="low"),
            market_value=200.0,
            pct_under_market=40.0,
            dollar_savings=80.0,
            is_opportunity=True,
        )
        assert "low-confidence" in report._watch_why(deal, 30.0, 10.0)

    def test_the_old_hedging_phrasing_is_gone(self):
        deal = make_listing(
            comp_match=make_match(),
            market_value=200.0,
            pct_under_market=14.2,
            dollar_savings=28.4,
            is_opportunity=False,
            rejection_reason=reasons.Reason.BELOW_DISCOUNT_THRESHOLD,
        )
        assert "or clearing it on percent" not in report._watch_why(deal, 30.0, 10.0)


def cheap_deal(**overrides):
    """A $4 rookie parallel at 67% off a $12.20 comp: below market, real, and
    not worth flipping once postage and fees are paid."""
    fields = dict(
        price=4.0,
        shipping_price=0.0,
        comp_match=make_match(),
        market_value=12.20,
        pct_under_market=67.2,
        dollar_savings=8.20,
        is_opportunity=True,
        is_cheap=True,
        resale_uneconomic=True,
        desirable_attributes=(desirability.ROOKIE, desirability.PARALLEL),
    )
    fields.update(overrides)
    return make_listing(**fields)


def test_cheap_find_line_reads_as_english_with_several_attributes():
    # "it is rookie card, non-base parallel" was the bug: describe() returns
    # noun phrases, and "it is" cannot be glued straight onto a list of them.
    text = report._cheap_find_text(cheap_deal())
    assert "but it is a rookie card, non-base parallel -- not a base common" in text
    assert "it is rookie card" not in text


def test_cheap_find_line_drops_the_article_before_an_adjective():
    # "it is a serial numbered" is the same bug with the article left in.
    text = report._cheap_find_text(
        cheap_deal(desirable_attributes=(desirability.SERIAL_NUMBERED,))
    )
    assert "but it is serial numbered -- not a base common" in text


def test_cheap_find_line_uses_an_before_a_vowel():
    text = report._cheap_find_text(cheap_deal(desirable_attributes=(desirability.AUTOGRAPH,)))
    assert "but it is an autograph -- not a base common" in text


def test_thresholds_footer_mentions_the_cheap_rule_when_a_cheap_card_is_shown():
    """The plain footer quotes the ordinary threshold, which understates what
    a cheap card had to clear to be here."""
    _, body = report.build_report([cheap_deal()], 30.0, RUN_DATE, min_savings_dollars=3.0)
    assert "Cheap cards clear a higher bar" in body


def test_thresholds_footer_stays_quiet_when_no_cheap_card_is_shown():
    deal = make_listing(
        price=250.0,
        comp_match=make_match(),
        market_value=400.0,
        pct_under_market=37.5,
        dollar_savings=150.0,
        is_opportunity=True,
        is_cheap=False,
    )
    _, body = report.build_report([deal], 30.0, RUN_DATE, min_savings_dollars=3.0)
    assert "Cheap cards clear a higher bar" not in body


def test_no_assumptions_footer_when_every_card_is_a_collector_buy():
    """The footer explains profit figures. On a day of only cheap finds there
    are none, and "every profit figure above rests on these" above zero of
    them explains arithmetic the report deliberately did not do."""
    deal = make_listing(
        price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0,
        is_cheap=True, resale_uneconomic=True, economics=make_economics(price=4.0, market=8.0),
    )
    body = body_of([deal])
    assert "ECONOMICS ASSUMPTIONS" not in body
    assert "collector buy" in body


def test_assumptions_footer_returns_when_a_flippable_card_is_present():
    cheap = make_listing(
        id="cheap", price=4.0, market_value=8.0, pct_under_market=50.0, dollar_savings=4.0,
        is_cheap=True, resale_uneconomic=True, economics=make_economics(price=4.0, market=8.0),
    )
    flippable = make_listing(
        id="flip", price=200.0, market_value=400.0, pct_under_market=50.0, dollar_savings=200.0,
        economics=make_economics(price=200.0, market=400.0),
    )
    body = body_of([cheap, flippable])
    assert "ECONOMICS ASSUMPTIONS" in body


# ---------------------------------------------------------------------------
# focus: what reaches the email, and how long it is
# ---------------------------------------------------------------------------


FOCUS = focus.FocusRules(
    price_ceiling=40.0,
    exceptional_min_discount_pct=50.0,
    exceptional_min_savings_dollars=100.0,
    max_listings=6,
    max_per_section=2,
)


def make_cheap(**overrides) -> Listing:
    """A $20 card against a $60 comp -- the shape the report is now built
    around, and small enough that nothing about it can be mistaken for the
    old $200-slab fixture."""
    defaults = dict(
        title="Kyle Teel 2024 Bowman Chrome Refractor RC #BCP-1",
        price=20.0,
        card_type="raw",
        grader=None,
        grade=None,
        market_value=60.0,
        comp_median=60.0,
        pct_under_market=66.7,
        dollar_savings=40.0,
    )
    defaults.update(overrides)
    return make_listing(**defaults)


def test_budget_order_covers_every_section():
    """A section left off BUDGET_ORDER would silently become the lowest
    priority in the email -- focus.trim appends unknown keys, and it would
    do it without complaining."""
    assert sorted(report.BUDGET_ORDER) == sorted(report.SECTION_ORDER)


def test_focus_line_says_why_the_email_is_short():
    """Otherwise a focused report and a dead market look identical."""
    body = body_of([make_cheap()], focus_rules=FOCUS)
    text = flat(body)
    assert "Focus: cards at or under $40.00" in text
    assert "top 6 listings" in text


def test_no_focus_line_when_focus_is_off():
    assert "Focus: cards at or under" not in body_of([make_cheap()])


def test_dear_card_is_left_out_and_the_footer_says_so():
    dear = make_listing(id="dear", url="http://example.com/dear", price=400.0,
                        market_value=600.0, pct_under_market=33.3, dollar_savings=200.0)
    body = body_of([make_cheap(id="cheap", url="http://example.com/cheap"), dear], focus_rules=FOCUS)
    assert "http://example.com/dear" not in body
    assert "http://example.com/cheap" in body
    assert "1 listing above your $40.00 focus ceiling left out" in flat(body)


def test_exceptional_dear_card_still_gets_in():
    """The ceiling is about what you shop for, not a claim that expensive
    cards are bad. A genuinely exceptional one is still worth your
    morning."""
    dear = make_listing(id="dear", url="http://example.com/dear", price=400.0,
                        market_value=1000.0, pct_under_market=60.0, dollar_savings=600.0)
    body = body_of([dear], focus_rules=FOCUS)
    assert "http://example.com/dear" in body
    assert "focus ceiling left out" not in body


def test_auction_bid_past_its_max_is_left_out_with_its_own_sentence():
    auction = make_listing(
        id="gone", url="http://example.com/gone", price=90.0, listing_type="auction",
        max_rational_bid=45.0,
        market_value=80.0, pct_under_market=-12.5, dollar_savings=-10.0, is_opportunity=False,
    )
    body = body_of([make_cheap(id="cheap", url="http://example.com/cheap"), auction], focus_rules=FOCUS)
    assert "http://example.com/gone" not in body
    assert "the current bid is already above the most you could pay" in flat(body)


def test_email_is_capped_and_says_what_it_trimmed():
    deals = [make_cheap(id=str(i), url="http://example.com/{}".format(i)) for i in range(20)]
    body = body_of(deals, focus_rules=FOCUS)
    shown = sum(1 for i in range(20) if "http://example.com/{}  ".format(i) in body)
    assert shown == FOCUS.max_listings
    assert "14 listings matched your focus but were trimmed" in flat(body)


def test_a_flood_of_opportunities_cannot_starve_the_auctions():
    """The reason trim runs two passes. You bid on cheap auctions; a good
    day for Buy It Nows must not be the reason you see none."""
    deals = [
        make_cheap(id="opp-{}".format(i), url="http://example.com/opp-{}".format(i))
        for i in range(10)
    ]
    deals.append(
        make_cheap(
            id="auction", url="http://example.com/auction", listing_type="auction",
            price=12.0, max_rational_bid=35.0, bid_count=3, time_left_text="4h 10m",
            is_opportunity=False,
        )
    )
    body = body_of(deals, focus_rules=FOCUS)
    assert "http://example.com/auction" in body


def test_focus_omissions_are_not_counted_as_reviewed_and_not_close():
    """The footer's four buckets -- printed, out of focus, trimmed, not
    close -- have to add up. A card left out for being expensive must not
    also be described as having been "simply not close"."""
    dear = make_listing(id="dear", url="http://example.com/dear", price=400.0,
                        market_value=600.0, pct_under_market=33.3, dollar_savings=200.0)
    body = flat(body_of([make_cheap(id="cheap", url="http://example.com/cheap"), dear], focus_rules=FOCUS))
    assert "other listing was reviewed and fitted no section" not in body
    assert "other listings were reviewed and fitted no section" not in body


def test_target_hit_above_the_ceiling_survives_focus():
    deal = make_listing(id="target", url="http://example.com/target", price=900.0, market_value=950.0,
                        pct_under_market=5.3, dollar_savings=50.0,
                        target_hit=make_target_hit(), is_opportunity=False)
    body = body_of([deal], focus_rules=FOCUS)
    assert "http://example.com/target" in body


def test_needs_review_cannot_take_over_a_quiet_day():
    """The real shape of the long email: on a corpus day where nothing could
    be valued, every one of 289 listings was a NEEDS REVIEW block. That
    section is capped at its share and never takes the leftovers -- a page
    of "we could not value this" is not what the cap is for."""
    deals = [
        make_cheap(
            id=str(i), url="http://example.com/{}".format(i),
            comp_match=make_match(level="price_tier", flag_eligible=False),
            is_opportunity=False,
        )
        for i in range(30)
    ]
    body = body_of(deals, focus_rules=FOCUS)
    shown = sum(1 for i in range(30) if "http://example.com/{}  ".format(i) in body)
    assert shown == FOCUS.max_per_section
    assert "LOW CONFIDENCE / NEEDS REVIEW" in body


def test_needs_review_is_capped_but_opportunities_are_not():
    """Same budget, different section: TOP OPPORTUNITIES does take the
    leftovers, because a day with 20 real opportunities is a day worth
    reading 6 of."""
    deals = [make_cheap(id=str(i), url="http://example.com/{}".format(i)) for i in range(30)]
    body = body_of(deals, focus_rules=FOCUS)
    shown = sum(1 for i in range(30) if "http://example.com/{}  ".format(i) in body)
    assert shown == FOCUS.max_listings


def test_empty_state_does_not_claim_completeness_under_focus():
    """"Everything CardPro did see is still below" above a capped list is
    the report claiming a completeness it deliberately gave up."""
    deals = [
        make_cheap(
            id=str(i), url="http://example.com/{}".format(i),
            comp_match=make_match(level="price_tier", flag_eligible=False),
            is_opportunity=False,
        )
        for i in range(30)
    ]
    focused = flat(body_of(deals, focus_rules=FOCUS))
    assert "Everything CardPro did see" not in focused
    assert "The best of what CardPro did see" in focused
    assert "Everything CardPro did see" in flat(body_of(deals))


# ---------------------------------------------------------------------------
# "Sold comps worth adding" -- where a few minutes of your time buys the most
# ---------------------------------------------------------------------------


def a_request(**overrides):
    from src.comp_requests import CompRequest

    defaults = dict(
        player="Caleb Williams",
        year=2024,
        set_name="Prizm",
        parallel="Silver Prizm",
        market=("graded", "PSA", "10", None),
        listings_waiting=6,
        sold_on_file=1,
        still_needed=2,
        example_url="https://www.ebay.com/itm/123",
    )
    defaults.update(overrides)
    return CompRequest(**defaults)


class TestSoldCompRequestsSection:
    def test_nothing_to_ask_for_prints_nothing(self):
        assert report._sold_comp_requests_section([], 0) == ""

    def test_names_the_card_the_market_and_the_cost_of_the_ask(self):
        text = flat(report._sold_comp_requests_section([a_request()], 0))
        assert "Caleb Williams (2024 Prizm Silver Prizm PSA 10)" in text
        assert "6 listings waiting" in text
        assert "2 sales still needed" in text

    def test_gives_a_query_that_can_be_pasted_straight_in(self):
        text = report._sold_comp_requests_section([a_request()], 0)
        assert 'search: "2024 Prizm Caleb Williams Silver Prizm PSA 10"' in text

    def test_points_at_the_tool_that_records_the_answer(self):
        # A suggestion with no way to act on it is a complaint.
        text = report._sold_comp_requests_section([a_request()], 0)
        assert "130point.com" in text
        assert "scripts.add_sold_comp" in text

    def test_singular_ask_is_not_written_as_plural(self):
        text = report._sold_comp_requests_section(
            [a_request(listings_waiting=1, still_needed=1)], 0
        )
        assert "1 listing waiting" in text
        assert "1 sale still needed" in text

    def test_an_example_listing_is_optional(self):
        assert "example:" not in report._sold_comp_requests_section(
            [a_request(example_url=None)], 0
        )

    def test_unidentified_listings_are_reported_as_a_different_problem(self):
        # The whole point of printing this number: it says data entry is NOT
        # the bottleneck, parsing is. Without it the section reads as though
        # typing in sold prices would fix everything.
        text = report._sold_comp_requests_section([], 412)
        assert "412 listing(s) could not be identified" in text
        assert "not more data" in text

    def test_the_section_appears_in_the_assembled_report(self):
        body = report.build_report(
            [], 30, RUN_DATE, comp_requests_list=[a_request()], unidentified_listings=9
        )[1]
        assert "Sold comps worth adding" in body
        assert "Caleb Williams (2024 Prizm Silver Prizm PSA 10)" in body

    def test_a_caller_that_does_not_compute_them_gets_no_section(self):
        assert "Sold comps worth adding" not in report.build_report([], 30, RUN_DATE)[1]


# ---------------------------------------------------------------------------
# A number that cannot be stood behind is not printed
#
# The pipeline fills in market_value, dollar_savings and economics BEFORE it
# checks flag-eligibility, and a target hit reaches a headline section
# regardless of claims. So a context-only comp arrived at the renderer fully
# furnished with a discount and an ROI -- an $18 card inside a bucket defined
# as "$25-$100", median $44, reported as 51% off with a 39% return. That is
# the audit's failure mode #1 reprinted, and the fix is not a softer
# adjective on the number.
# ---------------------------------------------------------------------------


def context_only_deal(**overrides):
    deal = make_listing(**overrides)
    deal.comp_match = CompMatch(
        stats=make_stats(median=44.0, sample_size=12, minimum=25.0, maximum=99.0),
        level="price_tier",
        flag_eligible=False,
        confidence="low",
        blocked_reasons=("context_only_level",),
    )
    deal.market_value = 44.0
    deal.dollar_savings = 22.5
    deal.pct_under_market = 51.1
    return deal


class TestContextOnlyCompsPrintNoNumber:
    def test_no_discount_line(self):
        assert report._discount_text(context_only_deal()) is None

    def test_no_economics_line(self):
        deal = context_only_deal()
        deal.economics = object()  # would have rendered if it were consulted
        assert report._economics_text(deal) is None

    def test_the_absence_is_explained_rather_than_silent(self):
        body = flat(body_of([context_only_deal()]))
        assert "CardPro has no valuation for this card" in body
        assert "51.1%" not in body

    def test_a_flag_eligible_comp_still_gets_its_numbers(self):
        deal = make_listing()
        assert report._discount_text(deal) is not None


class TestConfidenceNamesEveryDowngrade:
    def test_a_time_concentrated_sample_is_named(self):
        # The most common downgrade in production by far -- 866 of 907
        # listings -- and the only one with no sentence. An exact
        # grade-matched comp at n=9 printed "LOW" with nothing accounting
        # for the fall, which reads as an arbitrary ladder.
        deal = make_listing()
        deal.comp_match = CompMatch(
            stats=make_stats(sample_size=9, distinct_dates=1, span_days=0, is_concentrated=True),
            level="exact",
            flag_eligible=False,
            confidence="low",
            blocked_reasons=("concentrated_sample",),
        )
        text = flat(report._confidence_text(deal))
        assert "one snapshot, not independent readings" in text
        assert "1 date(s) spanning 0 day(s)" in text

    def test_a_well_spread_sample_says_nothing_about_dates(self):
        assert "snapshot" not in report._confidence_text(make_listing())


class TestUnknownShippingIsCarriedThrough:
    def test_the_discount_says_it_is_a_ceiling(self):
        # total_cost falls back to price alone when shipping is unknown, so
        # the Cost line declines to state a total and the Discount line then
        # named one to the cent two lines later.
        deal = make_listing(shipping_price=None)
        assert "before shipping, which is unknown" in report._discount_text(deal)

    def test_a_known_shipping_discount_carries_no_caveat(self):
        assert "unknown" not in report._discount_text(make_listing(shipping_price=4.99))


class TestPriceDropsCarryConfidence:
    def test_the_block_says_how_far_to_trust_its_market_value(self):
        # Every other block type carries this; its absence here was a
        # copy-paste omission that no test caught.
        deal = make_listing(is_price_drop=True, previous_price=90.0)
        assert "Confidence" in report._price_drop_block(1, deal)


class TestAWarnedRunIsNotAQuietDay:
    def _warned_stats(self):
        from src import observability

        stats = observability.RunStats(alert_emails_scanned=14)
        stats.warn(
            "Read 14 eBay alert email(s) and extracted 0 listings from all of them.",
            broken=True,
        )
        return stats

    def test_the_subject_says_to_check(self):
        subject = report.build_report([], 30, RUN_DATE, stats=self._warned_stats())[0]
        assert "CHECK THIS" in subject

    def test_the_email_does_not_call_the_silence_normal(self):
        # It used to open with "That is a normal outcome, not a failure" six
        # lines above a footer saying 14 emails yielded nothing -- asserting
        # the zeroes were fine while the alarm said they were manufactured.
        body = flat(body_of([], stats=self._warned_stats()))
        assert "SOMETHING IS WRONG WITH TODAY'S SCAN." in body
        assert "That is a normal outcome, not a failure" not in body
        assert "Do not read the zeroes as a quiet market" in body

    def test_a_notable_but_normal_warning_does_not_cry_wolf(self):
        # "No comp bucket is strong enough to flag today" is TRUE ON MOST
        # DAYS right now. An alarm that fires every morning is not an alarm,
        # so only warn(broken=True) reaches the subject and the opening line.
        from src import observability

        stats = observability.RunStats()
        stats.warn("No comp bucket anywhere is strong enough to declare a deal from.")
        body = flat(body_of([], stats=stats))
        assert "SOMETHING IS WRONG WITH TODAY'S SCAN." not in body
        assert "That is a normal outcome, not a failure" in body
        assert "CHECK THIS" not in report.build_report([], 30, RUN_DATE, stats=stats)[0]
        assert "No comp bucket anywhere is strong enough" in body  # still in the footer

    def test_an_unwarned_quiet_day_still_reads_as_normal(self):
        from src import observability

        body = flat(body_of([], stats=observability.RunStats()))
        assert "NOTHING CLEARED THE BAR TODAY." in body
        assert "That is a normal outcome, not a failure" in body
        assert "No opportunities today" in report.build_report([], 30, RUN_DATE)[0]


class TestFooterWrapping:
    def test_no_footer_line_runs_past_the_wrap_width(self):
        # The "Top reasons" line came out at 404 characters, which a mail
        # client soft-wraps wherever it likes -- so the one part of the
        # footer worth reading became the part nobody could.
        from src import observability, reasons

        stats = observability.RunStats(listings_matched_to_watchlist=200, valued=60)
        for index in range(40):
            stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL, str(index))
        for line in body_of([make_listing()], stats=stats).splitlines():
            assert len(line) <= report._WRAP_WIDTH + 8, line


class TestEveryBlockKindRefusesToPriceAnAuction:
    """An auction's number is a CURRENT BID. Rendering it under "Asking" or
    "Cost" is how a $40 opening bid became a reported 60%-off deal.
    _thesis_block and _compact_block have always guarded against this;
    _offer_block and _price_drop_block did not, and were safe only because
    classify_sections happens to claim auctions before it reaches their
    loops. That is an ordering accident, not a guarantee."""

    def _auction(self):
        return make_listing(listing_type="auction", bid_count=4, time_left_text="2d 04h")

    def test_the_offer_block_routes_an_auction_to_the_auction_block(self):
        text = report._offer_block(1, self._auction(), 30.0)
        assert "CURRENT BID, not an asking price" in flat(text)
        assert "Asking" not in text

    def test_the_price_drop_block_routes_an_auction_to_the_auction_block(self):
        text = report._price_drop_block(1, self._auction())
        assert "CURRENT BID, not an asking price" in flat(text)

    def test_a_fixed_price_listing_is_unaffected(self):
        assert "CURRENT BID" not in report._offer_block(1, make_listing(), 30.0)


class TestRisksAreOrderedWorstFirst:
    """They used to be appended as they were computed, so on a card with six
    risks "shipping unknown" and "this may be a reprint" landed in the same
    run-on sentence with the same weight and the reader had to rank them.
    They are not the same kind of statement."""

    def _kitchen_sink(self):
        deal = make_listing(
            shipping_price=None,
            title_truncated=True,
            listing_type="unknown",
            negative_signals=("reprint",),
            matched_players=("Caleb Williams", "Rome Odunze"),
        )
        deal.comp_match = CompMatch(
            stats=make_stats(sample_size=2, is_stale=True, age_days_newest=90),
            level="same_card",
            flag_eligible=False,
            confidence="low",
            blocked_reasons=("thin_sample", "stale_comps"),
        )
        return deal

    def test_it_may_not_be_the_card_outranks_everything(self):
        risks = report._risks(self._kitchen_sink())
        assert "reprint" in risks[0].lower()

    def test_the_price_may_not_be_the_price_outranks_comp_quality(self):
        risks = report._risks(self._kitchen_sink())
        shipping = next(i for i, r in enumerate(risks) if "shipping unknown" in r)
        thin = next(i for i, r in enumerate(risks) if "thin sample" in r)
        assert shipping < thin

    def test_a_card_with_only_comp_caveats_still_lists_them(self):
        deal = make_listing(shipping_price=4.99)
        deal.comp_match = CompMatch(
            stats=make_stats(sample_size=2),
            level="same_card", flag_eligible=False, confidence="low",
            blocked_reasons=("thin_sample",),
        )
        assert any("thin sample" in r for r in report._risks(deal))

    def test_a_clean_listing_has_no_risks_line(self):
        # "Risks: none" trains you to stop reading the ones that matter.
        assert report._risks(make_listing(shipping_price=4.99)) == []


class TestACardIsNotPrintedTwiceInFull:
    """Sections answer different questions, so a card legitimately appears in
    more than one -- a target hit whose only comp is context-only belongs in
    both TARGET CARD HITS and NEEDS REVIEW. What it does not need is the same
    nine lines twice, eight of them byte-identical, in an email whose length
    is a known problem."""

    def _target_with_a_context_only_comp(self):
        deal = context_only_deal()
        deal.target_hit = SimpleNamespace(
            label="BUY ZONE", in_buy_zone=True, threshold=25.0, price_known=True,
            target=SimpleNamespace(label="Payton 1984 Topps"),
        )
        return deal

    def test_the_second_appearance_points_at_the_first(self):
        body = body_of([self._target_with_a_context_only_comp()])
        later = body[body.index("WATCH"):]
        assert "shown above under TARGET CARD HITS" in later
        assert "Cost" not in later

    def test_the_line_that_is_actually_new_is_still_printed(self):
        # The whole reason the card is in this section.
        body = body_of([self._target_with_a_context_only_comp()])
        later = body[body.index("WATCH"):]
        assert "Why here" in later

    def test_the_card_still_appears_in_both_sections(self):
        # "Nothing disappears silently" -- the fix is to stop repeating the
        # block, never to stop listing the card.
        body = body_of([self._target_with_a_context_only_comp()])
        assert body.count("Michael Jordan --") >= 2

    def test_a_card_appearing_only_once_gets_its_full_block(self):
        body = body_of([context_only_deal()])
        assert "shown above" not in body
        assert "Cost" in body[body.index("WATCH"):]


# ---------------------------------------------------------------------------
# COOL CARDS and CHEAP FINDS
#
# The report used to answer one question -- "is this underpriced against a
# grade-matched comp" -- and on this project's data it can almost never
# answer it. So the body of the email was a list of things it could not
# value, under headings that read as junk drawers. These two sections answer
# questions it CAN answer from a title: what is this card, and is it cheap.
# Neither makes any claim about price, which is why neither can produce the
# false confidence the valuation rules exist to prevent.
# ---------------------------------------------------------------------------


def carded(title, price, **overrides):
    identity = card_identity.extract_card_identity(title)
    fields = dict(
        # id from a hash of the title, not a prefix of it: two 2024 Panini
        # cards share their first twelve characters, and a shared id makes
        # the second one look like a duplicate of the first.
        id=str(abs(hash(title))), title=title, price=price, card_identity=identity,
        card_type="raw", grader=None, grade=None, is_opportunity=False,
        comp_match=None, market_value=None, pct_under_market=None, dollar_savings=None,
        is_rookie_card="RC" in title or "Rookie" in title,
    )
    fields.update(overrides)
    return make_listing(**fields)


AUTO = "2024 Topps Chrome Pete Crow-Armstrong Auto Refractor /499 #RA-PCA"
CHEAP_ROOKIE = "2024 Panini Donruss Caleb Williams #301 RC"
BASE_COMMON = "2024 Topps Caleb Williams #150"


class TestCoolCards:
    def test_a_signed_numbered_card_gets_a_section_of_its_own(self):
        sections = report.classify_sections([carded(AUTO, 42.0)])
        assert [d.title for d in sections[report.SECTION_COOL_CARDS]] == [AUTO]

    def test_a_base_common_is_not_a_cool_card(self):
        sections = report.classify_sections([carded(BASE_COMMON, 42.0, is_rookie_card=False)])
        assert sections[report.SECTION_COOL_CARDS] == []

    def test_the_scarcest_card_comes_first(self):
        plain = "2024 Panini Prizm Caleb Williams Silver Prizm RC #301"
        sections = report.classify_sections([carded(plain, 9.0), carded(AUTO, 42.0)])
        assert sections[report.SECTION_COOL_CARDS][0].title == AUTO

    def test_the_block_prints_no_market_value_and_no_discount(self):
        # The whole point: this section is about what the card IS. A
        # discount here would be a valuation claim wearing a browse label.
        deal = carded(AUTO, 42.0)
        deal.comp_match = make_match(flag_eligible=False, blocked=("context_only_level",))
        deal.market_value = 200.0
        deal.dollar_savings = 150.0
        deal.pct_under_market = 75.0
        block = report._interest_block(1, deal)
        assert "Market" not in block
        assert "Discount" not in block
        assert "What it is" in block

    def test_an_auction_still_gets_the_auction_block(self):
        deal = carded(AUTO, 42.0, listing_type="auction", bid_count=3)
        assert "CURRENT BID, not an asking price" in flat(report._interest_block(1, deal))


class TestCheapFinds:
    def test_pocket_change_with_something_to_it_is_shown(self):
        sections = report.classify_sections([carded(CHEAP_ROOKIE, 1.99)], cheap_find_ceiling=15.0)
        assert [d.title for d in sections[report.SECTION_CHEAP_FINDS]] == [CHEAP_ROOKIE]

    def test_a_cheap_card_with_nothing_to_it_is_not(self):
        # Being cheap is not a reason on its own -- the cheap end of the
        # hobby is mostly base commons and printing them all is the pile
        # this section exists to filter.
        sections = report.classify_sections(
            [carded(BASE_COMMON, 1.99, is_rookie_card=False)], cheap_find_ceiling=15.0
        )
        assert sections[report.SECTION_CHEAP_FINDS] == []

    def test_the_ceiling_is_on_total_cost_not_the_asking_price(self):
        deal = carded(CHEAP_ROOKIE, 14.0, shipping_price=5.0)
        sections = report.classify_sections([deal], cheap_find_ceiling=15.0)
        assert sections[report.SECTION_CHEAP_FINDS] == []

    def test_cheapest_first(self):
        dearer = carded("2024 Panini Select Matas Buzelis Concourse RC #45", 9.0)
        sections = report.classify_sections(
            [dearer, carded(CHEAP_ROOKIE, 1.99)], cheap_find_ceiling=15.0
        )
        assert sections[report.SECTION_CHEAP_FINDS][0].title == CHEAP_ROOKIE

    def test_a_cool_card_is_not_also_a_cheap_find(self):
        # One card, one section. COOL CARDS is claimed first.
        cheap_auto = carded("2024 Topps Chrome Caleb Williams Auto RC", 5.0)
        sections = report.classify_sections([cheap_auto], cheap_find_ceiling=15.0)
        assert len(sections[report.SECTION_COOL_CARDS]) == 1
        assert sections[report.SECTION_CHEAP_FINDS] == []


class TestTheEmailSaysWhatItHas:
    def _browsable_day(self):
        return [carded(AUTO, 42.0), carded(CHEAP_ROOKIE, 1.99)]

    def test_the_subject_names_the_cards_not_the_absence_of_deals(self):
        # It is the line most likely to be the only thing read, and "No
        # opportunities today" above a page of autographs is the subject
        # arguing with its own contents.
        subject = report.build_report(self._browsable_day(), 30, RUN_DATE)[0]
        assert "No opportunities" not in subject
        assert "cool card" in subject and "cheap find" in subject

    def test_the_counts_line_names_them_too(self):
        body = body_of(self._browsable_day())
        assert "1 cool card | 1 cheap find" in body

    def test_it_still_says_there_was_no_deal(self):
        # Dropping the statement would let browsable cards read as
        # opportunities, which is the confusion the engine exists to prevent.
        body = flat(body_of(self._browsable_day()))
        assert "No deal today" in body
        assert "NOTHING CLEARED THE BAR TODAY." not in body

    def test_a_genuinely_empty_day_still_gets_the_full_explanation(self):
        body = flat(body_of([]))
        assert "NOTHING CLEARED THE BAR TODAY." in body

    def test_a_real_deal_still_leads(self):
        subject = report.build_report([make_listing()], 30, RUN_DATE)[0]
        assert "opportunity" in subject


class TestYoungCore:
    """The old rule was `young_core AND is_opportunity`, and is_opportunity
    requires a flag-eligible comp -- which has never once existed in this
    project's production data. The section for the players the watchlist is
    built around could not fire, ever."""

    def _young(self, title, price, **overrides):
        return carded(title, price, player_tier="young_core", **overrides)

    def test_a_young_core_card_with_something_to_it_now_appears(self):
        # One weak attribute: a parallel and nothing else, so COOL CARDS
        # (which wants a standout) does not take it, and it is over the
        # pocket-change ceiling so CHEAP FINDS does not either.
        deal = self._young(
            "2024 Panini Prizm Caleb Williams Silver Prizm #301", 30.0, is_rookie_card=False
        )
        sections = report.classify_sections([deal], cheap_find_ceiling=15.0)
        assert len(sections[report.SECTION_INVESTMENT]) == 1

    def test_a_young_core_base_common_does_not(self):
        deal = self._young("2024 Topps Caleb Williams #150", 30.0, is_rookie_card=False)
        sections = report.classify_sections([deal], cheap_find_ceiling=15.0)
        assert sections[report.SECTION_INVESTMENT] == []

    def test_a_legend_is_not_young_core(self):
        deal = carded(
            "2024 Panini Prizm Michael Jordan Silver Prizm #301", 30.0,
            player="Michael Jordan", player_tier="legend", is_rookie_card=False,
        )
        sections = report.classify_sections([deal], cheap_find_ceiling=15.0)
        assert sections[report.SECTION_INVESTMENT] == []

    def test_cool_cards_still_claims_a_standout_first(self):
        # A young-core autograph belongs at the top of the email, not in a
        # tier-labelled section further down. The headline carries a
        # [YOUNG CORE] tag either way, so nothing is lost by claiming it.
        deal = self._young(AUTO, 42.0)
        sections = report.classify_sections([deal], cheap_find_ceiling=15.0)
        assert len(sections[report.SECTION_COOL_CARDS]) == 1
        assert sections[report.SECTION_INVESTMENT] == []

    def test_the_block_makes_no_valuation_claim(self):
        deal = self._young(
            "2024 Panini Prizm Caleb Williams Silver Prizm #301", 30.0, is_rookie_card=False
        )
        deal.comp_match = make_match(flag_eligible=False, blocked=("context_only_level",))
        deal.market_value = 200.0
        deal.dollar_savings = 170.0
        deal.pct_under_market = 85.0
        body = body_of([deal])
        assert "YOUNG CORE" in body
        assert "85.0%" not in body


class TestBrowseSectionsDropCompCaveats:
    """These sections show no market value, no discount and no ROI.
    Caveating the quality of a comp that is not on the page is noise -- it
    was four wrapped lines of it on every card, the same four lines seven
    times over, about a number the section deliberately refuses to print."""

    def _with_bad_comps(self):
        deal = carded(AUTO, 42.0, shipping_price=None)
        deal.comp_match = make_match(
            flag_eligible=False,
            blocked=("context_only_level", "concentrated_sample", "thin_sample"),
        )
        deal.market_value = 200.0
        return deal

    def test_comp_quality_caveats_are_left_out(self):
        block = flat(report._interest_block(1, self._with_bad_comps()))
        assert "context-only" not in block
        assert "one snapshot" not in block

    def test_the_ones_about_the_card_and_the_cost_stay(self):
        block = flat(report._interest_block(1, self._with_bad_comps()))
        assert "shipping unknown" in block

    def test_a_negative_signal_is_never_dropped(self):
        # "This may be a reprint" is a fact about the card, not about how
        # well a comp was measured, and it outranks everything.
        deal = carded(AUTO, 42.0, negative_signals=("reprint",))
        assert "REPRINT" in report._interest_block(1, deal)

    def test_a_deal_section_still_gets_the_full_list(self):
        risks = report._risks(self._with_bad_comps())
        assert any("context-only" in r for r in risks)


class TestCraigslistLinksAreCapped:
    """Twenty formulaic links, every morning, forever, was a fifth of the
    email -- and nobody eyeballs twenty Craigslist searches daily."""

    def _links(self, count):
        names = ["Player %d" % i for i in range(count)]
        return {
            name: "https://chicago.craigslist.org/search/sss?query={}+card".format(
                name.replace(" ", "+")
            )
            for name in names
        }

    def test_a_short_watchlist_prints_every_link(self):
        text = report._craigslist_section(self._links(4))
        assert text.count("craigslist.org") == 4
        assert "more on the same pattern" not in text

    def test_a_long_one_caps_and_says_so(self):
        text = report._craigslist_section(self._links(20))
        assert text.count("https://") == report.CRAIGSLIST_MAX_LINKS + 1  # +1 for the template
        assert "and 14 more on the same pattern" in flat(text)

    def test_nobody_is_dropped_silently(self):
        # The names are all still there, and the template builds any of the
        # links -- capping the clickable ones is not hiding the players.
        text = flat(report._craigslist_section(self._links(20)))
        for index in range(20):
            assert "Player {}".format(index) in text

    def test_the_template_is_a_real_substitution(self):
        text = report._craigslist_section(self._links(20))
        assert "query=<player>+card" in text

    def test_no_links_prints_nothing(self):
        assert report._craigslist_section({}) == ""


class TestRejectedListingsNeverReachTheBrowseSections:
    """main.evaluate_listings leaves desirable_attributes empty for a
    listing it rejected -- a break slot, a lot, a custom, a multi-player
    card, a grade read off a truncated title. The browse sections used to
    recount the attributes from the title instead, and the title of a break
    slot still says "auto refractor /99". A group-break slot became the top
    card in the email, described as "rookie card, autograph, serial
    numbered". A break slot is not a card you receive at all."""

    def _rejected(self, title):
        # Exactly what the pipeline hands the report for a rejected listing:
        # a full card_identity, and an empty verdict.
        deal = carded(title, 30.0)
        deal.desirable_attributes = ()
        deal.is_opportunity = False
        return deal

    def test_a_break_slot_is_not_a_cool_card(self):
        deal = self._rejected("Kyle Teel 2024 Bowman Chrome Auto Refractor /99 RC BREAK SLOT 1")
        assert report.classify_sections([deal])[report.SECTION_COOL_CARDS] == []

    def test_a_lot_is_not_a_cool_card(self):
        deal = self._rejected("Caleb Williams 2024 Topps Chrome Auto Refractor /99 RC LOT OF 5")
        assert report.classify_sections([deal])[report.SECTION_COOL_CARDS] == []

    def test_a_custom_is_not_a_cheap_find(self):
        deal = self._rejected("Caleb Williams 2024 Topps Chrome Auto RC CUSTOM ART CARD")
        deal.price = 4.0
        sections = report.classify_sections([deal], cheap_find_ceiling=15.0)
        assert sections[report.SECTION_CHEAP_FINDS] == []

    def test_a_rejected_young_core_card_is_not_promoted_either(self):
        deal = self._rejected("Caleb Williams 2024 Topps Chrome Auto Refractor /99 RC LOT OF 5")
        deal.player_tier = "young_core"
        assert report.classify_sections([deal])[report.SECTION_INVESTMENT] == []

    def test_the_block_never_describes_attributes_the_pipeline_did_not_settle(self):
        deal = self._rejected("Kyle Teel 2024 Bowman Chrome Auto Refractor /99 RC BREAK SLOT 1")
        assert "What it is" not in report._interest_block(1, deal)

    def test_an_accepted_card_is_unaffected(self):
        deal = carded(AUTO, 42.0)
        assert report.classify_sections([deal])[report.SECTION_COOL_CARDS] == [deal]


class TestTheBrowseSectionsDoNotSwallowAValuation:
    """COOL CARDS, CHEAP FINDS and YOUNG CORE print no market value, no
    discount and no confidence -- that is what makes them safe, and it makes
    them the wrong home for a card that HAS a comp worth standing behind.
    They sit above WATCH and PRICE DROPS in the order, so without a guard a
    card 70% under market against a flag-eligible exact comp was claimed as
    a cool card and printed with none of that, under a header reading "No
    deal today: nothing had a comp CardPro will stand behind"."""

    def _standout(self, **overrides):
        deal = carded(AUTO, 60.0, **overrides)
        return deal

    def test_a_flag_eligible_comp_keeps_the_card_out_of_cool_cards(self):
        deal = self._standout(
            comp_match=make_match(level="exact", confidence="low", median=200.0),
            market_value=200.0, pct_under_market=70.0, dollar_savings=140.0,
        )
        sections = report.classify_sections([deal], threshold_pct=30)
        assert sections[report.SECTION_COOL_CARDS] == []
        assert sections[report.SECTION_WATCH] == [deal]

    def test_and_the_numbers_actually_print(self):
        deal = self._standout(
            comp_match=make_match(level="exact", confidence="low", median=200.0),
            market_value=200.0, pct_under_market=70.0, dollar_savings=140.0,
        )
        body = flat(body_of([deal]))
        assert "70.0%" in body
        assert "No deal today" not in body

    def test_a_price_drop_is_not_swallowed_either(self):
        deal = self._standout(
            comp_match=None, market_value=None, pct_under_market=None, dollar_savings=None,
            is_price_drop=True, previous_price=80.0,
        )
        deal.price = 45.0
        sections = report.classify_sections([deal])
        assert sections[report.SECTION_COOL_CARDS] == []
        assert sections[report.SECTION_PRICE_DROPS] == [deal]
        assert "was $80.00 -> now $45.00" in flat(body_of([deal]))

    def test_a_context_only_comp_does_not_count_as_a_valuation(self):
        # The whole point of the browse sections: these cards have no number
        # anyone can stand behind, and that is the normal case.
        deal = self._standout(
            comp_match=make_match(flag_eligible=False, blocked=("context_only_level",)),
            market_value=200.0,
        )
        assert report.classify_sections([deal])[report.SECTION_COOL_CARDS] == [deal]
