from datetime import datetime, timezone

from src import price_history


def test_record_appends_to_bucket():
    history = {}
    price_history.record(history, "Michael Jordan", "graded", 100.0, "2026-08-10", "id-1")
    price_history.record(history, "Michael Jordan", "graded", 120.0, "2026-08-11", "id-2")
    assert history["Michael Jordan|graded"] == [
        {"price": 100.0, "date": "2026-08-10", "id": "id-1"},
        {"price": 120.0, "date": "2026-08-11", "id": "id-2"},
    ]


def test_record_defaults_listing_id_to_empty_string():
    history = {}
    price_history.record(history, "Michael Jordan", "graded", 100.0, "2026-08-10")
    assert history["Michael Jordan|graded"] == [{"price": 100.0, "date": "2026-08-10", "id": ""}]


def test_as_buckets_converts_to_comps_shape():
    history = {
        "Michael Jordan|graded": [
            {"price": 100.0, "date": "2026-08-10", "id": "id-1"},
            {"price": 120.0, "date": "2026-08-11", "id": "id-2"},
        ],
        "Walter Payton|raw": [{"price": 50.0, "date": "2026-08-10", "id": "id-3"}],
    }
    buckets = price_history.as_buckets(history)
    assert buckets[("Michael Jordan", "graded", "100_plus")] == [100.0, 120.0]
    assert buckets[("Walter Payton", "raw", "25_to_100")] == [50.0]


def test_as_buckets_separates_observations_by_price_tier():
    """A $1 common and a $150 parallel observed for the same
    player/card_type must land in different buckets, not get averaged
    together -- this is the whole reason as_buckets tiers at read time."""
    history = {
        "Michael Jordan|raw": [
            {"price": 1.0, "date": "2026-08-10", "id": "id-1"},
            {"price": 1.5, "date": "2026-08-10", "id": "id-2"},
            {"price": 150.0, "date": "2026-08-10", "id": "id-3"},
        ],
    }
    buckets = price_history.as_buckets(history)
    assert buckets[("Michael Jordan", "raw", "under_5")] == [1.0, 1.5]
    assert buckets[("Michael Jordan", "raw", "100_plus")] == [150.0]


def test_as_buckets_collapses_repeat_sightings_of_the_same_listing():
    """The same listing id observed on multiple days (e.g. because
    ebay_alerts_lookback_days overlaps two consecutive runs) must only
    contribute its latest price once, not once per sighting -- otherwise a
    single relisted/overlapping item silently doubles its own weight in the
    comp median."""
    history = {
        "Michael Jordan|graded": [
            {"price": 100.0, "date": "2026-08-10", "id": "https://ebay.com/itm/1"},
            {"price": 100.0, "date": "2026-08-11", "id": "https://ebay.com/itm/1"},  # same listing, re-seen
            {"price": 250.0, "date": "2026-08-11", "id": "https://ebay.com/itm/2"},  # a genuinely different listing
        ],
    }
    buckets = price_history.as_buckets(history)
    assert sorted(buckets[("Michael Jordan", "graded", "100_plus")]) == [100.0, 250.0]


def test_as_buckets_keeps_latest_price_when_same_listing_price_changes():
    history = {
        "Michael Jordan|graded": [
            {"price": 200.0, "date": "2026-08-10", "id": "https://ebay.com/itm/1"},
            {"price": 150.0, "date": "2026-08-12", "id": "https://ebay.com/itm/1"},  # price dropped, still same listing
        ],
    }
    buckets = price_history.as_buckets(history)
    assert buckets[("Michael Jordan", "graded", "100_plus")] == [150.0]


def test_as_buckets_keeps_legacy_observations_without_an_id_ungrouped():
    """Observations recorded before the "id" field existed have no id to
    dedupe by -- they're kept as individual observations rather than
    dropped, and naturally age out via prune_old."""
    history = {
        "Michael Jordan|graded": [
            {"price": 100.0, "date": "2026-08-10"},
            {"price": 110.0, "date": "2026-08-11"},
        ],
    }
    buckets = price_history.as_buckets(history)
    assert sorted(buckets[("Michael Jordan", "graded", "100_plus")]) == [100.0, 110.0]


def test_prune_old_removes_stale_observations_but_keeps_bucket_if_any_remain():
    history = {
        "Michael Jordan|graded": [
            {"price": 100.0, "date": "2026-01-01"},
            {"price": 200.0, "date": "2026-08-09"},
        ]
    }
    pruned = price_history.prune_old(history, max_age_days=30, today=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert pruned["Michael Jordan|graded"] == [{"price": 200.0, "date": "2026-08-09"}]


def test_prune_old_drops_bucket_entirely_when_all_stale():
    history = {"Michael Jordan|graded": [{"price": 100.0, "date": "2026-01-01"}]}
    pruned = price_history.prune_old(history, max_age_days=30, today=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert pruned == {}


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "history.json"
    history = {"Michael Jordan|graded": [{"price": 100.0, "date": "2026-08-10"}]}
    price_history.save(path, history)
    loaded = price_history.load(path)
    assert loaded == history


def test_load_missing_file_returns_empty_dict(tmp_path):
    assert price_history.load(tmp_path / "does_not_exist.json") == {}


def test_load_corrupt_file_returns_empty_dict_and_does_not_raise(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")
    assert price_history.load(path) == {}
