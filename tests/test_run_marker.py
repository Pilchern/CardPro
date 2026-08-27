"""Tests for the last-run marker.

GitHub's scheduled workflows are best-effort: under load they are delayed by
hours and sometimes dropped outright. A dropped run was the one failure this
project could not see -- no email, no failure notification, no red job, just
silence indistinguishable from a day you were not looking.
"""
from __future__ import annotations

import json

from src import run_marker


class TestRanOn:
    def test_a_missing_marker_means_not_yet_run(self, tmp_path):
        assert run_marker.ran_on(tmp_path / "nope.json", "2026-08-27") is False

    def test_a_marker_from_today_means_already_run(self, tmp_path):
        path = tmp_path / "m.json"
        run_marker.save(path, "2026-08-27", 15)
        assert run_marker.ran_on(path, "2026-08-27") is True

    def test_a_marker_from_yesterday_does_not(self, tmp_path):
        path = tmp_path / "m.json"
        run_marker.save(path, "2026-08-26", 15)
        assert run_marker.ran_on(path, "2026-08-27") is False

    def test_an_unreadable_marker_is_not_fatal(self, tmp_path, caplog):
        # The worst a spurious extra run costs is a few seconds of a runner.
        # Refusing to scan because a convenience file is corrupt would be the
        # exact failure this module exists to prevent.
        path = tmp_path / "m.json"
        path.write_text("{not valid json")
        with caplog.at_level("WARNING"):
            assert run_marker.ran_on(path, "2026-08-27") is False
        assert "unreadable" in caplog.text

    def test_a_marker_of_the_wrong_shape_is_ignored(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text('["not", "a", "dict"]')
        assert run_marker.ran_on(path, "2026-08-27") is False

    def test_the_seeded_empty_marker_does_not_block_a_run(self, tmp_path):
        # The file is committed so the workflow's `git add` always has
        # something to add; it must not look like a run.
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"date": "", "listings_seen": 0}))
        assert run_marker.ran_on(path, "2026-08-27") is False


class TestGapDays:
    def test_the_ordinary_cadence_is_one(self, tmp_path):
        path = tmp_path / "m.json"
        run_marker.save(path, "2026-08-26", 15)
        assert run_marker.gap_days(path, "2026-08-27") == 1

    def test_a_missed_day_shows_as_two(self, tmp_path):
        path = tmp_path / "m.json"
        run_marker.save(path, "2026-08-25", 15)
        assert run_marker.gap_days(path, "2026-08-27") == 2

    def test_a_second_run_the_same_day_is_zero(self, tmp_path):
        path = tmp_path / "m.json"
        run_marker.save(path, "2026-08-27", 15)
        assert run_marker.gap_days(path, "2026-08-27") == 0

    def test_unknown_stays_unknown(self, tmp_path):
        assert run_marker.gap_days(tmp_path / "nope.json", "2026-08-27") is None

    def test_an_unparseable_date_is_unknown_not_a_crash(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"date": "last tuesday"}))
        assert run_marker.gap_days(path, "2026-08-27") is None


class TestSave:
    def test_it_records_the_date_and_what_was_seen(self, tmp_path):
        path = tmp_path / "m.json"
        run_marker.save(path, "2026-08-27", 42)
        assert json.loads(path.read_text()) == {"date": "2026-08-27", "listings_seen": 42}

    def test_it_creates_the_directory(self, tmp_path):
        path = tmp_path / "deeper" / "m.json"
        run_marker.save(path, "2026-08-27", 1)
        assert path.exists()
