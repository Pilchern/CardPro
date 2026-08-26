"""Tests for the email's editorial rules.

Two separable jobs, tested separately:

* **What is email material** (``select``) -- the price ceiling, the
  exception for an exceptional dearer card, bidding room on auctions. The
  interesting cases are all the ones where a rule must NOT fire: a target
  hit above the ceiling, an auction with no computable ceiling, a big
  discount off a comp the engine won't stand behind.
* **How long the email is** (``trim``) -- the two-pass budget. The point of
  the first pass is that a crowded section cannot starve the sections below
  it, so that is what most of these assert.

Throughout: focus may only ever REMOVE, and what it removes must be
countable. A test that focus dropped something is only half a test -- the
other half is that the count came back.
"""
from collections import OrderedDict

from src import focus
from src.models import Listing

RULES = focus.FocusRules(
    price_ceiling=40.0,
    exceptional_min_discount_pct=50.0,
    exceptional_min_savings_dollars=100.0,
    max_listings=10,
    max_per_section=3,
)


class FakeMatch:
    def __init__(self, flag_eligible=True):
        self.flag_eligible = flag_eligible


def make_listing(**overrides) -> Listing:
    """A cheap fixed-price listing, comfortably inside focus."""
    defaults = dict(
        id="1",
        source="ebay",
        title="Kyle Teel 2024 Bowman Chrome #BCP-1",
        price=20.0,
        url="http://example.com/1",
        player="Kyle Teel",
        card_type="raw",
        listing_type="fixed_price",
        comp_match=FakeMatch(),
        market_value=60.0,
        pct_under_market=66.7,
        dollar_savings=40.0,
    )
    defaults.update(overrides)
    return Listing(**defaults)


def ids(listings) -> list:
    return [listing.id for listing in listings]


# ---------------------------------------------------------------------------
# the price ceiling
# ---------------------------------------------------------------------------


def test_cheap_listing_is_email_material():
    assert focus.omission_reason(make_listing(price=20.0), RULES) is None


def test_listing_exactly_at_the_ceiling_is_kept():
    """The ceiling is an "at or under" line. A $40 ceiling that rejects a
    $40 card is a $39.99 ceiling with a misleading name."""
    assert focus.omission_reason(make_listing(price=40.0), RULES) is None


def test_dear_listing_is_left_out_with_a_reason():
    listing = make_listing(price=300.0, pct_under_market=20.0, dollar_savings=75.0)
    assert focus.omission_reason(listing, RULES) == focus.ABOVE_CEILING


def test_shipping_counts_towards_the_ceiling():
    """$38 plus $6 postage is a $44 card. The ceiling is what leaves your
    account, not what the listing advertises."""
    listing = make_listing(price=38.0, shipping_price=6.0, pct_under_market=20.0, dollar_savings=10.0)
    assert focus.omission_reason(listing, RULES) == focus.ABOVE_CEILING


def test_exceptional_dear_listing_gets_in_anyway():
    listing = make_listing(price=300.0, pct_under_market=60.0, dollar_savings=450.0)
    assert focus.omission_reason(listing, RULES) is None
    assert focus.is_exceptional(listing, RULES)


def test_exceptional_needs_the_dollars_as_well_as_the_percent():
    listing = make_listing(price=60.0, pct_under_market=60.0, dollar_savings=90.0)
    assert not focus.is_exceptional(listing, RULES)
    assert focus.omission_reason(listing, RULES) == focus.ABOVE_CEILING


def test_exceptional_needs_the_percent_as_well_as_the_dollars():
    listing = make_listing(price=900.0, pct_under_market=25.0, dollar_savings=300.0)
    assert not focus.is_exceptional(listing, RULES)


