from datetime import datetime, timezone

import pytest

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


def test_missing_seen_file_is_the_normal_first_run(tmp_path):
    assert dedupe.load_seen(tmp_path / "nope.json") == {}


def test_corrupt_seen_file_refuses_rather_than_resetting(tmp_path):
    # Starting fresh here means every listing already reported comes back as
    # new tomorrow -- and then that reset gets committed over the real file.
    path = tmp_path / "seen.json"
    path.write_text("{not valid json")
    with pytest.raises(dedupe.CorruptSeenListings):
        dedupe.load_seen(path)


# Hand-edited and legacy records: the file is documented as safe to edit, so
# the readers have to survive what an edit leaves behind. Both fixes fall the
# same way -- toward re-reporting, never toward suppressing.


def test_record_with_no_price_key_is_reported_rather_than_suppressed():
    seen = {"id1": {"first_seen": "2026-08-01", "last_flagged": "2026-08-26"}}
    assert dedupe.is_new_or_price_drop("id1", 20.0, seen) is True


def test_record_with_null_price_is_reported_rather_than_suppressed():
    seen = {"id1": {"price": None, "last_flagged": "2026-08-26"}}
    assert dedupe.is_new_or_price_drop("id1", 20.0, seen) is True


def test_record_with_unparseable_price_is_reported_rather_than_suppressed():
    seen = {"id1": {"price": "ask me", "last_flagged": "2026-08-26"}}
    assert dedupe.is_new_or_price_drop("id1", 20.0, seen) is True


def test_record_that_is_not_an_object_is_reported_rather_than_suppressed():
    assert dedupe.is_new_or_price_drop("id1", 20.0, {"id1": 25.0}) is True


def test_quoted_price_is_read_as_a_number():
    # A quoted number is still an unambiguous price, so it keeps working as one
    # in both directions: same price suppresses, lower price is a drop.
    assert dedupe.is_new_or_price_drop("id1", 25.0, {"id1": {"price": "25.00"}}) is False
    assert dedupe.is_new_or_price_drop("id1", 20.0, {"id1": {"price": "25.00"}}) is True


def test_unusable_price_is_logged_not_repaired_silently(caplog):
    with caplog.at_level("WARNING"):
        dedupe.is_new_or_price_drop("id1", 20.0, {"id1": {"price": None}})
    assert "id1" in caplog.text


def test_record_flagged_survives_a_prior_with_no_first_seen():
    seen = {"id1": {"price": None, "last_flagged": "2026-08-26"}}
    dedupe.record_flagged("id1", 20.0, seen, "2026-08-27")
    assert seen["id1"] == {"price": 20.0, "first_seen": "2026-08-27", "last_flagged": "2026-08-27"}


def test_prune_old_keeps_record_with_missing_last_flagged():
    # Dropping it un-sees the listing, and the user gets re-told about it.
    seen = {"id1": {"price": 50, "first_seen": "2026-08-26"}}
    pruned = dedupe.prune_old(seen, max_age_days=120, today=datetime(2026, 8, 27, tzinfo=timezone.utc))
    assert "id1" in pruned


def test_prune_old_keeps_record_with_unreadable_date_format():
    seen = {"id1": {"price": 50, "last_flagged": "2026/08/26"}}
    pruned = dedupe.prune_old(seen, max_age_days=120, today=datetime(2026, 8, 27, tzinfo=timezone.utc))
    assert "id1" in pruned


def test_prune_old_survives_a_record_that_is_not_an_object():
    seen = {"id1": "25.00"}
    pruned = dedupe.prune_old(seen, max_age_days=120, today=datetime(2026, 8, 27, tzinfo=timezone.utc))
    assert "id1" in pruned


def test_prune_old_reads_iso_timestamps():
    # Accepted, not merely kept: an unreadable date is kept forever, so a form
    # that names the day unambiguously should still age out on schedule.
    recent = {"id1": {"price": 50, "last_flagged": "2026-08-26T09:00:00Z"}}
    stale = {"id1": {"price": 50, "last_flagged": "2025-01-01T09:00:00Z"}}
    today = datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert "id1" in dedupe.prune_old(recent, max_age_days=120, today=today)
    assert dedupe.prune_old(stale, max_age_days=120, today=today) == {}


def test_prune_old_unreadable_date_is_logged_not_dropped_silently(caplog):
    with caplog.at_level("WARNING"):
        dedupe.prune_old(
            {"id1": {"price": 50, "last_flagged": "2026/08/26"}},
            max_age_days=120,
            today=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    assert "id1" in caplog.text
