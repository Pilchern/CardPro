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
from datetime import datetime, timezone
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


def _make_project(tmp_path, monkeypatch, *, alerts_enabled: bool):
    settings = json.loads(json.dumps(SETTINGS))
    settings["ebay_alerts"]["enabled"] = alerts_enabled

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "watchlist.json").write_text(json.dumps({"players": ["Caleb Williams"]}))
    (tmp_path / "config" / "settings.json").write_text(json.dumps(settings))

    if alerts_enabled:
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


def fake_active(query, token, category_id, marketplace_id, limit=50):
    if "Caleb Williams" not in query:
        return []
    return [
        {
            "itemId": "e1",
            "title": IDENTIFIED_TITLE,
            "price": {"value": "200"},
            "itemWebUrl": "http://ebay/e1",
            "buyingOptions": ["FIXED_PRICE"],
        },
        {
            "itemId": "e2",
            "title": "2024 Panini Prizm Caleb Williams Silver #301 PSA 10 REPRINT",
            "price": {"value": "10"},
            "itemWebUrl": "http://ebay/e2",
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
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 20.0, listing_id="A1", listing_type="auction", bid_count=3)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats)

        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.AUCTION_CURRENT_BID_NOT_A_PRICE
        # ...but it IS valued, and it gets the number that makes it actionable
        assert listing.market_value == 400.0
        assert listing.max_rational_bid > 0

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

    def test_non_opportunities_pass_through_untouched(self):
        # Auctions and needs-review items must not be deduped away: an
        # auction that's still live is still worth seeing.
        stats = observability.RunStats()
        auction = make_listing(EXACT_TITLE, 50.0, listing_id="A1", listing_type="auction")
        kept = main_module.apply_dedupe([auction], {}, "2026-08-22", stats)
        assert kept == [auction]
        assert stats.duplicates_suppressed == 0


class TestListingsAsObservations:
    """The eBay API path has no persisted corpus, so today's active listings
    become asking-basis observations. Same exclusions as the alerts path."""

    def test_fixed_price_listings_become_asking_basis_observations(self):
        listing = make_listing(EXACT_TITLE, 240.0, listing_id="F1", listing_type="fixed_price")
        obs = main_module.listings_as_observations([listing], "2026-08-22")
        assert len(obs) == 1
        assert obs[0]["basis"] == comps.BASIS_ASKING
        assert obs[0]["id"] == "F1"
        assert obs[0]["grade"] == "10"

    def test_auctions_are_excluded(self):
        auction = make_listing(EXACT_TITLE, 40.0, listing_id="A1", listing_type="auction", bid_count=4)
        assert main_module.listings_as_observations([auction], "2026-08-22") == []

    def test_blocked_listings_are_excluded(self):
        reprint = make_listing("2024 Panini Prizm Caleb Williams Silver #301 REPRINT", 12.0)
        assert main_module.listings_as_observations([reprint], "2026-08-22") == []

    def test_sold_observations_take_priority_but_asking_still_counts(self):
        # A bucket mixing both is reported as asking-basis, which caps its
        # confidence -- honest, not a loss.
        sold = [dict(observation(400.0, "s%d" % i), basis=comps.BASIS_SOLD) for i in range(3)]
        asking = main_module.listings_as_observations(
            [make_listing(EXACT_TITLE, 500.0, listing_id="a1", listing_type="fixed_price")], "2026-08-22"
        )
        engine = engine_for(sold + asking)
        match = engine.lookup(
            player="Caleb Williams", card_type="graded", price=200.0, grader="PSA", grade="10",
            year=2024, set_name="Prizm", parallel="Silver", card_number="301",
        )
        assert match.stats.basis == comps.BASIS_ASKING
        assert match.confidence != "high"


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

        assert listing.is_opportunity is True
        assert listing.comp_match.level == "price_tier"
        warning = " ".join(stats.warnings)
        assert "require_flag_eligible_comp is FALSE" in warning
        assert "cannot be evidence about price" in warning

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
        else. Everything between them is the real pipeline."""
        def fake_fetch(gmail_address, gmail_app_password, sender_contains,
                       lookback_days, mailbox=None, counters=None):
            if counters is not None:
                counters["messages"] = 1 if items else 0
            return items

        monkeypatch.setattr(main_module.ebay_email_alerts, "fetch_alert_listings", fake_fetch)
        monkeypatch.setattr(
            main_module.emailer, "send_email",
            lambda subject, body, *rest: sent.append((subject, body)),
        )

    def test_a_run_reads_the_inbox_and_sends_one_email(self, project_with_alerts_enabled, monkeypatch):
        sent = []
        self._wire(
            project_with_alerts_enabled, monkeypatch,
            [alert_item("https://www.ebay.com/itm/1", 25.0)], sent,
        )
        project_with_alerts_enabled.run(self._args())
        assert len(sent) == 1
        subject, body = sent[0]
        assert subject.startswith("[Card Deals]")
        assert "CARDPRO DAILY" in body

    def test_state_is_written_after_the_email_not_before(self, project_with_alerts_enabled,
                                                         monkeypatch, tmp_path):
        # If SMTP fails, nothing may be marked as reported-when-it-was-not.
        def explode(*_args, **_kwargs):
            raise RuntimeError("smtp down")

        main_module = project_with_alerts_enabled
        self._wire(main_module, monkeypatch, [alert_item("https://www.ebay.com/itm/1", 25.0)], [])
        monkeypatch.setattr(main_module.emailer, "send_email", explode)
        with pytest.raises(RuntimeError):
            main_module.run(self._args())
        assert not (tmp_path / "data" / "seen_listings.json").exists()

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
            lambda subject, body, *rest: sent.append((subject, body)),
        )
        main_module.run(self._args())
        subject, body = sent[0]
        assert "CHECK THIS" in subject
        assert "eBay changed their email template" in body


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