def test_exceptional_needs_a_comp_cardpro_will_stand_behind():
    """A huge discount off a context-only comp is exactly the false
    positive the whole 2.0 pass exists to stop. It must not become the way
    expensive cards get back into a report built around cheap ones."""
    listing = make_listing(
        price=900.0,
        pct_under_market=70.0,
        dollar_savings=2000.0,
        comp_match=FakeMatch(flag_eligible=False),
    )
    assert not focus.is_exceptional(listing, RULES)
    assert focus.omission_reason(listing, RULES) == focus.ABOVE_CEILING


def test_no_comp_at_all_cannot_be_exceptional():
    listing = make_listing(price=900.0, comp_match=None, pct_under_market=None, dollar_savings=None)
    assert focus.omission_reason(listing, RULES) == focus.ABOVE_CEILING


def test_target_hit_ignores_the_ceiling():
    """You named this card and set its price yourself. Overruling that with
    a second price opinion would be the config arguing with itself."""
    listing = make_listing(price=800.0, pct_under_market=5.0, dollar_savings=40.0, target_hit=object())
    assert focus.omission_reason(listing, RULES) is None


def test_unreadable_price_is_left_out_and_counted():
    listing = make_listing(price=None, pct_under_market=None, dollar_savings=None)
    assert focus.omission_reason(listing, RULES) == focus.PRICE_UNKNOWN


# ---------------------------------------------------------------------------
# bidding room
# ---------------------------------------------------------------------------


def make_auction(**overrides) -> Listing:
    defaults = dict(listing_type="auction", price=12.0, bid_count=4, max_rational_bid=30.0)
    defaults.update(overrides)
    return make_listing(**defaults)


def test_auction_still_under_the_max_bid_is_kept():
    assert focus.omission_reason(make_auction(price=12.0, max_rational_bid=30.0), RULES) is None


def test_auction_already_bid_past_the_max_is_left_out():
    listing = make_auction(price=35.0, max_rational_bid=30.0)
    assert focus.omission_reason(listing, RULES) == focus.NO_BIDDING_ROOM


def test_auction_at_exactly_the_max_bid_is_kept():
    """You can still make that bid. One cent over is the line, not the
    number itself."""
    assert focus.omission_reason(make_auction(price=30.0, max_rational_bid=30.0), RULES) is None


def test_shipping_is_not_charged_against_the_max_bid_twice():
    """economics.max_rational_bid has already subtracted inbound shipping to
    arrive at its ceiling. Comparing a shipping-inclusive total against it
    charges shipping twice and drops auctions you could still win -- this
    listing has $2 of bidding room, not none."""
    listing = make_auction(price=28.0, shipping_price=5.0, max_rational_bid=30.0)
    assert focus.omission_reason(listing, RULES) is None


def test_a_bid_over_the_max_is_still_left_out_when_shipping_is_known():
    listing = make_auction(price=31.0, shipping_price=5.0, max_rational_bid=30.0)
    assert focus.omission_reason(listing, RULES) == focus.NO_BIDDING_ROOM


def test_auction_without_a_max_bid_is_kept():
    """No market value means no rational ceiling, so there is nothing to be
    past. Dropping it would make "we could not judge this" look identical
    to "we judged it and it failed"."""
    assert focus.omission_reason(make_auction(max_rational_bid=None), RULES) is None


def test_bidding_room_rule_can_be_turned_off():
    rules = focus.FocusRules(require_auction_bidding_room=False)
    assert focus.omission_reason(make_auction(price=35.0, max_rational_bid=30.0), rules) is None


def test_fixed_price_listing_is_never_judged_on_bidding_room():
    listing = make_listing(price=35.0, max_rational_bid=1.0, listing_type="fixed_price")
    assert focus.omission_reason(listing, RULES) is None


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def test_select_splits_and_counts():
    deals = [
        make_listing(id="cheap", price=20.0),
        make_listing(id="dear", price=500.0, pct_under_market=10.0, dollar_savings=50.0),
        make_auction(id="bid-past", price=35.0, max_rational_bid=30.0),
        make_listing(id="no-price", price=None, pct_under_market=None, dollar_savings=None),
    ]
    selection = focus.select(deals, RULES)
    assert ids(selection.kept) == ["cheap"]
    assert selection.omitted[focus.ABOVE_CEILING] == 1
    assert selection.omitted[focus.NO_BIDDING_ROOM] == 1
    assert selection.omitted[focus.PRICE_UNKNOWN] == 1
    assert selection.omitted_total == 3


