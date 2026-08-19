"""End-to-end orchestration test with the eBay/email network layer mocked
out -- confirms fetch -> comp-building -> flagging -> dedupe -> report
wiring works together, without hitting real eBay/Gmail. Craigslist link
generation is a pure function (no network), so it isn't mocked.
"""
from __future__ import annotations

import importlib
import json
import logging
from logging.handlers import RotatingFileHandler
from unittest import mock

import pytest

from src import ebay_client, ebay_email_alerts, emailer


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "watchlist.json").write_text(json.dumps({"players": ["Michael Jordan"]}))
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps(
            {
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
            }
        )
    )
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
def project_with_alerts_enabled(tmp_path, monkeypatch):
    """Same as `project`, but eBay API creds are absent and
    ebay_alerts.enabled=True, so run() takes the IMAP-alerts branch."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "watchlist.json").write_text(json.dumps({"players": ["Michael Jordan"]}))
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps(
            {
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
                    "enabled": True,
                    "sender_contains": "ebay.com",
                    "lookback_days": 2,
                    "price_history_path": "data/ebay_alert_price_history.json",
                    "price_history_max_age_days": 180,
                },
                "craigslist": {"site": "chicago", "category": "sss"},
                "dedupe": {"seen_listings_path": "data/seen_listings.json", "prune_after_days": 120},
                "email": {"subject_prefix": "[Card Deals]"},
            }
        )
    )
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
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


def fake_active(query, token, category_id, marketplace_id, limit=50):
    if "Michael Jordan" not in query:
        return []
    return [
        {"itemId": "e1", "title": "1986 Fleer Michael Jordan Rookie PSA 9", "price": {"value": "5000"}, "itemWebUrl": "http://ebay/e1"},
        {"itemId": "e2", "title": "Michael Jordan raw rookie reprint", "price": {"value": "10"}, "itemWebUrl": "http://ebay/e2"},
    ]


def fake_sold(query, token, category_id, marketplace_id, lookback_days, limit=100):
    if "Michael Jordan" not in query:
        return []
    return [{"title": "Michael Jordan PSA 9 rookie", "price": {"value": p}} for p in ("9000", "10000", "9500")]


def test_full_run_flags_underpriced_listings_and_emails_report(project, monkeypatch):
    sent = {}

    def fake_send_email(subject, body, gmail_address, gmail_app_password, to_address):
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(ebay_client, "get_app_token", lambda cid, secret: "fake-token")
    monkeypatch.setattr(ebay_client, "search_active_listings", fake_active)
    monkeypatch.setattr(ebay_client, "search_sold_items", fake_sold)
    monkeypatch.setattr(emailer, "send_email", fake_send_email)
    monkeypatch.setattr("sys.argv", ["main.py"])

    project.main()

    assert "1 card deal found" in sent["subject"]
    assert "eBay" in sent["body"]
    # the $10 "raw" reprint has no raw comps (all sold comps were graded) so it must NOT be flagged
    assert "reprint" not in sent["body"]
    # Craigslist isn't scraped, but a quick-check link for the player should still be included
    assert "Craigslist quick check" in sent["body"]
    assert "chicago.craigslist.org/search/sss" in sent["body"]


def test_second_run_with_unchanged_prices_reports_nothing_new(project, monkeypatch):
    monkeypatch.setattr(ebay_client, "get_app_token", lambda cid, secret: "fake-token")
    monkeypatch.setattr(ebay_client, "search_active_listings", fake_active)
    monkeypatch.setattr(ebay_client, "search_sold_items", fake_sold)
    monkeypatch.setattr("sys.argv", ["main.py"])

    sent_subjects = []
    monkeypatch.setattr(
        emailer, "send_email", lambda subject, body, *a, **kw: sent_subjects.append(subject)
    )

    project.main()
    project.main()

    assert "No deals today" in sent_subjects[1]


def test_dry_run_does_not_send_email_or_write_dedupe_file(project, monkeypatch, capsys):
    monkeypatch.setattr(ebay_client, "get_app_token", lambda cid, secret: "fake-token")
    monkeypatch.setattr(ebay_client, "search_active_listings", fake_active)
    monkeypatch.setattr(ebay_client, "search_sold_items", fake_sold)
    monkeypatch.setattr(emailer, "send_email", mock.Mock(side_effect=AssertionError("should not send in dry-run")))
    monkeypatch.setattr("sys.argv", ["main.py", "--dry-run"])

    project.main()

    from src.config import load_config

    cfg = load_config()
    assert not cfg.seen_listings_path.exists()
    assert "card deal found" in capsys.readouterr().out


def test_unhandled_error_still_sends_a_failure_email(project, monkeypatch):
    def boom(client_id, client_secret):
        raise RuntimeError("eBay is down")

    sent = {}

    def fake_send_email(subject, body, gmail_address, gmail_app_password, to_address):
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(ebay_client, "get_app_token", boom)
    monkeypatch.setattr(emailer, "send_email", fake_send_email)
    monkeypatch.setattr("sys.argv", ["main.py"])

    with pytest.raises(RuntimeError, match="eBay is down"):
        project.main()

    assert "FAILED" in sent["subject"]
    assert "scraper.log" in sent["body"]


def test_dry_run_does_not_send_failure_email_on_error(project, monkeypatch):
    def boom(client_id, client_secret):
        raise RuntimeError("eBay is down")

    monkeypatch.setattr(ebay_client, "get_app_token", boom)
    monkeypatch.setattr(emailer, "send_email", mock.Mock(side_effect=AssertionError("should not send in dry-run")))
    monkeypatch.setattr("sys.argv", ["main.py", "--dry-run"])

    with pytest.raises(RuntimeError, match="eBay is down"):
        project.main()


def test_runs_successfully_without_ebay_credentials(project, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(
        ebay_client, "get_app_token", mock.Mock(side_effect=AssertionError("eBay should not be called"))
    )

    sent = {}

    def fake_send_email(subject, body, gmail_address, gmail_app_password, to_address):
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(emailer, "send_email", fake_send_email)
    monkeypatch.setattr("sys.argv", ["main.py"])

    project.main()

    assert "eBay not configured" in sent["subject"]
    assert "Craigslist quick check" in sent["body"]
    assert "chicago.craigslist.org/search/sss" in sent["body"]


def test_ebay_alerts_path_cold_start_flags_nothing_yet(project_with_alerts_enabled, monkeypatch):
    """With fewer than min_comps_required observations, no comp exists yet
    for the bucket, so nothing should be flagged -- not a false deal."""
    monkeypatch.setattr(
        ebay_email_alerts,
        "fetch_alert_listings",
        lambda *a, **kw: [{"title": "Michael Jordan PSA 9 rookie", "url": "https://www.ebay.com/itm/1001", "price": 100.0}],
    )
    sent = {}
    monkeypatch.setattr(
        emailer, "send_email", lambda subject, body, *a, **kw: sent.update(subject=subject, body=body)
    )
    monkeypatch.setattr("sys.argv", ["main.py"])

    project_with_alerts_enabled.main()

    assert "No deals today" in sent["subject"]


def test_ebay_alerts_path_flags_deal_once_enough_history_accumulated(project_with_alerts_enabled, monkeypatch):
    responses = [
        [{"title": "Michael Jordan PSA 9 rookie", "url": "https://www.ebay.com/itm/1001", "price": 9000.0}],
        [{"title": "Michael Jordan PSA 9 rookie", "url": "https://www.ebay.com/itm/1002", "price": 9500.0}],
        [{"title": "Michael Jordan PSA 9 rookie", "url": "https://www.ebay.com/itm/1003", "price": 10000.0}],
        [{"title": "Michael Jordan PSA 9 rookie", "url": "https://www.ebay.com/itm/1004", "price": 5000.0}],
    ]
    call_index = {"n": -1}

    def fake_fetch(*a, **kw):
        call_index["n"] += 1
        return responses[call_index["n"]]

    monkeypatch.setattr(ebay_email_alerts, "fetch_alert_listings", fake_fetch)

    sent = []
    monkeypatch.setattr(emailer, "send_email", lambda subject, body, *a, **kw: sent.append((subject, body)))
    monkeypatch.setattr("sys.argv", ["main.py"])

    for _ in range(4):
        project_with_alerts_enabled.main()

    last_subject, last_body = sent[-1]
    assert "1 card deal found" in last_subject
    assert "eBay (saved-search alert)" in last_body


def test_ebay_alerts_dry_run_does_not_persist_price_history(project_with_alerts_enabled, monkeypatch):
    monkeypatch.setattr(
        ebay_email_alerts,
        "fetch_alert_listings",
        lambda *a, **kw: [{"title": "Michael Jordan PSA 9 rookie", "url": "https://www.ebay.com/itm/1001", "price": 100.0}],
    )
    monkeypatch.setattr(emailer, "send_email", mock.Mock(side_effect=AssertionError("should not send in dry-run")))
    monkeypatch.setattr("sys.argv", ["main.py", "--dry-run"])

    project_with_alerts_enabled.main()

    from src.config import load_config

    cfg = load_config()
    assert not cfg.ebay_alert_price_history_path.exists()


def test_logging_is_rotated_not_unbounded(project, monkeypatch):
    monkeypatch.setattr(ebay_client, "get_app_token", lambda cid, secret: "fake-token")
    monkeypatch.setattr(ebay_client, "search_active_listings", fake_active)
    monkeypatch.setattr(ebay_client, "search_sold_items", fake_sold)
    monkeypatch.setattr(emailer, "send_email", lambda *a, **kw: None)
    monkeypatch.setattr("sys.argv", ["main.py"])

    project.main()

    file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes == project.LOG_MAX_BYTES
    assert file_handlers[0].backupCount == project.LOG_BACKUP_COUNT


class TestEnrichTruncatedGrades:
    """Unit tests for main.enrich_truncated_grades -- pure function, no
    fixture/config needed."""

    def _listing(self, **overrides):
        from src.models import Listing

        defaults = dict(
            id="1",
            source="ebay-alert",
            title="1990 Fleer Frank Thomas PSA 1…",
            price=350.0,
            url="https://www.ebay.com/itm/800530598774",
            player="Frank Thomas",
            card_type="graded",
            grader="PSA",
            grade="1",
        )
        defaults.update(overrides)
        return Listing(**defaults)

    def test_fixes_grade_when_fetch_succeeds(self, monkeypatch):
        from src import ebay_email_alerts, main

        monkeypatch.setattr(
            ebay_email_alerts, "fetch_full_title", lambda url: "1990 Fleer Frank Thomas PSA 10"
        )
        deal = self._listing()
        main.enrich_truncated_grades([deal])

        assert deal.grade == "10"
        assert deal.title == "1990 Fleer Frank Thomas PSA 10"
        assert deal.title_truncated is False

    def test_marks_uncertain_when_fetch_fails(self, monkeypatch):
        from src import ebay_email_alerts, main

        monkeypatch.setattr(ebay_email_alerts, "fetch_full_title", lambda url: None)
        deal = self._listing()
        main.enrich_truncated_grades([deal])

        assert deal.grade == "1"  # left as-is, not overwritten with a guess
        assert deal.title_truncated is True

    def test_skips_non_truncated_titles(self, monkeypatch):
        from src import ebay_email_alerts, main

        fetch = mock.Mock(side_effect=AssertionError("should not be called"))
        monkeypatch.setattr(ebay_email_alerts, "fetch_full_title", fetch)
        deal = self._listing(title="1990 Fleer Frank Thomas PSA 10")
        main.enrich_truncated_grades([deal])

        assert deal.title_truncated is False
        fetch.assert_not_called()

    def test_skips_raw_listings_even_if_truncated(self, monkeypatch):
        from src import ebay_email_alerts, main

        fetch = mock.Mock(side_effect=AssertionError("should not be called"))
        monkeypatch.setattr(ebay_email_alerts, "fetch_full_title", fetch)
        deal = self._listing(card_type="raw", grader=None, grade=None, title="1993-94 Fleer Michael Jordan…")
        main.enrich_truncated_grades([deal])

        assert deal.title_truncated is False
        fetch.assert_not_called()


class TestFlagDeals:
    """Unit tests for main.flag_deals -- pure function, no fixture needed."""

    def _listing(self, **overrides):
        from src.models import Listing

        defaults = dict(
            id="1",
            source="ebay",
            title="Michael Jordan card",
            price=100.0,
            url="http://example.com/1",
            player="Michael Jordan",
            card_type="raw",
        )
        defaults.update(overrides)
        return Listing(**defaults)

    def _comp_table(self, median, tier, n=5):
        """The comp table key's tier must match the LISTING's own price
        tier (that's what flag_deals looks up by), not the comp median's
        tier -- see comps.price_tier."""
        from src import comps

        return {("Michael Jordan", "raw", tier): comps.CompStats(median=median, sample_size=n, is_fallback=False)}

    def test_flags_when_both_percent_and_dollar_gates_clear(self):
        from src import main

        listing = self._listing(price=100.0)  # $100 -> "100_plus" tier; comp 200 -> 50% off, $100 saved
        flagged = main.flag_deals([listing], self._comp_table(200.0, "100_plus"), threshold_pct=30, min_savings_dollars=10)
        assert flagged == [listing]
        assert listing.dollar_savings == 100.0
        assert listing.pct_under_market == 50.0

    def test_blocked_by_dollar_gate_despite_clearing_percent(self):
        """50% off is well past the percent threshold, but $1 saved isn't
        worth a click -- the dollar gate must still block it."""
        from src import main

        listing = self._listing(price=1.0)  # $1 -> "under_5" tier; comp 2.00 -> 50% off, $1 saved
        flagged = main.flag_deals([listing], self._comp_table(2.0, "under_5"), threshold_pct=30, min_savings_dollars=10)
        assert flagged == []

    def test_blocked_by_percent_gate_despite_clearing_dollar_amount(self):
        """$50 off a $10,000 card is real money but barely under market --
        the percent gate must still block it."""
        from src import main

        listing = self._listing(price=9950.0)  # $9950 -> "100_plus" tier; comp 10000 -> 0.5% off, $50 saved
        flagged = main.flag_deals([listing], self._comp_table(10000.0, "100_plus"), threshold_pct=30, min_savings_dollars=10)
        assert flagged == []

    def test_min_savings_dollars_defaults_to_zero(self):
        """Backwards-compatible default: omitting min_savings_dollars
        shouldn't block anything that clears the percent threshold."""
        from src import main

        listing = self._listing(price=1.0)
        flagged = main.flag_deals([listing], self._comp_table(2.0, "under_5"), threshold_pct=30)
        assert flagged == [listing]

    def test_comp_lookup_uses_listings_own_price_tier_not_comp_medians(self):
        """A $1 listing must NOT be compared against a comp bucket built
        from $90 items -- this is the whole point of tiering. A comp only
        sitting in the "100_plus" bucket should never match a $1 listing,
        even if that bucket exists."""
        from src import main

        listing = self._listing(price=1.0)  # "under_5" tier
        comp_table = self._comp_table(90.0, "100_plus")  # wrong tier on purpose
        flagged = main.flag_deals([listing], comp_table, threshold_pct=30, min_savings_dollars=0)
        assert flagged == []
        assert listing.comp_median is None  # never matched, so never filled in
