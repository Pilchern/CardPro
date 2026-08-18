from datetime import datetime, timezone

from src import price_history


def test_record_appends_to_bucket():
    history = {}
    price_history.record(history, "Michael Jordan", "graded", 100.0, "2026-08-10")
    price_history.record(history, "Michael Jordan", "graded", 120.0, "2026-08-11")
    assert history["Michael Jordan|graded"] == [
        {"price": 100.0, "date": "2026-08-10"},
        {"price": 120.0, "date": "2026-08-11"},
    ]


def test_as_buckets_converts_to_comps_shape():
    history = {
        "Michael Jordan|graded": [{"price": 100.0, "date": "2026-08-10"}, {"price": 120.0, "date": "2026-08-11"}],
        "Walter Payton|raw": [{"price": 50.0, "date": "2026-08-10"}],
    }
    buckets = price_history.as_buckets(history)
    assert buckets[("Michael Jordan", "graded")] == [100.0, 120.0]
    assert buckets[("Walter Payton", "raw")] == [50.0]


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