def test_select_counts_a_repeated_listing_once():
    """The same eBay item can arrive twice in one run (two saved searches,
    one card). "2 listings left out" when one card was left out would make
    the footer's arithmetic wrong."""
    dear = dict(price=500.0, pct_under_market=10.0, dollar_savings=50.0)
    selection = focus.select([make_listing(id="x", **dear), make_listing(id="x", **dear)], RULES)
    assert selection.omitted_total == 1


def test_select_preserves_order_and_leaves_the_input_alone():
    deals = [make_listing(id="a"), make_listing(id="b"), make_listing(id="c")]
    selection = focus.select(deals, RULES)
    assert ids(selection.kept) == ["a", "b", "c"]
    assert len(deals) == 3


def test_disabled_focus_keeps_everything():
    deals = [
        make_listing(id="dear", price=5000.0, pct_under_market=1.0, dollar_savings=1.0),
        make_listing(id="no-price", price=None, pct_under_market=None, dollar_savings=None),
        make_auction(id="bid-past", price=99.0, max_rational_bid=1.0),
    ]
    selection = focus.select(deals, focus.OFF)
    assert ids(selection.kept) == ["dear", "no-price", "bid-past"]
    assert selection.omitted_total == 0


# ---------------------------------------------------------------------------
# trim
# ---------------------------------------------------------------------------


def sections_of(**counts) -> OrderedDict:
    """{"top": 5} -> five listings with ids top-0 .. top-4."""
    return OrderedDict(
        (key, [make_listing(id="{}-{}".format(key, i)) for i in range(count)])
        for key, count in counts.items()
    )


ORDER = ("top", "auctions", "watch")


def test_trim_gives_every_section_its_share_before_anyone_gets_seconds():
    """The whole reason for the first pass. Straight top-down allocation
    means a 10-opportunity morning prints zero auctions, and "the day was
    so good you saw none of the thing you bid on" is a bug wearing a
    ranking's clothes."""
    sections = sections_of(top=10, auctions=10, watch=10)
    kept, trimmed = focus.trim(sections, ORDER, RULES)
    assert len(kept["top"]) == 4  # 3 in the first pass, 1 from the leftovers
    assert len(kept["auctions"]) == 3
    assert len(kept["watch"]) == 3
    assert trimmed == 20


def test_trim_hands_leftovers_out_in_priority_order():
    sections = sections_of(top=10, auctions=1, watch=1)
    kept, trimmed = focus.trim(sections, ORDER, RULES)
    assert len(kept["top"]) == 8
    assert len(kept["auctions"]) == 1
    assert len(kept["watch"]) == 1
    assert trimmed == 2


def test_trim_never_exceeds_the_budget():
    sections = sections_of(top=50, auctions=50, watch=50)
    kept, _ = focus.trim(sections, ORDER, RULES)
    assert len({deal.id for deals in kept.values() for deal in deals}) == RULES.max_listings


def test_trim_cuts_from_the_bottom_of_a_section():
    """Each section already ranked itself. Trimming must take the tail, so
    the best card in a section is never the one that disappears."""
    sections = sections_of(top=10)
    kept, _ = focus.trim(sections, ORDER, RULES)
    assert ids(kept["top"]) == ["top-{}".format(i) for i in range(len(kept["top"]))]


def test_trim_keeps_render_order_not_budget_order():
    sections = OrderedDict((key, []) for key in ("watch", "top", "auctions"))
    kept, _ = focus.trim(sections, ORDER, RULES)
    assert list(kept) == ["watch", "top", "auctions"]


