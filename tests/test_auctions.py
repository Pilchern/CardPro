"""B4: auctions Nick can act on.

The Browse auction pass is windowed to auctions ending 3-27 h out, the
AUCTIONS section leads with the auctions that carry a MAX and caps the rest,
each auction card prints its close as a Central clock time, and the subject
counts only auctions with room under their MAX. All fixture-based; no eBay
call and no production corpus.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import ebay_client, observability, report, report_html
from src import main as main_module
from tests.test_config import _load_with
from tests.test_main import EXACT_TITLE
from tests.test_main import make_listing as pipeline_listing
from tests.test_report import flat, make_listing, make_target_hit

RUN_DATE = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 12, 47, tzinfo=timezone.utc)


def _fake_get(calls):
    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"itemSummaries": []}

    def get(url, headers=None, params=None, timeout=None):
        calls.append(dict(params))
        return Resp()

    return get


def auction(id, *, price=40.0, max_bid=None, time_left="10h 00m", ends_at=None, **overrides):
    """An auction with no comp unless a MAX is given (the usual case)."""
    defaults = dict(
        id=id,
        listing_type="auction",
        is_opportunity=False,
        price=price,
        shipping_price=4.0,
        bid_count=2,
        time_left_text=time_left,
        ends_at=ends_at,
        max_rational_bid=max_bid,
        pct_under_market=1.0,
    )
    if max_bid is None:
        defaults.update(comp_match=None, market_value=None, pct_under_market=None, dollar_savings=None)
    defaults.update(overrides)
    return make_listing(**defaults)


# ---------------------------------------------------------------------------
# 1. the Browse window
# ---------------------------------------------------------------------------


class TestEndDateFilter:
    def test_matches_ebays_documented_form(self):
        start = datetime(2026, 10, 20, 15, 47, 12, 345000, tzinfo=timezone.utc)
        end = datetime(2026, 10, 21, 15, 47, 12, tzinfo=timezone.utc)
        assert ebay_client.item_end_date_filter(start, end) == (
            "itemEndDate:[2026-10-20T15:47:12Z..2026-10-21T15:47:12Z]"
        )
        # Start only: no dots. End only: the dots stay. Per eBay's reference.
        assert ebay_client.item_end_date_filter(start, None) == "itemEndDate:[2026-10-20T15:47:12Z]"
        assert ebay_client.item_end_date_filter(None, end) == "itemEndDate:[..2026-10-21T15:47:12Z]"
        assert ebay_client.item_end_date_filter(None, None) is None

    def test_a_non_utc_time_is_converted_not_relabelled(self):
        central = timezone(timedelta(hours=-5))
        start = datetime(2026, 10, 20, 10, 0, tzinfo=central)
        assert ebay_client.item_end_date_filter(start, None) == "itemEndDate:[2026-10-20T15:00:00Z]"

    def test_the_search_appends_the_window_to_the_filter(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ebay_client.requests, "get", _fake_get(calls))
        ebay_client.search_active_listings(
            "x", "t", "261328", "EBAY_US", sort="endingSoonest", buying_options=("AUCTION",),
            ending_between=(NOW + timedelta(hours=3), NOW + timedelta(hours=27)),
        )
        assert calls[0]["filter"] == (
            "buyingOptions:{AUCTION},itemEndDate:[2026-10-20T15:47:00Z..2026-10-21T15:47:00Z]"
        )
        assert calls[0]["sort"] == "endingSoonest"

    def test_no_window_means_no_end_date_filter(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ebay_client.requests, "get", _fake_get(calls))
        ebay_client.search_active_listings("x", "t", "261328", "EBAY_US")
        assert "itemEndDate" not in calls[0]["filter"]


class TestAuctionWindow:
    def test_bounds_come_from_config(self):
        cfg = SimpleNamespace(auction_min_hours_left=3.0, auction_max_hours_left=27.0)
        assert main_module.auction_window(cfg, NOW) == (
            NOW + timedelta(hours=3), NOW + timedelta(hours=27))
        open_top = SimpleNamespace(auction_min_hours_left=3.0, auction_max_hours_left=None)
        assert main_module.auction_window(open_top, NOW) == (NOW + timedelta(hours=3), None)
        off = SimpleNamespace(auction_min_hours_left=None, auction_max_hours_left=None)
        assert main_module.auction_window(off, NOW) is None

    def test_only_the_auction_pass_is_windowed(self, tmp_path, monkeypatch):
        # Not test_main's `project` fixture: it reloads src.main under a tmp
        # ROOT_DIR, which leaks into test_flips when this file runs first.
        cfg = _load_with(tmp_path, monkeypatch)
        cfg.ebay_auction_pages_per_player = 1
        searches = []
        monkeypatch.setattr(main_module.ebay_client, "search_active_listings",
                            lambda query, **kwargs: searches.append(kwargs) or [])
        before = datetime.now(timezone.utc)
        main_module.fetch_ebay_active(cfg, ["Caleb Williams"], "t", observability.RunStats())
        by_sort = {search["sort"]: search for search in searches}
        assert "ending_between" not in by_sort["newlyListed"]
        start, end = by_sort["endingSoonest"]["ending_between"]
        assert (end - start) == timedelta(hours=24)
        assert timedelta(hours=3) - timedelta(seconds=5) <= start - before <= timedelta(hours=3, minutes=1)

    def test_browse_end_date_reaches_the_listing(self, tmp_path, monkeypatch):
        cfg = _load_with(tmp_path, monkeypatch)
        item = {"itemId": "v1|123456789012|0", "legacyItemId": "123456789012",
                "itemWebUrl": "https://www.ebay.com/itm/123456789012", "title": EXACT_TITLE,
                "currentBidPrice": {"value": "20.00"}, "buyingOptions": ["AUCTION"],
                "itemEndDate": "2026-10-21T00:14:00.000Z"}
        monkeypatch.setattr(main_module.ebay_client, "search_active_listings", lambda query, **kw: [item])
        [listing] = main_module.fetch_ebay_active(cfg, ["Caleb Williams"], "t", observability.RunStats())
        assert listing.is_auction
        assert listing.ends_at == "2026-10-21T00:14:00.000Z"


class TestConfig:
    def test_defaults_when_the_keys_are_missing(self, tmp_path, monkeypatch):
        cfg = _load_with(tmp_path, monkeypatch)
        assert (cfg.auction_min_hours_left, cfg.auction_max_hours_left, cfg.auction_no_max_cap) == (3.0, 27.0, 3)

    def test_keys_load_and_null_opens_a_bound(self, tmp_path, monkeypatch):
        cfg = _load_with(tmp_path, monkeypatch, settings_extra={
            "auctions": {"min_hours_left": 1.5, "max_hours_left": None, "no_max_cap": 0}})
        assert (cfg.auction_min_hours_left, cfg.auction_max_hours_left, cfg.auction_no_max_cap) == (1.5, None, 0)

    def test_shipped_settings(self):
        import json
        from pathlib import Path
        shipped = json.loads((Path(__file__).resolve().parent.parent / "config" / "settings.json").read_text())
        block = shipped["auctions"]
        assert (block["min_hours_left"], block["max_hours_left"], block["no_max_cap"]) == (3, 27, 3)


class TestAuctionsAreNeverDeduped:
    def test_an_auction_is_never_recorded_as_a_deal(self):
        # apply_dedupe records a deal (record_flagged) only for is_opportunity,
        # and evaluate_listings diverts every auction before that flag is set,
        # so trimming AUCTIONS cannot leave the seen file claiming a card
        # was sent.
        listing = pipeline_listing(EXACT_TITLE, 20.0, listing_id="A", listing_type="auction")
        seen = {}
        kept = main_module.apply_dedupe([listing], seen, "2026-10-20", observability.RunStats())
        assert kept == [listing]
        assert "A" not in seen


# ---------------------------------------------------------------------------
# 1b. alert-email auctions already closing
# ---------------------------------------------------------------------------


class TestEndingTooSoon:
    def test_an_auction_under_the_minimum_is_left_out_and_counted(self):
        soon = auction("soon", time_left="2h 10m", source="ebay-alert")
        later = auction("later", time_left="5h 00m", source="ebay-alert")
        model = report.build_model([soon, later], 30, RUN_DATE, auction_min_hours_left=3)
        body = report.render_text(model)
        assert "later" in model.shown_ids and "soon" not in model.shown_ids
        assert "1 auction was left out: under 3h left" in flat(body)
        assert "auctions.min_hours_left" in flat(body)

    def test_cheap_auctions_too(self):
        cheap = auction("cheap", price=2.0, time_left="45m", shipping_price=0.0)
        model = report.build_model([cheap], 30, RUN_DATE, auction_min_hours_left=3)
        assert model.shown_ids == frozenset()

    def test_unknown_countdown_and_target_hits_are_kept(self):
        unknown = auction("unknown", time_left=None)
        target = auction("target", time_left="30m", target_hit=make_target_hit())
        model = report.build_model([unknown, target], 30, RUN_DATE, auction_min_hours_left=3)
        assert {"unknown", "target"} <= model.shown_ids
        assert "under 3h left" not in flat(report.render_text(model))

    def test_off_by_default(self):
        soon = auction("soon", time_left="10m")
        assert "soon" in report.build_model([soon], 30, RUN_DATE).shown_ids


# ---------------------------------------------------------------------------
# 2. ranking and the no-MAX cap
# ---------------------------------------------------------------------------


class TestRanking:
    def test_max_bearing_first_by_bid_over_max_then_the_rest_by_time(self):
        tight = auction("tight", price=45.0, max_bid=50.0, time_left="4h 00m")  # 0.90
        roomy = auction("roomy", price=20.0, max_bid=50.0, time_left="20h 00m")  # 0.40
        no_max_late = auction("nomax-late", time_left="20h 00m")
        no_max_soon = auction("nomax-soon", time_left="3h 30m")
        walk_away = auction("walk-away", max_bid=0.0, time_left="5h 00m")  # MAX "none"
        sections = report.classify_sections(
            [no_max_late, tight, walk_away, no_max_soon, roomy], threshold_pct=30)
        assert [d.id for d in sections[report.SECTION_AUCTIONS]] == [
            "roomy", "tight", "nomax-soon", "walk-away", "nomax-late"]

    def test_no_max_auctions_are_capped_and_the_overflow_counted(self):
        with_max = [auction("max{}".format(i), price=20.0, max_bid=50.0 + i) for i in range(4)]
        without = [auction("plain{}".format(i), time_left="{}h 00m".format(4 + i)) for i in range(5)]
        model = report.build_model(with_max + without, 30, RUN_DATE, auction_no_max_cap=3)
        shown = model.shown_ids
        assert {"max0", "max1", "max2", "max3"} <= shown  # every MAX auction, cap or not
        assert {"plain0", "plain1", "plain2"} <= shown  # the three soonest
        assert not ({"plain3", "plain4"} & shown)
        text = flat(report.render_text(model))
        assert "2 auctions without a MAX left out of AUCTIONS ENDING SOON" in text
        assert "auctions.no_max_cap" in text
        # Counted once: not also in the "fitted no section" bucket.
        assert "fitted no section" not in text

    def test_the_cap_is_off_by_default(self):
        without = [auction("plain{}".format(i)) for i in range(6)]
        assert len(report.build_model(without, 30, RUN_DATE).shown_ids) == 6


# ---------------------------------------------------------------------------
# 3. the absolute end time
# ---------------------------------------------------------------------------


class TestEndsText:
    @pytest.mark.parametrize("stamp, expected", [
        ("2026-10-21T00:14:00.000Z", "Tue 7:14 PM CT"),  # CDT, UTC-5
        ("2026-12-01T01:05:00Z", "Mon 7:05 PM CT"),  # CST, UTC-6
        ("2026-11-01T06:59:00Z", "Sun 1:59 AM CT"),  # last minute of CDT
        ("2026-11-01T07:00:00Z", "Sun 1:00 AM CT"),  # first minute of CST
        ("2026-03-08T07:59:00Z", "Sun 1:59 AM CT"),  # last minute of CST
        ("2026-03-08T08:00:00Z", "Sun 3:00 AM CT"),  # first minute of CDT
        ("2026-10-20T05:00:00Z", "Tue 12:00 AM CT"),  # midnight
        ("2026-10-20T17:30:00Z", "Tue 12:30 PM CT"),  # noon
    ])
    def test_central_time_across_dst(self, stamp, expected):
        assert report.ends_text(stamp) == expected

    @pytest.mark.parametrize("stamp", [None, "", "not a date"])
    def test_unknown_is_none(self, stamp):
        assert report.ends_text(stamp) is None

    def test_without_a_tz_database_it_prints_utc_and_says_so(self, monkeypatch):
        import zoneinfo

        def missing(_key):
            raise zoneinfo.ZoneInfoNotFoundError("no tz database")

        monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
        assert report.ends_text("2026-10-21T00:14:00.000Z") == "Wed 12:14 AM UTC"


class TestAuctionCard:
    def test_the_card_prints_the_close_beside_the_max(self):
        card = auction("a", price=20.0, max_bid=50.0, ends_at="2026-10-21T00:14:00.000Z")
        text = flat(report.build_report([card], 30, RUN_DATE)[1])
        assert "Max bid $50.00, ends Tue 7:14 PM CT -- the most you can bid" in text
        assert "2 bids, ends Tue 7:14 PM CT" in text
        assert "remaining" not in text

    def test_no_end_date_keeps_the_countdown(self):
        card = auction("a", price=20.0, max_bid=50.0, time_left="5h 10m")
        text = flat(report.build_report([card], 30, RUN_DATE)[1])
        assert "2 bids, 5h 10m remaining" in text
        assert "Max bid $50.00 -- the most you can bid" in text

    def test_html_says_the_same(self):
        card = auction("a", price=20.0, max_bid=50.0, ends_at="2026-10-21T00:14:00.000Z")
        html = report_html.render(report.build_model([card], 30, RUN_DATE))
        assert "ends Tue 7:14 PM CT" in html


# ---------------------------------------------------------------------------
# 4. the subject
# ---------------------------------------------------------------------------


class TestSubject:
    def test_counts_only_auctions_with_room_under_max(self):
        deals = [
            auction("a", price=20.0, max_bid=50.0),
            auction("b", price=30.0, max_bid=40.0),
            auction("c"),  # no MAX
            auction("d", price=60.0, max_bid=40.0),  # bid already past the MAX
        ]
        assert report.build_report(deals, 30, RUN_DATE)[0] == (
            "No deals · 2 auctions with room under MAX (Oct 20)")

    def test_no_max_auctions_fall_through_to_the_next_element(self):
        assert report.build_report([auction("c")], 30, RUN_DATE)[0] == "No opportunities today (Oct 20)"
        cheap = auction("cheap", price=2.0, shipping_price=0.0)
        assert report.build_report([auction("c"), cheap], 30, RUN_DATE)[0] == (
            "No deals · 1 cheap auction to bid on (Oct 20)")

    def test_a_target_hit_still_outranks_the_no_deal_prefix(self):
        target = make_listing(is_opportunity=False, pct_under_market=1.0, target_hit=make_target_hit())
        assert report.build_report([target, auction("a", price=20.0, max_bid=50.0)], 30, RUN_DATE)[0] == (
            "1 target card hit (Oct 20)")
