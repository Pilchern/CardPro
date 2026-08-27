from datetime import datetime, timezone

import pytest

from src import price_history


def _obs(price, date, listing_id="", **identity_overrides):
    """Matches the shape price_history.record() now stores: price/date/id
    plus whatever identity fields were passed (None if not given)."""
    obs = {
        "price": price,
        "date": date,
        "id": listing_id,
        "year": None,
        "set_name": None,
        "parallel": None,
        "card_number": None,
        "grader": None,
        "grade": None,
        "qualifier": None,
        "print_run": None,
        "manufacturer": None,
        "is_base": None,
        "title": "",
        "basis": "asking",
    }
    obs.update(identity_overrides)
    return obs


def test_record_appends_to_bucket():
    history = {}
    price_history.record(history, "Michael Jordan", "graded", 100.0, "2026-08-10", "id-1")
    price_history.record(history, "Michael Jordan", "graded", 120.0, "2026-08-11", "id-2")
    assert history["Michael Jordan|graded"] == [
        _obs(100.0, "2026-08-10", "id-1"),
        _obs(120.0, "2026-08-11", "id-2"),
    ]


def test_record_defaults_listing_id_to_empty_string():
    history = {}
    price_history.record(history, "Michael Jordan", "graded", 100.0, "2026-08-10")
    assert history["Michael Jordan|graded"] == [_obs(100.0, "2026-08-10", "")]


def test_record_stores_card_identity_fields():
    history = {}
    price_history.record(
        history,
        "Caleb Williams",
        "graded",
        200.0,
        "2026-08-10",
        "id-1",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="123",
        grader="PSA",
        grade="10",
    )
    assert history["Caleb Williams|graded"] == [
        _obs(200.0, "2026-08-10", "id-1", year=2024, set_name="Prizm", parallel="Silver", card_number="123", grader="PSA", grade="10")
    ]


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


def test_load_corrupt_file_refuses_rather_than_starting_fresh(tmp_path):
    # It used to return {} with a warning saying the old file was left in
    # place. It was -- until save() replaced it with the day's observations
    # and the workflow committed the wipe. Failing here aborts the run, so
    # main emails the traceback and nothing gets committed over the file.
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")
    with pytest.raises(price_history.CorruptCorpus):
        price_history.load(path)


def test_save_refuses_to_replace_a_real_corpus_with_an_empty_one(tmp_path):
    path = tmp_path / "history.json"
    history = {}
    price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-1")
    price_history.save(path, history)

    with pytest.raises(price_history.CorruptCorpus):
        price_history.save(path, {})
    assert price_history.load(path) == history


def test_save_allows_a_prune_that_shrinks_but_does_not_empty(tmp_path):
    # Pruning legitimately removes observations. Second-guessing it here
    # would put the retention policy in two places.
    path = tmp_path / "history.json"
    history = {}
    for i in range(3):
        price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-%d" % i)
    price_history.save(path, history)

    smaller = {"Caleb Williams|raw": history["Caleb Williams|raw"][:1]}
    price_history.save(path, smaller)
    assert price_history.load(path) == smaller


def test_save_of_an_empty_corpus_is_fine_when_there_is_nothing_to_lose(tmp_path):
    path = tmp_path / "history.json"
    price_history.save(path, {})
    assert price_history.load(path) == {}


def test_record_stores_basis_and_qualifier():
    # basis distinguishes a seller's asking price from a real transaction --
    # the comp engine caps confidence on asking-basis comps, so this has to
    # be recorded honestly rather than defaulted optimistically.
    history = {}
    price_history.record(
        history,
        "Caleb Williams",
        "graded",
        400.0,
        "2026-08-22",
        "id-9",
        grader="PSA",
        grade="10",
        qualifier="OC",
        print_run=99,
        basis="sold",
    )
    stored = history["Caleb Williams|graded"][0]
    assert stored["basis"] == "sold"
    assert stored["qualifier"] == "OC"
    assert stored["print_run"] == 99


def test_record_defaults_to_asking_basis():
    history = {}
    price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-22", "id-10")
    assert history["Caleb Williams|raw"][0]["basis"] == "asking"