def test_a_duplicated_listing_costs_the_budget_once():
    """A target hit that is also an opportunity is printed twice on
    purpose. It is still one card, so it spends one slot -- but it does
    occupy a line in both sections, which is why it counts against each
    section's own share."""
    shared = make_listing(id="shared")
    sections = OrderedDict([("top", [shared]), ("auctions", [shared])])
    kept, trimmed = focus.trim(sections, ("top", "auctions"), focus.FocusRules(max_listings=1, max_per_section=1))
    assert ids(kept["top"]) == ["shared"]
    assert ids(kept["auctions"]) == ["shared"]
    assert trimmed == 0


def test_sections_missing_from_the_budget_order_still_get_filled():
    sections = sections_of(top=1, mystery=1)
    kept, trimmed = focus.trim(sections, ORDER, RULES)
    assert len(kept["mystery"]) == 1
    assert trimmed == 0


def test_trim_with_focus_off_changes_nothing():
    sections = sections_of(top=50, auctions=50)
    kept, trimmed = focus.trim(sections, ORDER, focus.OFF)
    assert [len(deals) for deals in kept.values()] == [50, 50]
    assert trimmed == 0


def test_trim_leaves_the_input_sections_alone():
    sections = sections_of(top=10, auctions=10, watch=10)
    focus.trim(sections, ORDER, RULES)
    assert [len(deals) for deals in sections.values()] == [10, 10, 10]


def test_max_per_section_of_zero_means_no_per_section_share():
    rules = focus.FocusRules(max_listings=5, max_per_section=0)
    kept, trimmed = focus.trim(sections_of(top=10, auctions=10), ORDER, rules)
    assert len(kept["top"]) == 5
    assert len(kept["auctions"]) == 0
    assert trimmed == 15


def test_a_no_leftovers_section_keeps_only_its_share():
    """A section that says of itself it is not a recommendation must not be
    able to become the whole email just by being the biggest thing that
    happened that morning."""
    sections = sections_of(top=1, review=50)
    kept, trimmed = focus.trim(sections, ("top", "review"), RULES, no_leftovers=("review",))
    assert len(kept["top"]) == 1
    assert len(kept["review"]) == RULES.max_per_section
    assert trimmed == 47


def test_no_leftovers_still_yields_to_a_busy_day():
    """The share is a ceiling, not a reservation -- if the sections above it
    spend the budget first, the capped section gets what is left, which may
    be nothing."""
    sections = sections_of(top=10, review=10)
    kept, _ = focus.trim(
        sections, ("top", "review"), focus.FocusRules(max_listings=3, max_per_section=3),
        no_leftovers=("review",),
    )
    assert len(kept["top"]) == 3
    assert kept["review"] == []


def test_a_listing_that_is_both_kept_and_omitted_counts_as_kept():
    """The same item can arrive twice in one run with different data -- one
    copy in focus, one out. It is one card, it is being shown, and counting
    it as left out as well would stop the report footer adding up."""
    deals = [
        make_listing(id="x", price=20.0),
        make_listing(id="x", price=500.0, pct_under_market=10.0, dollar_savings=50.0),
    ]
    selection = focus.select(deals, RULES)
    assert ids(selection.kept) == ["x"]
    assert selection.omitted_total == 0


def test_one_card_omitted_for_two_different_reasons_is_counted_once():
    """The same eBay item can arrive twice in one run (two saved searches),
    and the copies can fail different rules. Counting both makes the footer
    claim two cards were left out and print two sentences about one."""
    expensive = make_listing(price=500.0)
    expensive.id = "same-card"
    auction = make_auction(price=35.0, max_rational_bid=30.0)
    auction.id = "same-card"
    selection = focus.select([expensive, auction], RULES)
    assert selection.kept == []
    assert selection.omitted_total == 1
