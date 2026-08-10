from datetime import datetime, timezone

from src import dedupe


def test_new_listing_is_flagged():
    assert dedupe.is_new_or_price_drop("id1", 50, {}) is True


def test_unchanged_price_not_reflagged():
    seen = {}
    dedupe.record_flagged("id1", 50, seen, "2026-08-09")
    assert dedupe.is_new_or_price_drop("id1", 50, seen) is False


def test_price_drop_reflagged():
    seen = {}
    dedupe.record_flagged("id1", 50, seen, "2026-08-09")
    assert dedupe.is_new_or_price_drop("id1", 40, seen) is True


def test_price_increase_not_reflagged():
    seen = {}
    dedupe.record_flagged("id1", 50, seen, "2026-08-09")
    assert dedupe.is_new_or_price_drop("id1", 60, seen) is False


def test_record_flagged_preserves_first_seen():
    seen = {}
    dedupe.record_flagged("id1", 50, seen, "2026-08-09")
    dedupe.record_flagged("id1", 40, seen, "2026-08-10")
    assert seen["id1"] == {"price": 40, "first_seen": "2026-08-09", "last_flagged": "2026-08-10"}


def test_prune_old_removes_stale_entries():
    seen = {"id1": {"price": 50, "first_seen": "2026-01-01", "last_flagged": "2026-01-01"}}
    pruned = dedupe.prune_old(seen, max_age_days=1, today=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert pruned == {}


def test_prune_old_keeps_recent_entries():
    seen = {"id1": {"price": 50, "first_seen": "2026-08-09", "last_flagged": "2026-08-09"}}
    pruned = dedupe.prune_old(seen, max_age_days=120, today=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert "id1" in pruned