class TestOneRowPerListing:
    """Re-sighting a listing must update its row, not add another.

    Appending a row per sighting did two things: it inflated the file (2,099
    rows for 906 listings in the measured corpus) and, worse, it spread a
    single morning's batch of listings across several apparent dates, which
    is exactly the correlation comps._is_concentrated exists to refuse.
    """

    def _record(self, history, price, date, listing_id="id-1"):
        price_history.record(
            history, "Caleb Williams", "raw", price, date, listing_id, set_name="Prizm"
        )

    def test_the_same_listing_seen_again_does_not_add_a_row(self):
        history = {}
        self._record(history, 25.0, "2026-08-21")
        self._record(history, 25.0, "2026-08-22")
        self._record(history, 25.0, "2026-08-23")
        assert len(history["Caleb Williams|raw"]) == 1

    def test_the_date_stays_at_first_sighting(self):
        # Re-stamping it each morning is what manufactured the fake calendar
        # spread; the ask entered the market on the first date, not today.
        history = {}
        self._record(history, 25.0, "2026-08-21")
        self._record(history, 25.0, "2026-08-24")
        assert history["Caleb Williams|raw"][0]["date"] == "2026-08-21"

    def test_a_price_change_is_kept(self):
        history = {}
        self._record(history, 100.0, "2026-08-21")
        self._record(history, 60.0, "2026-08-24")
        row = history["Caleb Williams|raw"][0]
        assert (row["price"], row["date"]) == (60.0, "2026-08-21")

    def test_different_listings_still_get_their_own_rows(self):
        history = {}
        self._record(history, 25.0, "2026-08-21", "id-1")
        self._record(history, 30.0, "2026-08-21", "id-2")
        assert len(history["Caleb Williams|raw"]) == 2

    def test_a_listing_read_as_a_different_card_type_moves_rather_than_forking(self):
        # Live case: one eBay item stored under both `Munetaka Murakami|raw`
        # and `Munetaka Murakami|graded`, counted in two markets at once,
        # one of them wrong, for the whole 180-day window.
        history = {}
        price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-1")
        price_history.record(
            history, "Caleb Williams", "graded", 25.0, "2026-08-22", "id-1",
            grader="PSA", grade="10",
        )
        assert list(history) == ["Caleb Williams|graded"]
        assert len(history["Caleb Williams|graded"]) == 1

    def test_a_moved_listing_keeps_its_first_sighting_date(self):
        # The ask entered the market on the first date, whichever reading of
        # the title that sighting produced -- same rule as a re-sighting.
        history = {}
        price_history.record(history, "Caleb Williams", "raw", 100.0, "2026-08-21", "id-1")
        price_history.record(history, "Caleb Williams", "graded", 90.0, "2026-08-24", "id-1")
        row = history["Caleb Williams|graded"][0]
        assert (row["date"], row["price"]) == ("2026-08-21", 90.0)

    def test_moving_does_not_disturb_the_bucket_it_left(self):
        history = {}
        price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-1")
        price_history.record(history, "Caleb Williams", "raw", 30.0, "2026-08-21", "id-2")
        price_history.record(history, "Caleb Williams", "graded", 25.0, "2026-08-22", "id-1")
        assert [o["id"] for o in history["Caleb Williams|raw"]] == ["id-2"]

    def test_the_same_id_under_another_player_stays_its_own_row(self):
        # A multi-player card is genuinely in both players' markets, and
        # neither row can be re-fetched once the listing sells.
        history = {}
        price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-1")
        price_history.record(history, "Kyle Teel", "raw", 25.0, "2026-08-22", "id-1")
        assert sorted(history) == ["Caleb Williams|raw", "Kyle Teel|raw"]

    def test_an_idless_observation_is_still_appended(self):
        # No id means no way to tell two sightings apart, so appending is the
        # only honest option -- same as the pre-id data already on disk.
        history = {}
        price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "")
        price_history.record(history, "Caleb Williams", "raw", 26.0, "2026-08-22", "")
        assert len(history["Caleb Williams|raw"]) == 2

    def test_identity_fields_are_refreshed_on_re_sighting(self):
        # A later run may extract more from the same title (better
        # vocabulary), and the newer read is the better one.
        history = {}
        price_history.record(history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-1")
        price_history.record(
            history, "Caleb Williams", "raw", 25.0, "2026-08-22", "id-1", set_name="Prizm"
        )
        assert history["Caleb Williams|raw"][0]["set_name"] == "Prizm"


class TestCollapseDuplicates:
    def test_collapses_a_corpus_recorded_before_record_deduped(self):
        history = {"Caleb Williams|raw": [
            {"id": "a", "price": 100.0, "date": "2026-08-21"},
            {"id": "a", "price": 90.0, "date": "2026-08-22"},
            {"id": "b", "price": 50.0, "date": "2026-08-22"},
        ]}
        collapsed = price_history.collapse_duplicates(history)
        rows = collapsed["Caleb Williams|raw"]
        assert len(rows) == 2
        assert rows[0] == {"id": "a", "price": 90.0, "date": "2026-08-21"}

    def test_idless_rows_survive_untouched(self):
        history = {"Caleb Williams|raw": [
            {"price": 10.0, "date": "2026-08-21"},
            {"price": 11.0, "date": "2026-08-21"},
        ]}
        assert len(price_history.collapse_duplicates(history)["Caleb Williams|raw"]) == 2

    def test_collapsing_twice_changes_nothing_further(self):
        history = {"Caleb Williams|raw": [
            {"id": "a", "price": 100.0, "date": "2026-08-21"},
            {"id": "a", "price": 90.0, "date": "2026-08-22"},
        ]}
        once = price_history.collapse_duplicates(history)
        assert price_history.collapse_duplicates(once) == once

    def test_an_already_clean_corpus_is_unchanged(self):
        history = {"Caleb Williams|raw": [{"id": "a", "price": 100.0, "date": "2026-08-21"}]}
        assert price_history.collapse_duplicates(history) == history


def test_manufacturer_is_recorded_even_though_nothing_reads_it_yet():
    # The corpus is the only durable artefact -- titles are not stored -- so
    # a field extracted today and dropped is unrecoverable tomorrow.
    history = {}
    price_history.record(
        history, "Caleb Williams", "raw", 25.0, "2026-08-21", "id-1",
        set_name="Prizm", manufacturer="Panini",
    )
    assert history["Caleb Williams|raw"][0]["manufacturer"] == "Panini"


class TestOneListingOneMarket:
    """A listing that changed reading must not be counted in two markets.

    record() moves it when it sees it again, but a listing that flipped
    raw/graded and then sold leaves its stale row on disk until it ages out
    -- and one such pair was live in the corpus. Read-time dedupe is what
    stops every consumer double-counting that ask in the meantime.
    """

    def _flipped(self):
        return {
            "Munetaka Murakami|graded": [_obs(550.0, "2026-08-23", "itm-1", grader="PSA", grade="1")],
            "Munetaka Murakami|raw": [_obs(550.0, "2026-08-24", "itm-1")],
        }

    def test_a_stale_cross_card_type_row_is_not_counted_twice(self):
        observations = price_history.deduped_observations(self._flipped())
        assert len(observations) == 1

    def test_the_latest_reading_is_the_one_kept(self):
        observations = price_history.deduped_observations(self._flipped())
        assert observations[0]["card_type"] == "raw"

    def test_the_ask_reaches_only_one_bucket(self):
        assert price_history.as_buckets(self._flipped()) == {
            ("Munetaka Murakami", "raw", "100_plus"): [550.0]
        }

    def test_two_players_sharing_a_listing_both_keep_it(self):
        history = {
            "Caleb Williams|raw": [_obs(25.0, "2026-08-21", "id-1")],
            "Kyle Teel|raw": [_obs(25.0, "2026-08-22", "id-1")],
        }
        players = sorted(o["player"] for o in price_history.deduped_observations(history))
        assert players == ["Caleb Williams", "Kyle Teel"]

    def test_idless_rows_are_still_kept_one_per_sighting(self):
        history = {"Caleb Williams|raw": [_obs(10.0, "2026-08-21"), _obs(11.0, "2026-08-22")]}
        assert len(price_history.deduped_observations(history)) == 2


class TestCollapseAcrossCardTypes:
    def test_collapses_a_listing_stored_under_two_card_types(self):
        history = {
            "Munetaka Murakami|graded": [{"id": "a", "price": 550.0, "date": "2026-08-23"}],
            "Munetaka Murakami|raw": [{"id": "a", "price": 560.0, "date": "2026-08-24"}],
        }
        collapsed = price_history.collapse_duplicates(history)
        assert collapsed == {
            "Munetaka Murakami|raw": [{"id": "a", "price": 560.0, "date": "2026-08-23"}]
        }

    def test_a_listing_under_two_players_survives_in_both(self):
        history = {
            "Caleb Williams|raw": [{"id": "a", "price": 25.0, "date": "2026-08-21"}],
            "Kyle Teel|raw": [{"id": "a", "price": 25.0, "date": "2026-08-22"}],
        }
        assert price_history.collapse_duplicates(history) == history

    def test_collapsing_twice_changes_nothing_further(self):
        history = {
            "Munetaka Murakami|graded": [{"id": "a", "price": 550.0, "date": "2026-08-23"}],
            "Munetaka Murakami|raw": [{"id": "a", "price": 560.0, "date": "2026-08-24"}],
        }
        once = price_history.collapse_duplicates(history)
        assert price_history.collapse_duplicates(once) == once

    def test_other_listings_in_the_emptied_bucket_are_kept(self):
        history = {
            "Munetaka Murakami|graded": [
                {"id": "a", "price": 550.0, "date": "2026-08-23"},
                {"id": "b", "price": 400.0, "date": "2026-08-23"},
            ],
            "Munetaka Murakami|raw": [{"id": "a", "price": 550.0, "date": "2026-08-24"}],
        }
        collapsed = price_history.collapse_duplicates(history)
        assert [o["id"] for o in collapsed["Munetaka Murakami|graded"]] == ["b"]
        assert [o["id"] for o in collapsed["Munetaka Murakami|raw"]] == ["a"]
