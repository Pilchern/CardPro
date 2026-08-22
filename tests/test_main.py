"""End-to-end orchestration tests with the eBay/email network layer mocked
out, plus unit tests for the CardPro 2.0 evaluation pipeline.

The unit tests below are regression tests for defects
docs/CARDPRO_2_AUDIT.md measured in live production data -- every one of
them was real, not hypothetical.

Craigslist link generation is a pure function (no network), so it isn't
mocked.
"""
from __future__ import annotations

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


EXACT_TITLE = "2024 Panini Prizm Caleb Williams Silver #301 PSA 10"


# --- Failure mode #5: truncated grades were comped before being repaired ---


class TestTruncatedTitleRepair:
    def test_repair_happens_and_rewrites_identity(self):
        listing = make_listing("2024 Panini Prizm Caleb Williams Silver #301 PSA 1…", 250.0)
        assert listing.grade == "1"  # the whole problem: a PSA 10 parsed as PSA 1

        stats = observability.RunStats()
        with mock.patch.object(
            main_module.ebay_email_alerts, "fetch_full_title", return_value=EXACT_TITLE
        ):
            main_module.repair_truncated_titles([listing], stats)

        assert listing.grade == "10"
        assert listing.title_truncated is False
        assert listing.card_identity.card_number.value == "301"

    def test_failed_repair_marks_the_grade_uncertain_rather_than_asserting_it(self):
        listing = make_listing("2024 Panini Prizm Caleb Williams Silver #301 PSA 1…", 250.0)
        stats = observability.RunStats()
        with mock.patch.object(main_module.ebay_email_alerts, "fetch_full_title", return_value=None):
            main_module.repair_truncated_titles([listing], stats)
        assert listing.title_truncated is True

    def test_untruncated_titles_are_never_fetched(self):
        listing = make_listing(EXACT_TITLE, 250.0)
        stats = observability.RunStats()
        with mock.patch.object(main_module.ebay_email_alerts, "fetch_full_title") as fetch:
            main_module.repair_truncated_titles([listing], stats)
        fetch.assert_not_called()

    def test_repairs_are_capped_and_warn(self):
        listings = [
            make_listing("2024 Prizm Caleb Williams PSA 1…", 10.0, listing_id="L%d" % i) for i in range(6)
        ]
        stats = observability.RunStats()
        with mock.patch.object(
            main_module.ebay_email_alerts, "fetch_full_title", return_value=EXACT_TITLE
        ) as fetch:
            main_module.repair_truncated_titles(listings, stats, limit=2)
        assert fetch.call_count == 2
        assert stats.warnings
        # the ones we didn't get to are marked uncertain, not silently trusted
        assert all(listing.title_truncated for listing in listings[2:])


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
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)
        assert listing.comp_match is None
        assert listing.rejection_reason == reasons.Reason.NO_COMP_AT_ANY_LEVEL

    def test_exclusion_does_not_break_a_healthy_bucket(self):
        observations = [observation(400.0, "o%d" % i) for i in range(6)] + [observation(100.0, "L1")]
        listing = make_listing(EXACT_TITLE, 100.0, listing_id="L1", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)
        assert listing.market_value == 400.0  # its own $100 didn't drag the median down
        assert listing.is_opportunity is True

    def test_reprint_is_blocked_with_its_own_reason(self):
        listing = make_listing("1986 Fleer Caleb Williams #57 REPRINT", 20.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats, TODAY)
        assert listing.is_opportunity is False
        assert listing.rejection_reason == reasons.Reason.REPRINT
        assert stats.blocked_by_negative_signal == 1

    def test_lot_is_blocked(self):
        listing = make_listing("Lot of 5 Caleb Williams rookie cards", 40.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats, TODAY)
        assert listing.rejection_reason == reasons.Reason.LOT

    def test_multi_player_card_is_not_valued_against_one_player(self):
        listing = make_listing("2024 Prizm Dual Auto Caleb Williams Kyle Teel #5", 300.0)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for([]), fake_cfg(), stats, TODAY)
        assert listing.rejection_reason == reasons.Reason.MULTI_PLAYER_CARD

    def test_auction_is_never_an_opportunity_however_cheap(self):
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 20.0, listing_id="A1", listing_type="auction", bid_count=3)
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)

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
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)

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
        main_module.evaluate_listings([psa9], engine_for(psa10_comps), fake_cfg(), stats, TODAY)
        assert psa9.is_opportunity is False
        # No level -- not even a context-only one -- will hand a PSA 9 the
        # PSA 10 median. Nothing to say beats saying the wrong thing.
        assert psa9.market_value is None
        assert psa9.rejection_reason == reasons.Reason.NO_COMP_AT_ANY_LEVEL

    def test_below_threshold_records_the_specific_reason(self):
        observations = [observation(300.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 280.0, listing_id="L2", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)
        assert listing.rejection_reason == reasons.Reason.BELOW_DISCOUNT_THRESHOLD

    def test_below_min_savings_records_the_specific_reason(self):
        observations = [observation(20.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 12.0, listing_id="L3", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)
        assert listing.pct_under_market >= 30
        assert listing.rejection_reason == reasons.Reason.BELOW_MIN_SAVINGS

    def test_shipping_is_included_in_the_discount_maths(self):
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(
            EXACT_TITLE, 260.0, listing_id="L4", listing_type="fixed_price", shipping_price=40.0
        )
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)
        assert listing.dollar_savings == pytest.approx(100.0)  # 400 - (260 + 40), not 400 - 260

    def test_economics_are_attached_with_visible_assumptions(self):
        observations = [observation(400.0, "o%d" % i) for i in range(6)]
        listing = make_listing(EXACT_TITLE, 200.0, listing_id="L5", listing_type="fixed_price")
        stats = observability.RunStats()
        main_module.evaluate_listings([listing], engine_for(observations), fake_cfg(), stats, TODAY)
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
                                      fake_cfg(), stats, TODAY)
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
        main_module.evaluate_listings([listing], engine_for([]), cfg, stats, TODAY)

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
