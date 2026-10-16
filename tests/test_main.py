"""End-to-end orchestration tests with the eBay/email network layer mocked
out, plus unit tests for the CardPro 2.0 evaluation pipeline.

The unit tests below are regression tests for defects
docs/CARDPRO_2_AUDIT.md measured in live production data -- every one of
them was real, not hypothetical.

Craigslist link generation is a pure function (no network), so it isn't
mocked.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from unittest import mock

import pytest

from src import (
    card_identity,
    comps,
    ebay_client,
    ebay_email_alerts,
    emailer,
    main as main_module,
    matcher,
    observability,
    price_history,
    reasons,
)
from src.models import Listing

# A fully-identified modern card: year + set + parallel + card number +
# grader + grade. The CardPro 2.0 engine will only declare a deal off a comp
# level that needs all of those, so test data that omits them (as the old
# fixtures did) can no longer produce a flagged deal -- correctly.
IDENTIFIED_TITLE = "2024 Panini Prizm Caleb Williams Silver #301 PSA 10"

SETTINGS = {
    "discount_threshold_pct": 30,
    "min_savings_dollars": 0,
    "ebay": {
        "category_id": "212",
        "marketplace_id": "EBAY_US",
        "active_listing_limit_per_player": 50,
        "sold_lookback_days": 60,
        "min_comps_required": 3,
    },
    "ebay_alerts": {
        "enabled": False,
        "sender_contains": "ebay.com",
        "lookback_days": 2,
        "price_history_path": "data/ebay_alert_price_history.json",
        "price_history_max_age_days": 180,
    },
    "craigslist": {"site": "chicago", "category": "sss"},
    "dedupe": {"seen_listings_path": "data/seen_listings.json", "prune_after_days": 120},
    "email": {"subject_prefix": "[Card Deals]"},
    "valuation": {"min_comps_required": 3},
}


def _make_project(tmp_path, monkeypatch, *, alerts_enabled: bool, api_enabled=None):
    settings = json.loads(json.dumps(SETTINGS))
    settings["ebay_alerts"]["enabled"] = alerts_enabled

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "watchlist.json").write_text(json.dumps({"players": ["Caleb Williams"]}))
    (tmp_path / "config" / "settings.json").write_text(json.dumps(settings))

    if api_enabled is None:
        api_enabled = not alerts_enabled
    if not api_enabled:
        monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
        monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    else:
        monkeypatch.setenv("EBAY_CLIENT_ID", "fake_id")
        monkeypatch.setenv("EBAY_CLIENT_SECRET", "fake_secret")
    monkeypatch.setenv("GMAIL_ADDRESS", "fake@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "fakepassword")
    monkeypatch.setenv("EMAIL_TO", "fake@gmail.com")

    from src import config as config_module

    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path / "config")

    from src import main as main_module

    importlib.reload(main_module)
    monkeypatch.setattr(main_module, "LOG_PATH", tmp_path / "logs" / "scraper.log")
    return main_module


@pytest.fixture
def project(tmp_path, monkeypatch):
    """eBay API credentials present -> run() takes the Browse/Insights branch."""
    return _make_project(tmp_path, monkeypatch, alerts_enabled=False)


@pytest.fixture
def project_with_alerts_enabled(tmp_path, monkeypatch):
    """No eBay API credentials, alerts enabled -> run() takes the IMAP branch."""
    return _make_project(tmp_path, monkeypatch, alerts_enabled=True)


@pytest.fixture
def project_with_both_sources(tmp_path, monkeypatch):
    """Credentials AND alerts -> run() reads both and merges them."""
    return _make_project(tmp_path, monkeypatch, alerts_enabled=True, api_enabled=True)


def fake_active(query, token, category_id, marketplace_id, limit=50, **_search):
    if "Caleb Williams" not in query:
        return []
    return [
        {
            "itemId": "v1|111111111111|0",
            "legacyItemId": "111111111111",
            "title": IDENTIFIED_TITLE,
            "price": {"value": "200"},
            "itemWebUrl": "https://www.ebay.com/itm/111111111111?hash=x",
            "buyingOptions": ["FIXED_PRICE"],
        },
        {
            "itemId": "v1|222222222222|0",
            "legacyItemId": "222222222222",
            "title": "2024 Panini Prizm Caleb Williams Silver #301 PSA 10 REPRINT",
            "price": {"value": "10"},
            "itemWebUrl": "https://www.ebay.com/itm/222222222222",
            "buyingOptions": ["FIXED_PRICE"],
        },
    ]


def fake_sold(query, token, category_id, marketplace_id, lookback_days, limit=100):
    """Real sold comps for the exact same card -- six of them, so the bucket
    clears every quality gate."""
    if "Caleb Williams" not in query:
        return []
    return [
        {"itemId": "s%d" % i, "title": IDENTIFIED_TITLE, "price": {"value": str(value)}}
        for i, value in enumerate([400, 405, 395, 410, 398, 402])
    ]


def alert_item(url, price, title=IDENTIFIED_TITLE, **extra):
    item = {
        "title": title,
        "url": url,
        "price": price,
        "shipping_price": None,
        "listing_type": "fixed_price",
        "bid_count": None,
        "has_best_offer": False,
        "time_left_text": None,
    }
    item.update(extra)
    return item


# ---------------------------------------------------------------------------
# Unit tests for the CardPro 2.0 evaluation pipeline
# ---------------------------------------------------------------------------

TODAY = datetime(2026, 8, 22, tzinfo=timezone.utc)


# The deal gate's net-profit floor as shipped, read from the real settings so
# these tests follow it rather than restating it.
SHIPPED_MIN_NET_PROFIT = json.loads(
    (main_module.ROOT_DIR / "config" / "settings.json").read_text()
)["economics"]["min_net_profit"]


def fake_cfg(**overrides):
    """A config object with only what evaluate_listings/record_observations
    actually read. Keeps these tests independent of the JSON config files."""
    base = dict(
        players=["Caleb Williams", "Kyle Teel"],
        player_tiers={"Caleb Williams": "young_core"},
        target_cards=[],
        discount_threshold_pct=30.0,
        min_savings_dollars=10.0,
        valuation_min_comps_required=3,
        valuation_half_life_days=30,
        valuation_stale_after_days=45,
        valuation_max_dispersion=0.5,
        valuation_mad_threshold=3.5,
        require_flag_eligible_comp=True,
        fee_marketplace_pct=13.25,
        fee_marketplace_fixed=0.30,
        fee_payment_pct=0.0,
        outbound_shipping=5.0,
        supplies_cost=1.0,
        sales_tax_pct=0.0,
        resale_haircut_pct=5.0,
        # The refinements below are off (or equal to the flat figures) so the
        # hand-worked numbers in these tests stay simple; the shipped values
        # are tested in test_economics.py and by the targeted tests below.
        resale_haircut_asking_pct=5.0,
        fee_small_order_fixed=0.30,
        fee_small_order_max=10.0,
        outbound_shipping_envelope=None,
        envelope_max_sale=20.0,
        supplies_graded_cost=None,
        assumed_inbound_shipping=None,
        # Off by default here so the gross-discount tests keep testing only
        # the gross gates. The shipped floor (SHIPPED_MIN_NET_PROFIT) is
        # asserted by TestNetProfitGate and the shipped-floor siblings below.
        min_net_profit=float("-inf"),
        minutes_per_flip=25.0,
        auction_required_margin_pct=25.0,
        auction_ending_soon_hours=24,
        immediate_alert_min_savings_dollars=150.0,
        immediate_alert_min_discount_pct=40.0,
        cheap_cards_enabled=True,
        cheap_price_ceiling=10.0,
        cheap_min_discount_pct=50.0,
        cheap_min_savings_dollars=3.0,
        cheap_require_desirable_attribute=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_listing(title, price, listing_id="L1", **overrides):
    """Builds a Listing the same way main._build_listing does, so these
    tests exercise the real identity/grade extraction rather than a
    hand-written stand-in that could drift from it."""
    identity = card_identity.extract_card_identity(title)
    grade_info = matcher.detect_grade_details(title)
    matched = matcher.match_players(title, overrides.pop("players", ["Caleb Williams", "Kyle Teel"]))
    fields = dict(
        id=listing_id,
        source="ebay-alert",
        title=title,
        price=price,
        url="https://www.ebay.com/itm/" + listing_id,
        player=matched[0] if matched else "Caleb Williams",
        card_type=grade_info.card_type,
        grader=grade_info.grader,
        grade=grade_info.grade,
        card_identity=identity,
        negative_signals=tuple(identity.negative_signals.value or ()),
        matched_players=tuple(matched),
    )
    fields.update(overrides)
    return Listing(**fields)


def observation(price, listing_id, date="2026-08-20", **overrides):
    base = dict(
        price=price,
        date=date,
        id=listing_id,
        player="Caleb Williams",
        card_type="graded",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="301",
        grader="PSA",
        grade="10",
        qualifier=None,
        basis=comps.BASIS_ASKING,
    )
    base.update(overrides)
    return base


def engine_for(observations, min_comps=3):
    return comps.CompEngine(observations, min_comps_required=min_comps, today=TODAY)

# Comps spread over several separate mornings. A bucket captured in one scan
# is one snapshot however deep it is, and the engine (correctly) refuses to
# declare a deal from it -- so a fixture meant to produce a real opportunity
# has to span real calendar time.
SPREAD_DATES = ("2026-07-28", "2026-08-03", "2026-08-08", "2026-08-13", "2026-08-17", "2026-08-20")


def spread_observations(price, **overrides):
    return [
        observation(price, "o%d" % index, date=date, **overrides)
        for index, date in enumerate(SPREAD_DATES)
    ]


EXACT_TITLE = "2024 Panini Prizm Caleb Williams Silver #301 PSA 10"


# --- Failure mode #5: a truncated grade must never be treated as a real one ---
#
# eBay truncates long titles in its alert emails, so a PSA 10 can arrive as
# "PSA 1...", which parses as PSA 1. CardPro does NOT fetch the item page to
# recover the real title -- that would be automated access to eBay's site,
# which principle #1 forbids. The grade is therefore unknown, and a listing
# whose grade is unknown is not allowed to be valued off a grade-matched comp.


TRUNCATED_TITLE = "2024 Panini Prizm Caleb Williams Silver #301 PSA 1\u2026"



class TestTruncatedTitles:
    def test_truncated_titles_are_marked_uncertain_not_repaired(self):
        listing = make_listing(TRUNCATED_TITLE, 250.0)
        assert listing.grade == "1"  # the whole problem: a PSA 10 parsed as PSA 1
        main_module.mark_truncated_titles([listing])
        assert listing.title_truncated is True
        # The title is left exactly as eBay sent it. There is no recovery
        # step, so nothing rewrites identity behind your back either.
        assert listing.title == TRUNCATED_TITLE
        assert listing.grade == "1"

    def test_untruncated_titles_are_left_alone(self):
        listing = make_listing(EXACT_TITLE, 250.0)
        main_module.mark_truncated_titles([listing])
        assert listing.title_truncated is False

    def test_marking_makes_no_network_call_of_any_kind(self):
        """The point of the change: no HTTP request leaves the process for a
        truncated title, by any route."""
        listing = make_listing(TRUNCATED_TITLE, 250.0)
        with mock.patch("socket.socket", side_effect=AssertionError("network access attempted")):
            main_module.mark_truncated_titles([listing])
        assert listing.title_truncated is True

    def test_truncated_graded_listing_never_becomes_an_opportunity(self):
        # Comps for PSA 1 at $400 -- the grade the truncated title *parses*
        # as. Without the gate this $100 listing reads as a 75%-off deal on a
        # card that is probably a PSA 10, i.e. a wrong grade producing a
        # confident wrong answer.
        psa1_comps = spread_observations(400.0, grade="1")
        listing = make_listing(TRUNCATED_TITLE, 100.0, listing_id="T1", listing_type="fixed_price")
        stats = observability.RunStats()

        main_module.mark_truncated_titles([listing])
        main_module.evaluate_listings([listing], engine_for(psa1_comps), fake_cfg(), stats)

        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.GRADE_UNCERTAIN
        # Rejected BEFORE the comp lookup, so no market value derived from
        # the wrong grade is attached for the report to print.
        assert listing.comp_match is None
        assert listing.market_value is None
        # Stated, not silently dropped -- it lands in NEEDS REVIEW.
        assert stats.rejections.counts()[reasons.Reason.GRADE_UNCERTAIN] == 1

    def test_the_same_listing_untruncated_would_have_been_a_deal(self):
        """Proves the rejection above comes from the uncertain grade and not
        from some unrelated part of the pipeline."""
        psa1_comps = spread_observations(400.0, grade="1")
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 PSA 1", 100.0,
            listing_id="T2", listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.mark_truncated_titles([listing])
        assert listing.title_truncated is False

        main_module.evaluate_listings([listing], engine_for(psa1_comps), fake_cfg(), stats)
        assert listing.is_opportunity is True

    def test_truncated_raw_listing_is_unaffected(self):
        # No grader, no grade -- there is no grade to get wrong, so a
        # truncated raw card is valued normally and can still be a deal.
        raw_comps = spread_observations(400.0, card_type="raw", grader=None, grade=None)
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 Rookie Card\u2026", 100.0,
            listing_id="R1", listing_type="fixed_price",
        )
        assert listing.card_type == "raw"
        assert listing.grade is None
        stats = observability.RunStats()

        main_module.mark_truncated_titles([listing])
        assert listing.title_truncated is True  # still surfaced as a risk

        main_module.evaluate_listings([listing], engine_for(raw_comps), fake_cfg(), stats)
        assert listing.rejection_reason != reasons.Reason.GRADE_UNCERTAIN
        assert listing.is_opportunity is True


# --- An auction's current bid must never enter the comp corpus ---


class TestRecordObservations:
    def test_auctions_are_not_recorded_as_comps(self):
        history = {}
        auction = make_listing(EXACT_TITLE, 45.0, listing_id="A1", listing_type="auction", bid_count=7)
        main_module.record_observations([auction], history, "2026-08-22")
        assert history == {}

    def test_fixed_price_listings_are_recorded_as_asking_basis(self):
        history = {}
        listing = make_listing(EXACT_TITLE, 245.0, listing_id="F1", listing_type="fixed_price")
        assert main_module.record_observations([listing], history, "2026-08-22") == 1
        stored = history["Caleb Williams|graded"][0]
        assert stored["basis"] == comps.BASIS_ASKING
        assert stored["parallel"] == "Silver"
        assert stored["grade"] == "10"

    def test_blocked_listings_are_not_recorded(self):
        history = {}
        for title in (
            "1986 Fleer Caleb Williams REPRINT",
            "Lot of 5 Caleb Williams cards",
            "2024 Panini Prizm Caleb Williams Hobby Box sealed",
        ):
            main_module.record_observations([make_listing(title, 20.0)], history, "2026-08-22")
        assert history == {}

    def test_listings_without_a_price_are_not_recorded(self):
        history = {}
        main_module.record_observations([make_listing(EXACT_TITLE, None)], history, "2026-08-22")
        assert history == {}


# --- Evaluation ---


class TestEvaluateListings:
    def test_a_listing_is_never_part_of_its_own_comp(self):
        # Failure mode #4. The listing under test is in the corpus; with
        # min_comps=3 and only three observations, including itself would
        # both change the median and let a lowball drag its own "market
        # value" toward itself.
        observations = [observation(400.0, "L1"), observation(400.0, "o2"), observation(400.0, "o3")]
        listing = make_listing(EXACT_TITLE, 100.0, listing_id="L1")
        stats = observability.RunStats()

        # Only two comps remain once L1 is excluded, so there is no valuation
        # at all rather than one built partly from the listing itself.
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.comp_match is None
        assert listing.rejection_reason == reasons.Reason.NO_COMP_AT_ANY_LEVEL

    def test_exclusion_does_not_break_a_healthy_bucket(self):
        observations = spread_observations(400.0) + [observation(100.0, "L1")]
        listing = make_listing(EXACT_TITLE, 100.0, listing_id="L1", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.market_value == 400.0  # its own $100 didn't drag the median down
        assert listing.is_opportunity is True

    def test_reprint_is_blocked_with_its_own_reason(self):
        listing = make_listing("1986 Fleer Caleb Williams #57 REPRINT", 20.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats)
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.REPRINT
        assert stats.blocked_by_negative_signal == 1

    def test_lot_is_blocked(self):
        listing = make_listing("Lot of 5 Caleb Williams rookie cards", 40.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats)
        assert listing.rejection_reason == reasons.Reason.LOT

    def test_multi_player_card_is_not_valued_against_one_player(self):
        listing = make_listing("2024 Prizm Dual Auto Caleb Williams Kyle Teel #5", 300.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats)
        assert listing.rejection_reason == reasons.Reason.MULTI_PLAYER_CARD

    def test_auction_is_never_an_opportunity_however_cheap(self):
        # Spread over real mornings: a bucket captured in one scan is one
        # snapshot, and the engine refuses to stand behind it. This fixture
        # used the single-date form, so it was asserting that a max bid comes
        # out of a comp CardPro will NOT stand behind -- the defect below.
        listing = make_listing(EXACT_TITLE, 20.0, listing_id="A1", listing_type="auction", bid_count=3)
        stats = observability.RunStats()
        main_module.evaluate_listings(
            [listing], engine_for(spread_observations(400.0)), fake_cfg(), stats
        )

        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.AUCTION_CURRENT_BID_NOT_A_PRICE
        # ...but it IS valued, and it gets the number that makes it actionable
        assert listing.market_value == 400.0
        assert listing.max_rational_bid > 0

    def test_a_context_only_comp_never_produces_a_max_bid(self):
        """The max bid is the one number in the report that tells you to
        spend money -- "the most you can pay ... Above this, stop." On the
        live corpus it was being computed from
        price-bracket medians, putting a $729.31 ceiling on a $125 card two
        lines under "Market: not established"."""
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 20.0, listing_id="A1", listing_type="auction", bid_count=3)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)

        assert listing.comp_match is not None
        assert listing.comp_match.flag_eligible is False
        assert listing.max_rational_bid is None

    def test_context_only_comp_can_never_flag_a_deal(self):
        # Failure mode #1: the price-tier level is defined by price, so the
        # cheap end of every bucket is automatically "under market". It may
        # still be shown as context -- it may never declare a deal.
        observations = [
            observation(
                p, "o%d" % i, card_type="raw", grader=None, grade=None, parallel=None, card_number=None,
                set_name=None, year=None,
            )
            for i, p in enumerate([40.0, 45.0, 50.0, 55.0, 60.0])
        ]
        listing = make_listing("Caleb Williams rookie card", 25.0, listing_id="L9", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)

        assert listing.comp_match is not None
        assert listing.comp_match.level == "price_tier"
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.CONTEXT_ONLY_LEVEL

    def test_different_grades_are_different_markets(self):
        # Failure mode #3. A PSA 9 must not be valued off PSA 10 comps.
        psa10_comps = [observation(400.0, "o%d" % i) for i in range(6)]
        psa9 = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 PSA 9", 150.0, listing_id="L9",
            listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([psa9], engine_for(psa10_comps), fake_cfg(), stats)
        assert psa9.is_opportunity is False
        # No level -- not even a context-only one -- will hand a PSA 9 the
        # PSA 10 median. Nothing to say beats saying the wrong thing.
        assert psa9.market_value is None
        assert psa9.rejection_reason == reasons.Reason.NO_COMP_AT_ANY_LEVEL

    def test_below_threshold_records_the_specific_reason(self):
        observations = spread_observations(300.0)
        listing = make_listing(EXACT_TITLE, 280.0, listing_id="L2", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.rejection_reason == reasons.Reason.BELOW_DISCOUNT_THRESHOLD

    def test_below_min_savings_records_the_specific_reason(self):
        observations = spread_observations(20.0)
        listing = make_listing(EXACT_TITLE, 12.0, listing_id="L3", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.pct_under_market >= 30
        assert listing.rejection_reason == reasons.Reason.BELOW_MIN_SAVINGS

    def test_shipping_is_included_in_the_discount_maths(self):
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(
            EXACT_TITLE, 260.0, listing_id="L4", listing_type="fixed_price", shipping_price=40.0
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.dollar_savings == pytest.approx(100.0)  # 400 - (260 + 40), not 400 - 260

    def test_economics_are_attached_with_visible_assumptions(self):
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 200.0, listing_id="L5", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.economics is not None
        assert listing.economics.assumptions  # never a bare unexplained profit figure

    def test_every_listing_leaves_with_an_outcome_or_a_reason(self):
        # The invariant the whole reasons/observability layer exists for:
        # 21% of listings used to vanish with neither.
        listings = [
            make_listing(EXACT_TITLE, 200.0, listing_id="a", listing_type="fixed_price"),
            make_listing("1986 Fleer Caleb Williams REPRINT", 5.0, listing_id="b"),
            make_listing("Caleb Williams mystery card", None, listing_id="c"),
            make_listing("Lot of 3 Kyle Teel cards", 15.0, listing_id="d"),
            make_listing(EXACT_TITLE, 50.0, listing_id="e", listing_type="auction", bid_count=2),
        ]
        stats = observability.RunStats()
        main_module.evaluate_listings(listings, engine_for([observation(400.0, "o%d" % i) for i in range(6)]),
                                      fake_cfg(), stats)
        for listing in listings:
            assert listing.is_opportunity or listing.rejection_reason, listing.title
        assert stats.unexplained_count() == 0

    def test_target_hit_is_recorded_separately_from_the_deal_verdict(self):
        from src import targets

        cfg = fake_cfg(
            target_cards=targets.load_targets(
                [
                    {
                        "label": "Prizm Silver PSA 10",
                        "player": "Caleb Williams",
                        "year": 2024,
                        "set_name": "Prizm",
                        "parallel": "Silver",
                        "grader": "PSA",
                        "grade": "10",
                        "buy_zone": 500,
                    }
                ]
            )
        )
        listing = make_listing(EXACT_TITLE, 450.0, listing_id="T1", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), cfg, stats)

        # In the buy zone the user set, but with no comps there is no claim
        # that it's underpriced -- those are separate answers.
        assert listing.target_hit is not None and listing.target_hit.in_buy_zone
        assert listing.is_opportunity is False


class TestApplyDedupe:
    def _opportunity(self, price, listing_id="D1"):
        listing = make_listing(EXACT_TITLE, price, listing_id=listing_id, listing_type="fixed_price")
        listing.is_opportunity = True
        return listing

    def test_first_sighting_is_reported_and_recorded(self):
        seen = {}
        stats = observability.RunStats()
        listing = self._opportunity(200.0)
        main_module.apply_dedupe([listing], seen, "2026-08-22", stats)
        assert listing.is_opportunity is True
        assert seen["D1"]["price"] == 200.0

    def test_same_price_next_run_is_suppressed_with_a_reason(self):
        seen = {"D1": {"price": 200.0, "first_seen": "2026-08-21", "last_flagged": "2026-08-21"}}
        stats = observability.RunStats()
        listing = self._opportunity(200.0)
        main_module.apply_dedupe([listing], seen, "2026-08-22", stats)
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.PRICE_NOT_DROPPED
        assert stats.duplicates_suppressed == 1

    def test_price_drop_is_reported_and_labelled(self):
        seen = {"D1": {"price": 200.0, "first_seen": "2026-08-21", "last_flagged": "2026-08-21"}}
        stats = observability.RunStats()
        listing = self._opportunity(150.0)
        main_module.apply_dedupe([listing], seen, "2026-08-22", stats)
        assert listing.is_opportunity is True
        assert listing.is_price_drop is True
        assert listing.previous_price == 200.0
        assert stats.price_drops == 1

    def test_a_card_not_shown_before_passes_through(self):
        stats = observability.RunStats()
        auction = make_listing(EXACT_TITLE, 50.0, listing_id="A1", listing_type="auction")
        kept = main_module.apply_dedupe([auction], {}, "2026-08-22", stats)
        assert kept == [auction]
        assert stats.duplicates_suppressed == 0


    SEEN = {"C1": {"price": 100.0, "first_seen": "2026-08-21", "last_flagged": "2026-08-21"}}

    def _card(self, price, **overrides):
        return make_listing(EXACT_TITLE, price, listing_id="C1", listing_type="fixed_price", **overrides)

    def test_a_card_shown_before_is_left_out_and_counted(self):
        stats = observability.RunStats()
        kept = main_module.apply_dedupe([self._card(100.0)], dict(self.SEEN), "2026-08-22", stats)
        assert kept == []
        assert stats.previously_shown_suppressed == 1

    def test_it_comes_back_on_a_five_percent_drop_and_not_less(self):
        stats = observability.RunStats()
        assert main_module.apply_dedupe([self._card(94.0)], dict(self.SEEN), "2026-08-22", stats)
        assert not main_module.apply_dedupe([self._card(97.0)], dict(self.SEEN), "2026-08-22", stats)

    def test_a_target_hit_is_always_shown(self):
        hit = self._card(100.0, target_hit=object())
        kept = main_module.apply_dedupe([hit], dict(self.SEEN), "2026-08-22", observability.RunStats())
        assert kept == [hit]

    def test_only_printed_cards_are_recorded_as_seen(self):
        seen = {}
        shown, hidden = self._card(50.0), make_listing(EXACT_TITLE, 60.0, listing_id="C2")
        main_module.record_shown([shown, hidden], frozenset({"C1"}), seen, "2026-08-22")
        assert set(seen) == {"C1"} and seen["C1"]["price"] == 50.0


class TestBrowseFields:
    """Browse API item dicts -> the fields the alert path already fills, so
    the report reads both sources one way."""

    def test_an_auction_is_priced_at_its_current_bid(self):
        item = {"buyingOptions": ["AUCTION"], "currentBidPrice": {"value": "3.50"}}
        assert ebay_client.extract_listing_price(item) == 3.5

    def test_fixed_price_reads_price(self):
        item = {"buyingOptions": ["FIXED_PRICE"], "price": {"value": "20"}}
        assert ebay_client.extract_listing_price(item) == 20.0

    def test_shipping_takes_the_cheapest_stated_cost(self):
        item = {"shippingOptions": [{"shippingCost": {"value": "4.99"}}, {"shippingCost": {"value": "1.25"}}]}
        assert ebay_client.extract_shipping(item) == 1.25

    def test_calculated_shipping_is_unknown_not_free(self):
        assert ebay_client.extract_shipping({"shippingOptions": [{"shippingCostType": "CALCULATED"}]}) is None
        assert ebay_client.extract_shipping({}) is None

    def test_time_left_uses_the_alert_email_format(self):
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        def left(end):
            return ebay_client.time_left_text({"itemEndDate": end}, now)
        assert left("2026-10-17T16:30:00.000Z") == "2d 04h"
        assert left("2026-10-15T15:12:00.000Z") == "3h 12m"
        assert left("2026-10-15T12:45:00.000Z") == "45m"
        assert left("2026-10-15T11:00:00.000Z") is None  # already ended
        assert left("garbage") is None
        assert ebay_client.time_left_text({}, now) is None


class TestBrowsePaging:
    def _fake_get(self, pages, calls):
        class Resp:
            status_code = 200
            def __init__(self, payload):
                self._payload = payload
                self.text = ""
            def json(self):
                return self._payload
        def get(url, headers=None, params=None, timeout=None):
            calls.append(dict(params))
            index = int(params["offset"]) // int(params["limit"])
            return Resp(pages[index])
        return get

    def test_walks_pages_until_max_pages_and_passes_sort(self, monkeypatch):
        calls = []
        pages = [
            {"itemSummaries": [{"itemId": "a"}], "next": "more"},
            {"itemSummaries": [{"itemId": "b"}], "next": "more"},
            {"itemSummaries": [{"itemId": "c"}]},
        ]
        monkeypatch.setattr(ebay_client.requests, "get", self._fake_get(pages, calls))
        items = ebay_client.search_active_listings(
            "Caleb Williams", "t", "261328", "EBAY_US", limit=200, sort="newlyListed", max_pages=2,
        )
        assert [i["itemId"] for i in items] == ["a", "b"]
        assert [c["offset"] for c in calls] == ["0", "200"]
        assert all(c["sort"] == "newlyListed" for c in calls)

    def test_stops_when_ebay_says_there_is_no_next_page(self, monkeypatch):
        calls = []
        pages = [{"itemSummaries": [{"itemId": "a"}]}]
        monkeypatch.setattr(ebay_client.requests, "get", self._fake_get(pages, calls))
        items = ebay_client.search_active_listings(
            "x", "t", "261328", "EBAY_US", limit=200, buying_options=("AUCTION",), max_pages=5,
        )
        assert len(items) == 1 and len(calls) == 1
        assert calls[0]["filter"] == "buyingOptions:{AUCTION}"
        assert "sort" not in calls[0]


    def test_a_min_price_adds_a_price_filter(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ebay_client.requests, "get", self._fake_get([{"itemSummaries": []}], calls))
        ebay_client.search_active_listings("x", "t", "261328", "EBAY_US", sort="newlyListed", min_price=20.0)
        assert calls[0]["filter"] == "buyingOptions:{FIXED_PRICE|AUCTION},price:[20],priceCurrency:USD"

    def test_no_min_price_means_no_price_filter(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ebay_client.requests, "get", self._fake_get([{"itemSummaries": []}], calls))
        ebay_client.search_active_listings("x", "t", "261328", "EBAY_US")
        assert "price" not in calls[0]["filter"]

    def test_only_the_newest_pass_gets_the_floor(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        cfg.ebay_auction_pages_per_player = 1
        searches = []
        monkeypatch.setattr(project.ebay_client, "search_active_listings",
                            lambda query, **kwargs: searches.append(kwargs) or [])
        project.fetch_ebay_active(cfg, ["Caleb Williams"], "t", observability.RunStats())
        by_sort = {search["sort"]: search for search in searches}
        assert by_sort["newlyListed"]["min_price"] == 20.0
        assert "min_price" not in by_sort["endingSoonest"]


    def test_a_non_200_is_reported_back_to_the_caller(self, monkeypatch):
        class Down:
            status_code = 429
            text = "Too many requests"
        monkeypatch.setattr(ebay_client.requests, "get", lambda *a, **k: Down())
        failures = []
        assert ebay_client.search_active_listings("x", "t", "261328", "EBAY_US", failures=failures) == []
        assert failures == ["429 Too many requests"]

    def test_a_refused_search_reaches_the_email_as_a_warning(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        def refuse(query, failures=None, **kwargs):
            if query == "Kyle Teel":
                failures.append("500 Internal Server Error")
            return []
        monkeypatch.setattr(project.ebay_client, "search_active_listings", refuse)
        stats = observability.RunStats()
        project.fetch_ebay_active(cfg, ["Caleb Williams", "Kyle Teel"], "t", stats)
        warning = " ".join(stats.warnings)
        assert "for 1 player(s) (Kyle Teel)" in warning
        assert "their listings are missing" in warning  # every Kyle Teel search failed
        assert "500 Internal Server Error" in warning
        assert stats.breakage_warnings == []

    def test_one_failed_pass_says_some_listings_may_be_missing(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        cfg.ebay_auction_pages_per_player = 1
        def auction_pass_refused(query, failures=None, sort=None, **kwargs):
            if sort == "endingSoonest":
                failures.append("429 Too many requests")
            return []
        monkeypatch.setattr(project.ebay_client, "search_active_listings", auction_pass_refused)
        stats = observability.RunStats()
        project.fetch_ebay_active(cfg, ["Caleb Williams"], "t", stats)
        warning = " ".join(stats.warnings)
        assert "refused 1 of 2 searches" in warning
        assert "some listings may be missing" in warning
        assert stats.breakage_warnings == []

    def test_every_search_refused_marks_the_run_broken(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        def refuse_all(query, failures=None, **kwargs):
            failures.append("401 Unauthorized")
            return []
        monkeypatch.setattr(project.ebay_client, "search_active_listings", refuse_all)
        stats = observability.RunStats()
        project.fetch_ebay_active(cfg, ["Caleb Williams", "Kyle Teel"], "t", stats)
        assert stats.breakage_warnings and "refused 2 of 2 searches" in stats.breakage_warnings[0]

    def test_no_warning_when_every_search_succeeds(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        monkeypatch.setattr(project.ebay_client, "search_active_listings", lambda query, **kw: [])
        stats = observability.RunStats()
        project.fetch_ebay_active(cfg, ["Caleb Williams"], "t", stats)
        assert not any("refused" in w for w in stats.warnings)


class TestListingAge:
    TODAY = date(2026, 10, 16)

    def test_an_api_listing_is_aged_from_ebays_creation_date(self):
        listing = make_listing(EXACT_TITLE, 20.0, listing_id="A", listed_at="2026-10-14T08:00:00.000Z")
        main_module.annotate_listing_age([listing], {}, self.TODAY)
        assert (listing.age_days, listing.age_basis) == (2, "listed")

    def test_an_alert_listing_is_aged_from_the_corpus_first_sighting(self):
        listing = make_listing(EXACT_TITLE, 20.0, listing_id="B")
        history = {"Caleb Williams|graded": [{"id": "B", "date": "2026-10-10", "price": 20.0}]}
        main_module.annotate_listing_age([listing], history, self.TODAY)
        assert (listing.age_days, listing.age_basis) == (6, "first seen")

    def test_an_unreadable_creation_date_falls_back_and_nothing_known_is_none(self):
        bad = make_listing(EXACT_TITLE, 20.0, listing_id="B", listed_at="yesterday")
        unknown = make_listing(EXACT_TITLE, 20.0, listing_id="C")
        history = {"x": [{"id": "B", "date": "2026-10-15"}]}
        main_module.annotate_listing_age([bad, unknown], history, self.TODAY)
        assert (bad.age_days, bad.age_basis) == (1, "first seen")
        assert unknown.age_days is None

    def test_browse_creation_date_reaches_the_listing(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        item = {"itemId": "v1|123456789012|0", "legacyItemId": "123456789012",
                "itemWebUrl": "https://www.ebay.com/itm/123456789012", "title": EXACT_TITLE,
                "price": {"value": "20.00"}, "buyingOptions": ["FIXED_PRICE"],
                "itemCreationDate": "2026-10-15T10:00:00.000Z"}
        monkeypatch.setattr(project.ebay_client, "search_active_listings", lambda query, **kw: [item])
        [listing] = project.fetch_ebay_active(cfg, ["Caleb Williams"], "t", observability.RunStats())
        assert listing.listed_at == "2026-10-15T10:00:00.000Z"


class TestCorpusSizeWarning:
    def test_quiet_below_the_threshold_and_when_missing(self, tmp_path):
        small = tmp_path / "c.json"
        small.write_text("{}")
        assert main_module.corpus_size_warning(small) is None
        assert main_module.corpus_size_warning(tmp_path / "missing.json") is None

    def test_warns_before_github_refuses_the_push(self, tmp_path, monkeypatch):
        big = tmp_path / "c.json"
        big.write_text("{}")
        monkeypatch.setattr(main_module, "CORPUS_WARN_MB", 0)
        assert "100MB" in main_module.corpus_size_warning(big)


class TestBothSources:
    """The API used to replace the alert path outright -- and with it the
    comp corpus, which only the alert path read or wrote."""

    def test_the_api_id_is_the_same_canonical_url_the_alerts_use(self, project):
        item = {"itemId": "v1|336749665825|0", "legacyItemId": "336749665825",
                "itemWebUrl": "https://www.ebay.com/itm/336749665825?hash=abc"}
        assert project._browse_listing_id(item) == "https://www.ebay.com/itm/336749665825"

    def test_an_item_both_sources_saw_keeps_the_api_copy(self):
        api = make_listing(EXACT_TITLE, 200.0, listing_id="https://www.ebay.com/itm/1",
                           source="ebay", listing_type="fixed_price")
        alert_same = make_listing("2024 Panini Prizm Caleb Williams Silver #301 PSA 1...", 200.0,
                                  listing_id="https://www.ebay.com/itm/1")
        alert_only = make_listing(EXACT_TITLE, 150.0, listing_id="https://www.ebay.com/itm/2")
        merged, summary = main_module.merge_sources([api], [alert_same, alert_only])
        assert merged == [api, alert_only]
        assert "1 from the eBay API, 2 from alert emails, 1 in both" in summary

    def test_one_item_from_both_passes_is_one_listing(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        cfg.ebay_auction_pages_per_player = 1
        monkeypatch.setattr(project.ebay_client, "search_active_listings", fake_active)
        stats = observability.RunStats()
        listings = project.fetch_ebay_active(cfg, ["Caleb Williams"], "t", stats)
        assert [listing.id for listing in listings].count("https://www.ebay.com/itm/111111111111") == 1

    def test_a_full_newest_walk_warns_that_listings_were_missed(self, project, monkeypatch):
        from src import config as config_module
        cfg = config_module.load_config()
        cfg.ebay_active_listing_limit = 2
        cfg.ebay_newest_pages_per_player = 1
        monkeypatch.setattr(project.ebay_client, "search_active_listings", fake_active)
        stats = observability.RunStats()
        project.fetch_ebay_active(cfg, ["Caleb Williams"], "t", stats)
        assert any("newest listings for 1 player(s) (Caleb Williams)" in w for w in stats.warnings)

    def test_an_api_outage_falls_back_to_the_alerts(self, project_with_both_sources, monkeypatch):
        main = project_with_both_sources
        def down(*_args):
            raise main.requests.ConnectionError("eBay down")
        monkeypatch.setattr(main.ebay_client, "get_app_token", down)
        monkeypatch.setattr(
            main.ebay_email_alerts, "fetch_alert_listings",
            lambda *a, counters=None, **k: [alert_item("https://www.ebay.com/itm/333333333333", 25.0)],
        )
        sent = []
        monkeypatch.setattr(main.emailer, "send_email",
                            lambda subject, body, *rest, html_body=None: sent.append((subject, body)))
        main.run(argparse.Namespace(dry_run=False))
        assert len(sent) == 1
        assert "The eBay API failed this run (ConnectionError)" in sent[0][1]

    def test_api_listings_land_in_the_same_corpus_as_alert_listings(
        self, project_with_both_sources, monkeypatch, tmp_path
    ):
        main = project_with_both_sources
        monkeypatch.setattr(main.ebay_client, "get_app_token", lambda *_: "t")
        monkeypatch.setattr(main.ebay_client, "search_active_listings", fake_active)
        monkeypatch.setattr(main.ebay_client, "search_sold_items",
                            lambda *a, **k: pytest.fail("Insights is off by default"))
        monkeypatch.setattr(
            main.ebay_email_alerts, "fetch_alert_listings",
            lambda *a, counters=None, **k: [alert_item("https://www.ebay.com/itm/333333333333", 25.0)],
        )
        sent = []
        monkeypatch.setattr(main.emailer, "send_email",
                            lambda subject, body, *rest, html_body=None: sent.append(body))
        main.run(argparse.Namespace(dry_run=False))

        corpus = json.loads((tmp_path / "data" / "ebay_alert_price_history.json").read_text())
        ids = {o["id"] for rows in corpus.values() for o in rows}
        assert "https://www.ebay.com/itm/111111111111" in ids  # from the API
        assert "https://www.ebay.com/itm/333333333333" in ids  # from the alerts
        assert "https://www.ebay.com/itm/222222222222" not in ids  # a reprint is never a comp
        assert "Sources: 2 from the eBay API, 1 from alert emails, 0 in both" in sent[0]


class TestFlagEligibilityOverride:
    """`valuation.require_flag_eligible_comp` was loaded from config but
    never read -- setting it to false silently did nothing. It is a real
    escape hatch now, and a loud one."""

    def _context_only_setup(self):
        observations = [
            observation(
                p, "o%d" % i, card_type="raw", grader=None, grade=None,
                parallel=None, card_number=None, set_name=None, year=None,
            )
            for i, p in enumerate([40.0, 45.0, 50.0, 55.0, 60.0])
        ]
        listing = make_listing("Caleb Williams rookie card", 25.0, listing_id="C1", listing_type="fixed_price")
        return observations, listing

    def test_default_refuses_to_flag_from_a_context_only_comp(self):
        observations, listing = self._context_only_setup()
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.CONTEXT_ONLY_LEVEL

    def test_override_allows_it_but_warns_loudly(self):
        observations, listing = self._context_only_setup()
        stats = observability.RunStats()
        cfg = fake_cfg(require_flag_eligible_comp=False)
        main_module.evaluate_listings([listing], engine_for(observations), cfg, stats)

        # Gross gates only (net floor off in fake_cfg): see the sibling below.
        assert listing.is_opportunity is True
        assert listing.comp_match.level == "price_tier"
        warning = " ".join(stats.warnings)
        assert "require_flag_eligible_comp is FALSE" in warning
        assert "cannot be evidence about price" in warning

    def test_override_still_cannot_beat_the_shipped_net_floor(self):
        # $25 against a $50 median nets a few dollars: under the shipped
        # floor this is not an opportunity, override or not.
        observations, listing = self._context_only_setup()
        cfg = fake_cfg(require_flag_eligible_comp=False, min_net_profit=SHIPPED_MIN_NET_PROFIT)
        main_module.evaluate_listings([listing], engine_for(observations), cfg, observability.RunStats())
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.BELOW_MIN_NET_PROFIT

    def test_override_does_not_warn_when_left_on(self):
        stats = observability.RunStats()
        main_module.evaluate_listings([], engine_for([]), fake_cfg(), stats)
        assert stats.warnings == []


class TestCheapCards:
    """Sub-$10 cards are allowed through, but "cheap" and "junk" are gated
    separately. The old flat $10 dollar floor conflated them: a $4 card worth
    $12 is 67% off and was being rejected outright."""

    def _cheap_comps(self, median=12.0, **identity):
        fields = dict(
            card_type="raw", grader=None, grade=None, year=2024,
            set_name="Prizm", parallel="Silver", card_number="301",
        )
        fields.update(identity)
        return spread_observations(median, **fields)

    def test_a_cheap_card_with_a_real_attribute_can_now_be_an_opportunity(self):
        # Gross cheap-card rules only (net floor off in fake_cfg); the shipped
        # floor rejects this card -- see the shipped-floor test below.
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 RC", 4.0,
            listing_id="CH1", listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._cheap_comps()), fake_cfg(), stats)

        assert listing.is_cheap is True
        assert listing.is_opportunity is True
        assert listing.dollar_savings == pytest.approx(8.0)

    def test_the_old_ten_dollar_floor_would_have_rejected_it(self):
        # Pinning the actual regression: the same card fails under a config
        # where the cheap rules are off and the flat floor applies.
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 RC", 4.0,
            listing_id="CH2", listing_type="fixed_price",
        )
        cfg = fake_cfg(cheap_cards_enabled=False, min_savings_dollars=10.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._cheap_comps()), cfg, stats)
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.BELOW_MIN_SAVINGS

    def test_a_cheap_card_with_no_distinguishing_attribute_is_rejected_as_common(self):
        listing = make_listing("Caleb Williams 2024 Panini card", 4.0, listing_id="CH3",
                               listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings(
            [listing], engine_for(self._cheap_comps(parallel=None, card_number=None)), fake_cfg(), stats
        )
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.COMMON_CARD

    def test_common_cards_are_counted_not_silently_dropped(self):
        listing = make_listing("Caleb Williams 2024 Panini card", 2.0, listing_id="CH4",
                               listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats)
        assert stats.rejections.counts()[reasons.Reason.COMMON_CARD] == 1
        assert stats.unexplained_count() == 0

    def test_cheap_cards_face_a_higher_percentage_bar(self):
        # 40% off is plenty for a $200 card and not enough for a $6 one.
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 RC", 7.2,
            listing_id="CH5", listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._cheap_comps()), fake_cfg(), stats)
        assert listing.pct_under_market == pytest.approx(40.0)
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.BELOW_DISCOUNT_THRESHOLD

    def test_an_expensive_plain_card_is_never_treated_as_common(self):
        listing = make_listing("Caleb Williams 2024 Panini card", 250.0, listing_id="CH6",
                               listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats)
        assert listing.rejection_reason != reasons.Reason.COMMON_CARD
        assert listing.is_cheap is False

    def test_cheap_buys_are_marked_as_uneconomic_to_flip(self):
        # Gross cheap-card rules only (net floor off in fake_cfg); the shipped
        # floor rejects this card -- see the shipped-floor test below.
        # Postage and fees eat the spread at this price. That is arithmetic,
        # not a warning -- the report says "collector buy" rather than showing
        # a scary ROI on a card nobody would flip.
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 RC", 4.0,
            listing_id="CH7", listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._cheap_comps()), fake_cfg(), stats)
        assert listing.is_opportunity is True
        assert listing.resale_uneconomic is True

    def test_disabling_cheap_rules_applies_ordinary_thresholds_everywhere(self):
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 RC", 4.0,
            listing_id="CH8", listing_type="fixed_price",
        )
        cfg = fake_cfg(cheap_cards_enabled=False, min_savings_dollars=3.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._cheap_comps()), cfg, stats)
        assert listing.is_cheap is False
        assert listing.is_opportunity is True  # 67% off clears the ordinary 30% bar

    # The three tests above that end in is_opportunity True test the GROSS
    # cheap-card rules with the net floor off. Under the shipped floor no
    # fixed-price cheap card or collector buy is an opportunity: a $12 card
    # cannot net $15 at any purchase price.
    @pytest.mark.parametrize(
        "overrides",
        [{}, {"cheap_cards_enabled": False, "min_savings_dollars": 3.0}],
        ids=["cheap_rules_on", "cheap_rules_off"],
    )
    def test_at_the_shipped_net_floor_a_cheap_card_is_never_an_opportunity(self, overrides):
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 RC", 4.0,
            listing_id="CH9", listing_type="fixed_price",
        )
        cfg = fake_cfg(min_net_profit=SHIPPED_MIN_NET_PROFIT, **overrides)
        main_module.evaluate_listings([listing], engine_for(self._cheap_comps()), cfg, observability.RunStats())
        assert listing.pct_under_market >= 50  # clears every gross bar
        assert listing.resale_uneconomic is True
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.BELOW_MIN_NET_PROFIT


class TestSoldCompsWiring:
    """Hand-entered sold prices are the only real transactions in the corpus.
    They must actually reach the engine, and they must outrank asking prices
    -- see src/sold_comps.py."""

    def test_sold_comps_are_loaded_into_the_corpus(self, tmp_path, monkeypatch):
        from src import sold_comps

        path = tmp_path / "sold_comps.json"
        path.write_text(json.dumps({"sales": [
            {"player": "Caleb Williams", "year": 2024, "set_name": "Prizm", "parallel": "Silver",
             "card_number": "301", "grader": "PSA", "grade": "10", "price": 400.0,
             "date": "2026-08-0%d" % d, "source": "130point"}
            for d in (1, 5, 9)
        ]}))
        loaded = sold_comps.load(path)
        assert len(loaded) == 3
        assert all(o["basis"] == comps.BASIS_SOLD for o in loaded)

    def test_a_sold_comp_can_reach_high_confidence_where_asking_never_can(self):
        from src import sold_comps

        sales = [
            {"player": "Caleb Williams", "year": 2024, "set_name": "Prizm", "parallel": "Silver",
             "card_number": "301", "grader": "PSA", "grade": "10", "price": p, "date": d,
             "source": "130point"}
            for p, d in [(400.0, "2026-08-01"), (405.0, "2026-08-08"), (395.0, "2026-08-15"),
                         (402.0, "2026-08-18"), (398.0, "2026-08-20")]
        ]
        engine = engine_for(sold_comps.parse_sales(sales))
        match = engine.lookup(
            player="Caleb Williams", card_type="graded", price=250.0, grader="PSA", grade="10",
            year=2024, set_name="Prizm", parallel="Silver", card_number="301",
        )
        assert match.stats.basis == comps.BASIS_SOLD
        assert match.confidence == "high"
        assert match.flag_eligible is True

    def test_missing_sold_comps_file_is_the_normal_case(self, tmp_path):
        from src import sold_comps

        assert sold_comps.load(tmp_path / "nope.json") == []


class TestOneCardHasOneAcquisitionCost:
    """dollar_savings was computed against Listing.total_cost (no sales tax)
    while economics used Acquisition.total_cost (with it). At any non-zero
    sales_tax_pct one card block printed two different totals -- a Cost line
    saying $32.50 above a profit figure whose arithmetic only works at
    $35.10. Latent at the shipped 0.0, and settings.json explicitly invites
    tuning it."""

    def _evaluated(self, sales_tax_pct):
        listing = make_listing(EXACT_TITLE, 28.0, shipping_price=4.50)
        stats = observability.RunStats()
        main_module.evaluate_listings(
            [listing],
            engine_for(spread_observations(72.0)),
            fake_cfg(sales_tax_pct=sales_tax_pct),
            stats,
        )
        return listing

    def test_the_discount_and_the_economics_agree_with_tax_on(self):
        listing = self._evaluated(8.0)
        assert listing.dollar_savings == pytest.approx(
            listing.economics.estimated_market_value - listing.economics.acquisition_cost
        )

    def test_tax_makes_the_discount_smaller_not_unchanged(self):
        with_tax = self._evaluated(8.0)
        without = self._evaluated(0.0)
        assert with_tax.dollar_savings < without.dollar_savings


class TestNetProfitGate:
    """The gross-discount gates ignore what fees, postage and supplies take
    out of a flip; most corpus flags that cleared them netted under $15."""

    def _evaluated(self, market, price):
        listing = make_listing(EXACT_TITLE, price, listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings(
            [listing], engine_for(spread_observations(market)), fake_cfg(min_net_profit=15.0), stats
        )
        return listing, stats

    def test_a_big_gross_discount_that_nets_under_the_floor_is_rejected(self):
        # 33% off and $20 saved clear both gross gates; selling at $57 after
        # the haircut, ~$7.85 fees and $6 out leave ~$3.15 profit.
        listing, stats = self._evaluated(60.0, 40.0)
        assert listing.pct_under_market >= 30
        assert listing.dollar_savings >= 10
        assert listing.economics.expected_profit < 15
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.BELOW_MIN_NET_PROFIT
        assert stats.rejections.counts() == {reasons.Reason.BELOW_MIN_NET_PROFIT: 1}

    def test_a_deal_that_nets_the_floor_passes(self):
        listing, _ = self._evaluated(400.0, 200.0)
        assert listing.economics.expected_profit >= 15
        assert listing.is_opportunity is True
        assert listing.rejection_reason is None


class TestMaxPriceOnEveryPricedListing:
    """One MAX per listing with a flag-eligible comp: the highest item price
    that still nets min_net_profit. Fixed price too, not only auctions."""

    def _evaluated(self, price, listing_type, **cfg_overrides):
        listing = make_listing(EXACT_TITLE, price, listing_type=listing_type, bid_count=1)
        cfg = fake_cfg(**dict(
            dict(min_net_profit=SHIPPED_MIN_NET_PROFIT, auction_required_margin_pct=0.0), **cfg_overrides
        ))
        main_module.evaluate_listings(
            [listing], engine_for(spread_observations(400.0)), cfg, observability.RunStats()
        )
        return listing

    def test_a_fixed_price_listing_gets_a_max(self):
        listing = self._evaluated(390.0, "fixed_price")
        assert listing.max_rational_bid is not None and listing.max_rational_bid > 0

    def test_a_flagged_deal_is_always_at_or_under_its_max(self):
        listing = self._evaluated(200.0, "fixed_price")
        assert listing.is_opportunity is True
        assert listing.price <= listing.max_rational_bid

    def test_buying_at_the_max_nets_the_floor(self):
        max_price = self._evaluated(390.0, "fixed_price").max_rational_bid
        at_max = self._evaluated(max_price, "fixed_price")
        assert at_max.economics.expected_profit == pytest.approx(SHIPPED_MIN_NET_PROFIT)

    def test_auctions_and_fixed_price_share_one_rule(self):
        auction = self._evaluated(20.0, "auction")
        fixed = self._evaluated(20.0, "fixed_price")
        assert auction.max_rational_bid == pytest.approx(fixed.max_rational_bid)

    def test_the_auction_margin_is_an_extra_floor_only_for_auctions(self):
        auction = self._evaluated(20.0, "auction", auction_required_margin_pct=25.0)
        fixed = self._evaluated(20.0, "fixed_price", auction_required_margin_pct=25.0)
        assert auction.max_rational_bid < fixed.max_rational_bid


class TestDealTierWiring:
    def test_an_asking_comp_opportunity_is_maybe_with_profit_per_hour(self):
        listing = make_listing(EXACT_TITLE, 200.0, listing_type="fixed_price")
        main_module.evaluate_listings(
            [listing], engine_for(spread_observations(400.0)), fake_cfg(min_net_profit=15.0),
            observability.RunStats(),
        )
        assert listing.is_opportunity is True
        assert listing.deal_tier == "Maybe"
        assert listing.profit_per_hour == pytest.approx(listing.economics.expected_profit / (25 / 60))

    def test_the_low_end_net_uses_the_lowest_comp(self):
        listing = make_listing(EXACT_TITLE, 200.0, listing_type="fixed_price")
        rows = spread_observations(400.0)
        rows[0]["price"] = 300.0
        main_module.evaluate_listings([listing], engine_for(rows), fake_cfg(min_net_profit=15.0),
                                      observability.RunStats())
        assert listing.net_profit_low < listing.economics.expected_profit

    def test_no_profit_per_hour_without_minutes(self):
        listing = make_listing(EXACT_TITLE, 200.0, listing_type="fixed_price")
        main_module.evaluate_listings([listing], engine_for(spread_observations(400.0)),
                                      fake_cfg(min_net_profit=15.0, minutes_per_flip=0.0),
                                      observability.RunStats())
        assert listing.is_opportunity is True and listing.profit_per_hour is None

    def test_a_sold_comp_opportunity_is_strong(self):
        listing = make_listing(EXACT_TITLE, 200.0, listing_type="fixed_price")
        sold = spread_observations(400.0, basis=comps.BASIS_SOLD)
        main_module.evaluate_listings(
            [listing], engine_for(sold), fake_cfg(min_net_profit=15.0), observability.RunStats()
        )
        assert listing.deal_tier == "Strong"

    def test_a_rejected_listing_has_no_tier(self):
        listing = make_listing(EXACT_TITLE, 390.0, listing_type="fixed_price")
        main_module.evaluate_listings(
            [listing], engine_for(spread_observations(400.0)), fake_cfg(), observability.RunStats()
        )
        assert listing.is_opportunity is False
        assert listing.deal_tier is None


class TestBaseCardsAtExact:
    def test_a_base_card_is_valued_against_base_copies_of_its_number(self):
        title = "2024 Panini Prizm Caleb Williams #301 RC"
        rows = spread_observations(
            40.0, card_type="raw", grader=None, grade=None, parallel=None, is_base=True,
        )
        listing = make_listing(title, 30.0, listing_type="fixed_price")
        assert listing.card_identity.is_base.value is True
        main_module.evaluate_listings([listing], engine_for(rows), fake_cfg(), observability.RunStats())
        assert listing.comp_level_matched == "exact"
        assert listing.comp_match.flag_eligible is True


class TestFeeModelWiring:
    """The pipeline's choices that the fee model cannot make on its own:
    which haircut (by comp basis), what unknown inbound shipping costs, and
    whether the card ships as a slab."""

    def _evaluated(self, observations, title=EXACT_TITLE, price=200.0, **cfg_overrides):
        listing = make_listing(title, price, listing_type="fixed_price")
        main_module.evaluate_listings(
            [listing], engine_for(observations), fake_cfg(**cfg_overrides), observability.RunStats()
        )
        return listing

    def test_asking_comps_take_the_asking_haircut(self):
        listing = self._evaluated(spread_observations(400.0), resale_haircut_asking_pct=20.0)
        assert listing.economics.expected_sale_price == pytest.approx(320.0)

    def test_sold_comps_take_the_sold_haircut(self):
        sold = spread_observations(400.0, basis=comps.BASIS_SOLD)
        listing = self._evaluated(sold, resale_haircut_asking_pct=20.0)
        assert listing.economics.expected_sale_price == pytest.approx(380.0)

    def test_two_sales_value_the_card_with_the_sold_haircut(self):
        asks = spread_observations(400.0)
        sold = [observation(300.0, "s1", date="2026-08-10", basis=comps.BASIS_SOLD),
                observation(300.0, "s2", date="2026-08-18", basis=comps.BASIS_SOLD)]
        listing = self._evaluated(asks + sold, resale_haircut_asking_pct=20.0, resale_haircut_pct=5.0)
        assert listing.comp_match.stats.basis == comps.BASIS_SOLD
        assert listing.economics.expected_sale_price == pytest.approx(300.0 * 0.95)

    def test_unknown_inbound_shipping_is_assumed_not_zero(self):
        listing = self._evaluated(spread_observations(400.0), assumed_inbound_shipping=4.50)
        assert listing.economics.acquisition_cost == pytest.approx(204.50)
        assert listing.economics.shipping_known is False
        assert any("$4.50 is assumed" in a for a in listing.economics.assumptions)

    def test_unknown_shipping_also_lowers_the_max_bid(self):
        def max_bid(assumed):
            listing = make_listing(EXACT_TITLE, 20.0, listing_type="auction", bid_count=1)
            main_module.evaluate_listings(
                [listing], engine_for(spread_observations(400.0)),
                fake_cfg(assumed_inbound_shipping=assumed), observability.RunStats(),
            )
            return listing.max_rational_bid

        assert max_bid(4.50) == pytest.approx(max_bid(None) - 4.50)

    def test_a_slab_ships_as_a_slab(self):
        listing = self._evaluated(
            spread_observations(15.0), price=5.0,
            outbound_shipping_envelope=0.82, supplies_graded_cost=1.0, supplies_cost=0.5,
        )
        # PSA 10 under $20: still a tracked mailer and slab supplies.
        assert listing.economics.outbound_costs == pytest.approx(5.0 + 1.0)

    def test_a_cheap_raw_card_goes_in_an_envelope(self):
        raw = spread_observations(15.0, card_type="raw", grader=None, grade=None)
        listing = self._evaluated(
            raw, title="2024 Panini Prizm Caleb Williams Silver #301", price=5.0,
            outbound_shipping_envelope=0.82, supplies_graded_cost=1.0, supplies_cost=0.5,
        )
        assert listing.economics.outbound_costs == pytest.approx(0.82 + 0.5)


# ---------------------------------------------------------------------------
# run() end to end
#
# Everything above tests a stage. Nothing tested the function that wires the
# stages together -- the one that reads the inbox, values, dedupes, renders,
# emails and writes state, and the only one a production run actually calls.
# The two project fixtures at the top of this file existed for it and had no
# callers.
# ---------------------------------------------------------------------------


class TestRunEndToEnd:
    def _args(self, dry_run=False):
        return argparse.Namespace(dry_run=dry_run)

    def _wire(self, main_module, monkeypatch, items, sent):
        """Fake the two network edges -- IMAP in, SMTP out -- and nothing
        else. Everything between them is the real pipeline.

        The counters this sets are the ones the real fetch_alert_listings
        sets, on the same condition (title counters only when it returns
        results). That fidelity is not decoration: the fake used to report
        `messages` alone, so `titles_seen` stayed 0, the truncation-stats
        branch of fetch_ebay_alert_active never ran under test, and a
        NameError living in that branch crashed a production scan with the
        whole suite green.
        """
        def fake_fetch(gmail_address, gmail_app_password, sender_contains,
                       lookback_days, mailbox=None, counters=None):
            if counters is not None:
                counters["messages"] = 1 if items else 0
                if items:
                    counters["titles_seen"] = len(items)
                    counters["titles_truncated"] = sum(
                        1 for item in items
                        if main_module.ebay_email_alerts.looks_truncated(item["title"])
                    )
                    counters["titles_recovery_refused"] = 0
            return items

        monkeypatch.setattr(main_module.ebay_email_alerts, "fetch_alert_listings", fake_fetch)
        monkeypatch.setattr(
            main_module.emailer, "send_email",
            lambda subject, body, *rest, html_body=None: sent.append((subject, body, html_body)),
        )

    def test_a_run_reads_the_inbox_and_sends_one_email(self, project_with_alerts_enabled, monkeypatch):
        sent = []
        self._wire(
            project_with_alerts_enabled, monkeypatch,
            [alert_item("https://www.ebay.com/itm/1", 25.0)], sent,
        )
        project_with_alerts_enabled.run(self._args())
        assert len(sent) == 1
        subject, body, _html = sent[0]
        assert subject.startswith("[Card Deals]")
        assert "CARDPRO DAILY" in body

    def test_the_seen_file_and_run_marker_wait_for_a_successful_send(self,
                                                                     project_with_alerts_enabled,
                                                                     monkeypatch, tmp_path):
        # If SMTP fails, nothing may be marked as reported-when-it-was-not:
        # the seen file would suppress these listings from tomorrow's email,
        # and the marker would make the 19:00 backup run skip the day it
        # exists for. Both stay behind the send even though the comp corpus
        # (below) no longer does.
        def explode(*_args, **_kwargs):
            raise RuntimeError("smtp down")

        main_module = project_with_alerts_enabled
        self._wire(main_module, monkeypatch, [alert_item("https://www.ebay.com/itm/1", 25.0)], [])
        monkeypatch.setattr(main_module.emailer, "send_email", explode)
        with pytest.raises(RuntimeError):
            main_module.run(self._args())
        assert not (tmp_path / "data" / "seen_listings.json").exists()
        assert not (tmp_path / "data" / "last_run.json").exists()

    def test_the_corpus_keeps_the_days_observations_when_the_send_fails(self,
                                                                       project_with_alerts_enabled,
                                                                       monkeypatch, tmp_path):
        # Observing a listing is a fact that happened whether or not the
        # email went out, and the alert emails only look back a couple of
        # days -- a run that discarded its observations because SMTP was down
        # would leave a permanent hole in the comp corpus if the recovery run
        # missed that window too.
        def explode(*_args, **_kwargs):
            raise RuntimeError("smtp down")

        main_module = project_with_alerts_enabled
        corpus = tmp_path / "data" / "ebay_alert_price_history.json"

        self._wire(main_module, monkeypatch, [alert_item("https://www.ebay.com/itm/1", 25.0)], [])
        main_module.run(self._args())
        before = {
            o["id"] for rows in json.loads(corpus.read_text()).values() for o in rows
        }

        self._wire(
            main_module, monkeypatch,
            [alert_item("https://www.ebay.com/itm/2", 30.0),
             alert_item("https://www.ebay.com/itm/3", 31.0)],
            [],
        )
        monkeypatch.setattr(main_module.emailer, "send_email", explode)
        with pytest.raises(RuntimeError):
            main_module.run(self._args())

        after = {
            o["id"] for rows in json.loads(corpus.read_text()).values() for o in rows
        }
        assert "https://www.ebay.com/itm/2" in after
        assert "https://www.ebay.com/itm/3" in after
        # And the failed run added to the corpus rather than replacing it.
        assert before < after

    def test_a_dry_run_writes_no_state_and_sends_nothing(self, project_with_alerts_enabled,
                                                         monkeypatch, tmp_path, capsys):
        sent = []
        self._wire(
            project_with_alerts_enabled, monkeypatch,
            [alert_item("https://www.ebay.com/itm/1", 25.0)], sent,
        )
        project_with_alerts_enabled.run(self._args(dry_run=True))
        assert sent == []
        assert "CARDPRO DAILY" in capsys.readouterr().out
        assert not (tmp_path / "data" / "ebay_alert_price_history.json").exists()

    def test_an_empty_inbox_still_sends_a_report(self, project_with_alerts_enabled, monkeypatch):
        # "Never go silent" is a design principle, not a nicety.
        sent = []
        self._wire(project_with_alerts_enabled, monkeypatch, [], sent)
        project_with_alerts_enabled.run(self._args())
        assert len(sent) == 1
        assert "NOTHING CLEARED THE BAR TODAY." in sent[0][1]

    def test_the_corpus_survives_a_second_run(self, project_with_alerts_enabled,
                                              monkeypatch, tmp_path):
        main_module = project_with_alerts_enabled
        corpus = tmp_path / "data" / "ebay_alert_price_history.json"

        self._wire(main_module, monkeypatch, [alert_item("https://www.ebay.com/itm/1", 25.0)], [])
        main_module.run(self._args())
        first = json.loads(corpus.read_text())

        self._wire(main_module, monkeypatch, [alert_item("https://www.ebay.com/itm/2", 30.0)], [])
        main_module.run(self._args())
        second = json.loads(corpus.read_text())

        # The second day must ADD to the corpus, not replace it. This is the
        # shape of the failure that would silently reset months of history.
        first_ids = {o["id"] for rows in first.values() for o in rows}
        second_ids = {o["id"] for rows in second.values() for o in rows}
        assert first_ids and first_ids < second_ids

    def test_a_corrupt_corpus_aborts_the_run_and_is_left_alone(self, project_with_alerts_enabled,
                                                               monkeypatch, tmp_path):
        # The whole point of raising instead of starting fresh: the run stops
        # before save() can replace the unreadable file with one day of data,
        # and before the workflow can commit that over it.
        main_module = project_with_alerts_enabled
        corpus = tmp_path / "data" / "ebay_alert_price_history.json"
        corpus.parent.mkdir(parents=True, exist_ok=True)
        corpus.write_text("{not valid json")

        sent = []
        self._wire(main_module, monkeypatch, [alert_item("https://www.ebay.com/itm/1", 25.0)], sent)
        with pytest.raises(price_history.CorruptCorpus):
            main_module.run(self._args())
        assert corpus.read_text() == "{not valid json"
        assert sent == []

    def test_the_template_canary_reaches_the_email(self, project_with_alerts_enabled, monkeypatch):
        # The highest-value alarm in the system, and it used to exist only in
        # a log file on a runner that gets deleted.
        main_module = project_with_alerts_enabled

        def fake_fetch(*_args, counters=None, **_kwargs):
            if counters is not None:
                counters["messages"] = 14
                counters["template_warning"] = "eBay changed their email template"
            return []

        sent = []
        monkeypatch.setattr(main_module.ebay_email_alerts, "fetch_alert_listings", fake_fetch)
        monkeypatch.setattr(
            main_module.emailer, "send_email",
            lambda subject, body, *rest, html_body=None: sent.append((subject, body, html_body)),
        )
        main_module.run(self._args())
        subject, body, _html = sent[0]
        assert "CHECK THIS" in subject
        assert "eBay changed their email template" in body

    def test_a_run_where_no_price_could_be_read_is_an_alarm(self, project_with_alerts_enabled,
                                                            monkeypatch):
        # Four days of "no opportunities today" that were really "the parser
        # stopped reading eBay's template". Every listing arrived with a full
        # title and price None, which reads exactly like a quiet market and
        # is not one, so it says so in the email.
        sent = []
        self._wire(
            project_with_alerts_enabled, monkeypatch,
            [
                alert_item("https://www.ebay.com/itm/1", None),
                alert_item("https://www.ebay.com/itm/2", None),
            ],
            sent,
        )
        project_with_alerts_enabled.run(self._args())
        subject, body, _html = sent[0]
        assert "CHECK THIS" in subject
        assert "readable price" in body

    def test_an_ordinary_run_does_not_raise_the_price_alarm(self, project_with_alerts_enabled,
                                                            monkeypatch):
        # The alarm has to stay quiet on a normal morning or it is noise. A
        # listing here and there with no price is normal.
        sent = []
        self._wire(
            project_with_alerts_enabled, monkeypatch,
            [alert_item("https://www.ebay.com/itm/%d" % i, 25.0) for i in range(9)]
            + [alert_item("https://www.ebay.com/itm/99", None)],
            sent,
        )
        project_with_alerts_enabled.run(self._args())
        subject, body, _html = sent[0]
        assert "CHECK THIS" not in subject
        assert "readable price" not in body

    def test_the_truncation_stats_branch_runs_and_logs(self, project_with_alerts_enabled,
                                                       monkeypatch, caplog):
        # This branch crashed a live scan with `NameError: name 'logger' is
        # not defined` -- main.py bound `logger` inside four functions and
        # fetch_ebay_alert_active, which logs here, was not one of them. It
        # only ever ran in production because the test fake never set
        # `titles_seen`. Exercising it with a truncated title keeps both the
        # percentages and the log line covered.
        sent = []
        truncated = "2024 Panini Prizm Caleb Williams Silver #301 PSA 1..."
        assert main_module.ebay_email_alerts.looks_truncated(truncated)
        self._wire(
            project_with_alerts_enabled, monkeypatch,
            [
                alert_item("https://www.ebay.com/itm/1", 25.0),
                alert_item("https://www.ebay.com/itm/2", 30.0, title=truncated),
            ],
            sent,
        )
        with caplog.at_level(logging.INFO, logger="main"):
            project_with_alerts_enabled.run(self._args())

        assert len(sent) == 1
        assert any("still truncated" in record.message for record in caplog.records)

    def test_fetch_ebay_alert_active_logs_without_a_local_logger(self, monkeypatch):
        # The narrow version of the test above: call the function directly so
        # the regression is pinned to fetch_ebay_alert_active rather than to
        # a whole run().
        stats = observability.RunStats()
        cfg = SimpleNamespace(
            gmail_address="fake@gmail.com",
            gmail_app_password="fakepassword",
            ebay_alerts_sender_contains="ebay.com",
            ebay_alerts_lookback_days=2,
            ebay_alerts_mailbox=None,
            players=["Caleb Williams"],
            target_cards=[],
        )

        def fake_fetch(*_args, counters=None, **_kwargs):
            if counters is not None:
                counters["messages"] = 1
                counters["titles_seen"] = 4
                counters["titles_truncated"] = 3
                counters["titles_recovery_refused"] = 1
            return []

        monkeypatch.setattr(main_module.ebay_email_alerts, "fetch_alert_listings", fake_fetch)

        assert main_module.fetch_ebay_alert_active(cfg, stats) == []
        assert stats.titles_truncated_pct == 75.0
        assert stats.titles_recovery_refused_pct == 25.0


class TestSearchCoverageEvidence:
    """What gets recorded here is what makes a suggestion stop being
    suggested. Under-recording means being nagged forever to create a search
    you already have."""

    def _observed(self, *titles, **overrides):
        listings = [make_listing(t, 25.0, listing_id=str(i), **overrides)
                    for i, t in enumerate(titles)]
        captured = {}

        def capture(players, observed, *args, **kwargs):
            captured.update(observed)
            return {}

        cfg = fake_cfg()
        with mock.patch.object(main_module.search_terms, "coverage_gaps", capture):
            main_module.build_search_suggestions(cfg, listings)
        return captured

    def test_the_grader_is_recorded_by_name(self):
        # "psa" for every slab marked a BGS card as PSA coverage, so the PSA
        # suggestion went away on evidence that was about a different grader.
        observed = self._observed("2024 Panini Prizm Caleb Williams #301 BGS 9.5")
        assert "bgs" in observed["Caleb Williams"]
        assert "psa" not in observed["Caleb Williams"]

    def test_the_grade_is_recorded_alongside_the_grader(self):
        observed = self._observed("2024 Panini Prizm Caleb Williams #301 PSA 10")
        assert "psa 10" in observed["Caleb Williams"]

    def test_an_unreadable_slab_still_counts_as_graded_evidence(self):
        listings = [make_listing("2024 Panini Prizm Caleb Williams #301", 25.0)]
        listings[0].card_type = "graded"
        listings[0].grader = None
        captured = {}
        with mock.patch.object(
            main_module.search_terms, "coverage_gaps",
            lambda players, observed, *a, **k: captured.update(observed) or {},
        ):
            main_module.build_search_suggestions(fake_cfg(), listings)
        assert captured["Caleb Williams"]

    def test_the_set_is_recorded_so_product_queries_can_be_satisfied(self):
        # The generator suggests product queries; without this they could
        # never be marked covered by anything.
        observed = self._observed("2024 Panini Prizm Caleb Williams Silver Prizm #301")
        assert "prizm" in observed["Caleb Williams"]

    def test_numbering_and_autographs_are_recorded(self):
        observed = self._observed("2024 Topps Chrome Caleb Williams Auto #150 /99")
        assert "auto" in observed["Caleb Williams"]
        assert "/99" in observed["Caleb Williams"]

    def test_a_plain_raw_card_records_nothing_to_go_on(self):
        observed = self._observed("2024 Topps Caleb Williams")
        assert not observed.get("Caleb Williams")


class TestTheRunMarker:
    """A dropped scheduled run was the one failure nothing here could see --
    no email, no failure notification, no red job. The backup run reads this
    marker to decide whether the day still needs scanning."""

    def _args(self, **overrides):
        fields = dict(dry_run=False, skip_if_ran_today=False)
        fields.update(overrides)
        return argparse.Namespace(**fields)

    def _wire(self, main_module, monkeypatch, sent):
        monkeypatch.setattr(
            main_module.ebay_email_alerts, "fetch_alert_listings",
            lambda *a, counters=None, **k: (
                counters.update(messages=1) if counters is not None else None,
                [alert_item("https://www.ebay.com/itm/1", 25.0)],
            )[1],
        )
        monkeypatch.setattr(
            main_module.emailer, "send_email",
            lambda subject, body, *rest, html_body=None: sent.append((subject, body, html_body)),
        )

    def test_a_completed_run_records_the_day(self, project_with_alerts_enabled,
                                             monkeypatch, tmp_path):
        main_module = project_with_alerts_enabled
        self._wire(main_module, monkeypatch, [])
        main_module.run(self._args())
        marker = json.loads((tmp_path / "data" / "last_run.json").read_text())
        assert marker["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def test_a_dry_run_records_nothing(self, project_with_alerts_enabled, monkeypatch, tmp_path):
        main_module = project_with_alerts_enabled
        self._wire(main_module, monkeypatch, [])
        main_module.run(self._args(dry_run=True))
        assert not (tmp_path / "data" / "last_run.json").exists()

    def test_a_failed_send_records_nothing(self, project_with_alerts_enabled,
                                           monkeypatch, tmp_path):
        # The marker's one job is to answer "did today's scan complete".
        # Writing it before the send would let a failed send record a run
        # that never reached anybody -- and the backup would then skip.
        main_module = project_with_alerts_enabled
        self._wire(main_module, monkeypatch, [])
        monkeypatch.setattr(
            main_module.emailer, "send_email",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")),
        )
        with pytest.raises(RuntimeError):
            main_module.run(self._args())
        assert not (tmp_path / "data" / "last_run.json").exists()

    def test_the_gap_reaches_the_health_footer(self, project_with_alerts_enabled,
                                               monkeypatch, tmp_path):
        main_module = project_with_alerts_enabled
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        main_module.run_marker.save(tmp_path / "data" / "last_run.json", "2020-01-01", 5)
        sent = []
        self._wire(main_module, monkeypatch, sent)
        main_module.run(self._args())
        assert "since the last completed scan" in sent[0][1]


class TestAutographsAreNotCompedAgainstBaseCards:
    """End-to-end through evaluate_listings/record_observations, because the
    unit-level fix in comps is only worth anything if the pipeline hands it
    the fields.

    The case is the live corpus's, not an invented one: a Colson Montgomery
    2026 Topps Chrome Logofractor bucket held base copies at $4.00 and $4.30
    alongside on-card autographs at $109.67-$155, and reported the base
    cards as 96% under market.
    """

    AUTO_TITLE = "2024 Panini Prizm Caleb Williams Silver #301 On Card Auto"
    BASE_TITLE = "2024 Panini Prizm Caleb Williams Silver #301"

    def _auto_comps(self):
        return spread_observations(
            120.0, card_type="raw", grader=None, grade=None, is_autograph=True
        )

    def test_a_base_card_is_not_valued_against_autographed_copies(self):
        listing = make_listing(self.BASE_TITLE, 4.00, listing_id="B1", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._auto_comps()), fake_cfg(), stats)
        assert listing.is_opportunity is False
        # It falls through to same_set, which does pool autographs with base
        # cards -- and is context-only precisely because it does.
        assert listing.rejection_reason == reasons.Reason.CONTEXT_ONLY_LEVEL
        assert listing.comp_match.level == "same_set"

    def test_an_autographed_card_still_finds_its_own_market(self):
        listing = make_listing(self.AUTO_TITLE, 40.00, listing_id="A1", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(self._auto_comps()), fake_cfg(), stats)
        assert listing.is_opportunity is True
        assert listing.market_value == 120.0

    def test_a_numbered_parallel_is_not_valued_against_unnumbered_copies(self):
        unnumbered = spread_observations(20.0, card_type="raw", grader=None, grade=None)
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 /25", 8.00,
            listing_id="N1", listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(unnumbered), fake_cfg(), stats)
        assert listing.is_opportunity is False

    def test_an_insert_is_not_valued_against_the_base_card(self):
        """`same_card` omits the card number on purpose, so an insert of the
        same player, year, set and parallel lands in the base card's bucket."""
        base = spread_observations(
            55.0, card_type="raw", grader=None, grade=None, card_number="16"
        )
        insert = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #89CB-19", 18.40,
            listing_id="I1", listing_type="fixed_price",
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([insert], engine_for(base), fake_cfg(), stats)
        assert insert.is_opportunity is False
        assert insert.rejection_reason == reasons.Reason.MIXED_CARD_NUMBERS

    def test_recorded_observations_carry_the_variant_fields(self):
        """A row recorded without them falls back to re-reading its title,
        which works but costs a full parse of the corpus on every run."""
        listing = make_listing(self.AUTO_TITLE, 120.0, listing_id="A2", listing_type="fixed_price")
        history = {}
        main_module.record_observations([listing], history, "2026-08-22")
        row = history["Caleb Williams|raw"][0]
        assert row["is_autograph"] is True
        assert row["relic"] is None
        assert row["title_truncated"] is False
        assert row["listing_type"] == "fixed_price"

    def test_a_truncated_row_is_recorded_but_barred_from_flag_levels(self):
        """Recording it keeps the price-tier and same_set context; barring it
        keeps a hidden "Auto /150" out of the base card's median."""
        listing = make_listing(
            "2024 Panini Prizm Caleb Williams Silver #301 On Card…", 120.0,
            listing_id="T9", listing_type="fixed_price",
        )
        main_module.mark_truncated_titles([listing])
        history = {}
        main_module.record_observations([listing], history, "2026-08-22")
        row = history["Caleb Williams|raw"][0]
        assert row["title_truncated"] is True
        assert comps.variant_of_observation(row) is None


