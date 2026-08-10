"""End-to-end orchestration test with the eBay/email network layer mocked
out -- confirms fetch -> comp-building -> flagging -> dedupe -> report
wiring works together, without hitting real eBay/Gmail. Craigslist link
generation is a pure function (no network), so it isn't mocked.
"""
from __future__ import annotations

import importlib
import json
from unittest import mock

import pytest

from src import ebay_client, emailer


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "watchlist.json").write_text(json.dumps({"players": ["Michael Jordan"]}))
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps(
            {
                "discount_threshold_pct": 30,
                "ebay": {
                    "category_id": "212",
                    "marketplace_id": "EBAY_US",
                    "active_listing_limit_per_player": 50,
                    "sold_lookback_days": 60,
                    "min_comps_required": 3,
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