class TestBuyingFormatAlarm:
    """eBay's template never labels Buy It Now, so "unknown" is normal; the
    thing that would mean a changed template is the bid lines vanishing."""

    def _stats(self, auctions, fixed=0, unknown=0):
        return observability.RunStats(auctions=auctions, fixed_price=fixed, listing_type_unknown=unknown)

    def test_a_normal_run_is_mostly_unknown_and_raises_nothing(self):
        """The old 40%-unknown alarm fired on this every day."""
        assert main_module.buying_format_warning(self._stats(auctions=20, unknown=80)) is None

    def test_no_auctions_in_a_large_run_is_the_alarm(self):
        warning = main_module.buying_format_warning(self._stats(auctions=0, unknown=80))
        assert "CURRENT BID" in warning
        assert "80 listings" in warning

    def test_a_small_run_with_no_auctions_is_just_a_quiet_day(self):
        assert main_module.buying_format_warning(self._stats(auctions=0, unknown=10)) is None


class TestPlayerFromSavedSearch:
    """eBay cuts a search match's title to ~30 characters, which dropped the
    player's surname from 64 of 79 matches in six real alerts -- and every
    one of those was discarded as NO_PLAYER_MATCH."""

    PLAYERS = ["Pete Crow-Armstrong", "Caleb Williams", "Caleb Wilson"]
    CFG = SimpleNamespace(player_tiers={})

    def _build(self, title, search_query):
        return main_module._build_listing(
            self.CFG, listing_id="https://www.ebay.com/itm/1", source="ebay-alert", title=title,
            price=480.0, url="https://www.ebay.com/itm/1", players=self.PLAYERS,
            search_query=search_query,
        )

    def test_a_cut_off_name_is_recovered_from_the_search(self):
        listing = self._build("2024 Topps Chrome Pete Crow-A…", "pete crow-armstrong auto -lot")
        assert listing.player == "Pete Crow-Armstrong"
        assert listing.player_from_search is True

    def test_the_title_still_wins_when_it_names_the_player(self):
        listing = self._build("2024 Prizm Caleb Williams Silver #301", "caleb williams psa 10")
        assert listing.player == "Caleb Williams"
        assert listing.player_from_search is False

    def test_a_search_naming_two_players_proves_nothing(self):
        assert self._build("2025 Bowman Chrome Dual Au…", "caleb williams caleb wilson dual") is None

    def test_no_search_no_player_is_still_rejected(self):
        """A recommendation has no search behind it."""
        assert self._build("2024 Topps Chrome Pete Crow-A…", None) is None

    def test_it_is_shown_but_never_recorded_as_a_comp(self):
        """A "psa" search's match whose title lost "PSA" would otherwise land
        in the RAW bucket at a slab's price."""
        listing = self._build("2024 Topps Chrome Pete Crow-A…", "pete crow-armstrong psa")
        listing.listing_type = "fixed_price"
        history = {}
        assert main_module.record_observations([listing], history, "2026-09-23") == 0
        assert history == {}


def test_capped_saved_searches_are_named_in_the_footer(monkeypatch):
    stats = observability.RunStats()
    cfg = SimpleNamespace(
        gmail_address="a", gmail_app_password="b", ebay_alerts_sender_contains="ebay.com",
        ebay_alerts_lookback_days=2, ebay_alerts_mailbox=None, players=["Caleb Williams"],
        target_cards=[],
    )

    def fake_fetch(*_args, counters=None, **_kwargs):
        counters["messages"] = 2
        counters["capped_searches"] = {
            "pete crow-armstrong -lot -reprint": (1744, 16),
            "caleb williams psa 10": (45, 12),
        }
        return []

    monkeypatch.setattr(main_module.ebay_email_alerts, "fetch_alert_listings", fake_fetch)
    main_module.fetch_ebay_alert_active(cfg, stats)
    warning = next(w for w in stats.warnings if "saved search" in w)
    # Worst first, exclusions dropped, numbers readable.
    assert warning.index('"pete crow-armstrong" (1,744 new, 16 shown)') < warning.index(
        '"caleb williams psa 10" (45 new, 12 shown)'
    )
