"""Tests for hand-entered sold comps.

The load-side tests are mostly about REJECTION: this corpus is tiny and
trusted more than anything else, so a bad entry must be dropped loudly rather
than averaged in. The final test is the point of the whole module -- a sold
comp reaching "high" confidence, which no asking-price comp in this project
can ever do.
"""
import json
import logging
from datetime import datetime, timezone

import pytest

from src import comps, sold_comps
from scripts import add_sold_comp


def _sale(**overrides):
    sale = {
        "player": "Caleb Williams",
        "year": 2024,
        "set_name": "Prizm",
        "parallel": "Silver",
        "card_number": "301",
        "grader": "PSA",
        "grade": "10",
        "price": 348.0,
        "date": "2026-08-15",
        "source": "130point",
    }
    sale.update(overrides)
    return {k: v for k, v in sale.items() if v is not _OMIT}


_OMIT = object()


def _write(path, sales, **extra):
    document = {"_comment": "hand-edited", "_example": {"player": "x"}, "sales": sales}
    document.update(extra)
    path.write_text(json.dumps(document))
    return path


# -- happy path -------------------------------------------------------------


def test_load_returns_comp_engine_observation_shape(tmp_path):
    path = _write(tmp_path / "sold_comps.json", [_sale()])

    observations = sold_comps.load(path)

    assert len(observations) == 1
    obs = observations[0]
    assert obs["price"] == 348.0
    assert obs["date"] == "2026-08-15"
    assert obs["player"] == "Caleb Williams"
    assert obs["card_type"] == "graded"
    assert obs["year"] == 2024
    assert obs["set_name"] == "Prizm"
    assert obs["parallel"] == "Silver"
    assert obs["card_number"] == "301"
    assert obs["grader"] == "PSA"
    assert obs["grade"] == "10"
    assert obs["qualifier"] is None
    assert obs["print_run"] is None
    assert obs["basis"] == comps.BASIS_SOLD
    assert obs["id"].startswith("sold:")


def test_raw_sale_gets_raw_card_type(tmp_path):
    path = _write(tmp_path / "s.json", [_sale(grader=_OMIT, grade=_OMIT)])

    assert sold_comps.load(path)[0]["card_type"] == "raw"


def test_string_price_and_year_are_normalised(tmp_path):
    """The file is typed by hand: "$1,250.00" and "2024" must land in the same
    buckets as 1250.0 and 2024."""
    path = _write(tmp_path / "s.json", [_sale(price="$1,250.00", year="2024")])

    obs = sold_comps.load(path)[0]
    assert obs["price"] == 1250.0
    assert obs["year"] == 2024


def test_shipping_is_carried_but_never_folded_into_price(tmp_path):
    path = _write(tmp_path / "s.json", [_sale(price=348.0, shipping=5.0)])

    obs = sold_comps.load(path)[0]
    assert obs["price"] == 348.0
    assert obs["shipping"] == 5.0


def test_qualifier_and_grader_are_case_folded(tmp_path):
    path = _write(tmp_path / "s.json", [_sale(grader="psa", qualifier="oc")])

    obs = sold_comps.load(path)[0]
    assert obs["grader"] == "PSA"
    assert obs["qualifier"] == "OC"
    assert comps.market_key(obs["card_type"], obs["grader"], obs["grade"], obs["qualifier"]) == (
        "graded",
        "PSA",
        "10",
        "OC",
    )


# -- rejections -------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,expected_fragment",
    [
        ({"player": _OMIT}, "player"),
        ({"player": "   "}, "player"),
        ({"price": _OMIT}, "price"),
        ({"price": 0}, "positive"),
        ({"price": -25}, "positive"),
        ({"price": "not a number"}, "positive"),
        ({"date": _OMIT}, "date"),
        ({"date": "15/08/2026"}, "valid YYYY-MM-DD"),
        ({"date": "2026-02-30"}, "valid YYYY-MM-DD"),
        ({"date": ""}, "date"),
        ({"grade": _OMIT}, "no 'grade'"),
        ({"grader": _OMIT}, "no 'grader'"),
        ({"year": "rookie year"}, "year"),
        ({"shipping": -3}, "shipping"),
    ],
)
def test_invalid_sales_are_skipped_with_a_warning(tmp_path, caplog, overrides, expected_fragment):
    path = _write(tmp_path / "s.json", [_sale(**overrides)])

    with caplog.at_level(logging.WARNING):
        observations = sold_comps.load(path)

    assert observations == []
    assert expected_fragment in caplog.text
    # The warning has to name the offender or it can't be found in the file.
    assert "Caleb Williams" in caplog.text or "player" in caplog.text


def test_a_bad_entry_does_not_take_the_good_ones_with_it(tmp_path):
    path = _write(tmp_path / "s.json", [_sale(price=-1), _sale(price=300.0), _sale(date="nonsense")])

    observations = sold_comps.load(path)

    assert [obs["price"] for obs in observations] == [300.0]


def test_non_dict_entry_is_skipped(tmp_path, caplog):
    path = _write(tmp_path / "s.json", ["just a string", _sale()])

    with caplog.at_level(logging.WARNING):
        observations = sold_comps.load(path)

    assert len(observations) == 1
    assert "not a JSON object" in caplog.text


def test_sales_key_of_the_wrong_type_is_ignored(tmp_path, caplog):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"sales": {"player": "Caleb Williams"}}))

    with caplog.at_level(logging.WARNING):
        assert sold_comps.load(path) == []
    assert "expected a list" in caplog.text


# -- missing / corrupt files ------------------------------------------------


def test_missing_file_is_the_normal_case(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert sold_comps.load(tmp_path / "nope.json") == []
    assert caplog.text == ""  # not having sold comps is not a problem worth logging


def test_corrupt_file_warns_and_returns_empty(tmp_path, caplog):
    path = tmp_path / "s.json"
    path.write_text("{ this is not json")

    with caplog.at_level(logging.WARNING):
        assert sold_comps.load(path) == []

    assert "not valid JSON" in caplog.text
    assert path.read_text() == "{ this is not json"  # never repaired in place


def test_file_containing_a_list_instead_of_an_object_warns(tmp_path, caplog):
    path = tmp_path / "s.json"
    path.write_text("[]")

    with caplog.at_level(logging.WARNING):
        assert sold_comps.load(path) == []
    assert "must contain a JSON object" in caplog.text


def test_missing_sales_key_returns_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"_comment": "not filled in yet"}))

    assert sold_comps.load(path) == []


# -- id stability -----------------------------------------------------------


def test_id_is_stable_across_reordering(tmp_path):
    a, b, c = _sale(price=100.0), _sale(price=200.0), _sale(price=300.0)
    forward = _write(tmp_path / "a.json", [a, b, c])
    reversed_ = _write(tmp_path / "b.json", [c, b, a])

    ids_forward = {obs["price"]: obs["id"] for obs in sold_comps.load(forward)}
    ids_reversed = {obs["price"]: obs["id"] for obs in sold_comps.load(reversed_)}

    assert ids_forward == ids_reversed


def test_id_is_stable_across_runs_and_not_index_based(tmp_path):
    path = _write(tmp_path / "s.json", [_sale()])
    first = sold_comps.load(path)[0]["id"]

    grown = _write(tmp_path / "s.json", [_sale(price=99.0), _sale()])
    assert first in {obs["id"] for obs in sold_comps.load(grown)}
    assert sold_comps.sale_id(_sale()) == first


def test_different_sales_get_different_ids():
    base = _sale()
    assert sold_comps.sale_id(base) != sold_comps.sale_id(_sale(price=349.0))
    assert sold_comps.sale_id(base) != sold_comps.sale_id(_sale(grade="9"))
    assert sold_comps.sale_id(base) != sold_comps.sale_id(_sale(date="2026-08-16"))


def test_id_ignores_cosmetic_differences():
    """Re-typing the same sale must not create a second identity."""
    assert sold_comps.sale_id(_sale()) == sold_comps.sale_id(
        _sale(player="  Caleb Williams  ", grader="psa", year="2024", price="348")
    )


def test_identical_duplicate_sales_get_distinct_ids(tmp_path):
    """Two genuinely identical sales are indistinguishable, but they must not
    share an id -- self-exclusion would otherwise drop both at once."""
    path = _write(tmp_path / "s.json", [_sale(), _sale()])

    ids = [obs["id"] for obs in sold_comps.load(path)]
    assert len(set(ids)) == 2


# -- summary ----------------------------------------------------------------


def test_summary_of_nothing_says_so():
    assert "none" in sold_comps.summary([]).lower()
    assert "asking" in sold_comps.summary([]).lower()


def test_summary_reports_count_and_date_range(tmp_path):
    path = _write(
        tmp_path / "s.json",
        [_sale(date="2026-06-01"), _sale(date="2026-08-15", player="Connor Bedard")],
    )

    line = sold_comps.summary(sold_comps.load(path))

    assert "2" in line
    assert "2026-06-01 to 2026-08-15" in line
    assert "2 players" in line


def test_summary_is_pure():
    """No clock, no IO: same input, same line, every time."""
    sales = [{"player": "A", "price": 10.0, "date": "2026-01-01"}]
    assert sold_comps.summary(sales) == sold_comps.summary(sales)
    assert sales == [{"player": "A", "price": 10.0, "date": "2026-01-01"}]


def test_summary_ignores_dateless_rows_rather_than_crashing():
    assert "1 hand-entered sale" in sold_comps.summary(
        [{"player": "A", "price": 10.0, "date": "2026-01-01"}, {"player": "B", "price": 5.0}]
    )


# -- the point: sold comps reach a confidence asking prices cannot ----------


def _sold_run(price, date, **overrides):
    return _sale(price=price, date=date, **overrides)


def test_sold_observations_drive_comp_engine_to_high_confidence(tmp_path):
    """The whole reason this module exists.

    Five sold comps for one exact card produce an "exact"-level match with
    basis "sold" and confidence "high". The identical corpus recorded as
    asking prices cannot: comps.assess_comp_match downgrades any non-sold
    basis one step, capping it at "medium".
    """
    prices = [340.0, 345.0, 348.0, 350.0, 355.0]
    # Spread over weeks so the asking-price half of the comparison clears
    # comps' sample-span gate too -- otherwise it would be downgraded for
    # being time-concentrated and the test would prove the wrong thing.
    dates = ["2026-07-20", "2026-07-27", "2026-08-03", "2026-08-10", "2026-08-14"]
    path = _write(tmp_path / "s.json", [_sold_run(p, d) for p, d in zip(prices, dates)])
    observations = sold_comps.load(path)

    engine = comps.CompEngine(
        observations,
        min_comps_required=3,
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    match = engine.lookup(
        player="Caleb Williams",
        card_type="graded",
        price=299.0,
        grader="PSA",
        grade="10",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="301",
    )

    assert match is not None
    assert match.level == "exact"
    assert match.stats.basis == comps.BASIS_SOLD
    assert match.confidence == "high"
    assert match.flag_eligible is True
    assert match.blocked_reasons == ()
    assert match.stats.sample_size == 5

    # Same numbers, recorded as asking prices: structurally capped at medium.
    asking = [dict(obs, basis=comps.BASIS_ASKING) for obs in observations]
    asking_match = comps.CompEngine(
        asking, min_comps_required=3, today=datetime(2026, 8, 15, tzinfo=timezone.utc)
    ).lookup(
        player="Caleb Williams",
        card_type="graded",
        price=299.0,
        grader="PSA",
        grade="10",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="301",
    )
    assert asking_match.confidence == "medium"
    assert asking_match.confidence != "high"  # the ceiling, whatever else changes


def test_sold_comps_entered_for_one_day_still_count(tmp_path):
    """Hand-entered sold comps are scarce by construction, so the sample-span
    gate that guards against a single morning's asking-price snapshot must not
    apply to them: three sales on one day are three real buyers, not one
    seller's optimism listed three times."""
    path = _write(tmp_path / "s.json", [_sold_run(p, "2026-08-14") for p in (340.0, 348.0, 356.0)])

    match = comps.CompEngine(
        sold_comps.load(path), min_comps_required=3, today=datetime(2026, 8, 15, tzinfo=timezone.utc)
    ).lookup(
        player="Caleb Williams",
        card_type="graded",
        price=299.0,
        grader="PSA",
        grade="10",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="301",
    )

    assert match is not None
    assert match.stats.basis == comps.BASIS_SOLD
    assert match.flag_eligible is True


def test_sold_comp_excludes_itself_from_its_own_median(tmp_path):
    """The synthetic id has to be real enough for exclude_id to work."""
    path = _write(
        tmp_path / "s.json",
        [_sold_run(p, "2026-08-1%d" % i) for i, p in enumerate([340.0, 345.0, 348.0, 350.0])],
    )
    observations = sold_comps.load(path)
    engine = comps.CompEngine(
        observations, min_comps_required=3, today=datetime(2026, 8, 15, tzinfo=timezone.utc)
    )

    match = engine.lookup(
        player="Caleb Williams",
        card_type="graded",
        price=340.0,
        grader="PSA",
        grade="10",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="301",
        exclude_id=observations[0]["id"],
    )

    assert match.stats.sample_size == len(observations) - 1


def test_sold_and_asking_observations_can_share_one_engine(tmp_path):
    """Sold comps are meant to be concatenated onto price_history's corpus,
    so a mixed bucket must not claim sold basis."""
    path = _write(tmp_path / "s.json", [_sold_run(348.0, "2026-08-12")])
    mixed = sold_comps.load(path) + [
        {
            "price": 500.0,
            "date": "2026-08-12",
            "id": "ebay-1",
            "player": "Caleb Williams",
            "card_type": "graded",
            "year": 2024,
            "set_name": "Prizm",
            "parallel": "Silver",
            "card_number": "301",
            "grader": "PSA",
            "grade": "10",
            "qualifier": None,
            "print_run": None,
            "basis": comps.BASIS_ASKING,
        },
        {
            "price": 520.0,
            "date": "2026-08-12",
            "id": "ebay-2",
            "player": "Caleb Williams",
            "card_type": "graded",
            "year": 2024,
            "set_name": "Prizm",
            "parallel": "Silver",
            "card_number": "301",
            "grader": "PSA",
            "grade": "10",
            "qualifier": None,
            "print_run": None,
            "basis": comps.BASIS_ASKING,
        },
    ]

    match = comps.CompEngine(
        mixed, min_comps_required=3, today=datetime(2026, 8, 15, tzinfo=timezone.utc)
    ).lookup(
        player="Caleb Williams",
        card_type="graded",
        price=299.0,
        grader="PSA",
        grade="10",
        year=2024,
        set_name="Prizm",
        parallel="Silver",
        card_number="301",
    )

    assert match.stats.basis == comps.BASIS_ASKING
    assert match.confidence != "high"


# -- the shipped config file ------------------------------------------------


def test_shipped_config_file_loads_and_is_empty():
    """The file in the repo must parse, and must not ship with fake sales."""
    assert sold_comps.load(sold_comps.DEFAULT_PATH) == []
    document = sold_comps.read_document(sold_comps.DEFAULT_PATH)
    assert document["sales"] == []
    assert "130point" in document["_comment"]
    assert sold_comps.validation_error(document["_example"]) is None


# -- scripts/add_sold_comp.py -----------------------------------------------


def _argv(path, **overrides):
    args = {
        "--player": "Caleb Williams",
        "--year": "2024",
        "--set": "Prizm",
        "--parallel": "Silver",
        "--card-number": "301",
        "--grader": "PSA",
        "--grade": "10",
        "--price": "348",
        "--date": "2026-08-15",
        "--source": "130point",
    }
    args.update(overrides)
    argv = []
    for flag, value in args.items():
        if value is None:
            continue
        argv += [flag, value]
    return argv + ["--path", str(path)]


def test_cli_appends_preserving_comments_and_existing_sales(tmp_path, capsys):
    path = _write(tmp_path / "s.json", [_sale(price=300.0, date="2026-07-01")])

    assert add_sold_comp.main(_argv(path)) == 0

    document = json.loads(path.read_text())
    assert document["_comment"] == "hand-edited"
    assert document["_example"] == {"player": "x"}
    assert [s["price"] for s in document["sales"]] == [300.0, 348.0]
    out = capsys.readouterr().out
    assert "Added" in out
    assert "2 sold comp(s)" in out
    assert len(sold_comps.load(path)) == 2


def test_cli_creates_the_file_when_missing(tmp_path):
    path = tmp_path / "nested" / "s.json"

    assert add_sold_comp.main(_argv(path)) == 0

    assert len(sold_comps.load(path)) == 1


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    path = tmp_path / "s.json"

    assert add_sold_comp.main(_argv(path) + ["--dry-run"]) == 0

    assert not path.exists()
    assert "dry run" in capsys.readouterr().out


def test_cli_refuses_a_sale_the_loader_would_reject(tmp_path, capsys):
    path = _write(tmp_path / "s.json", [])

    assert add_sold_comp.main(_argv(path, **{"--price": "-5"})) == 2

    assert "Refusing" in capsys.readouterr().err
    assert json.loads(path.read_text())["sales"] == []


def test_cli_refuses_a_grader_without_a_grade(tmp_path, capsys):
    path = _write(tmp_path / "s.json", [])

    assert add_sold_comp.main(_argv(path, **{"--grade": None})) == 2
    assert "grade" in capsys.readouterr().err


def test_cli_refuses_to_overwrite_a_corrupt_file(tmp_path, capsys):
    path = tmp_path / "s.json"
    path.write_text("{ half-typed")

    assert add_sold_comp.main(_argv(path)) == 2

    assert path.read_text() == "{ half-typed"  # hand-typed data is never clobbered
    assert "Refusing to write" in capsys.readouterr().err


def test_cli_written_entry_matches_the_id_the_loader_derives(tmp_path, capsys):
    path = tmp_path / "s.json"
    add_sold_comp.main(_argv(path))
    printed = capsys.readouterr().out

    loaded_id = sold_comps.load(path)[0]["id"]
    assert loaded_id in printed


def test_cli_omits_unsupplied_fields_rather_than_writing_null(tmp_path):
    path = tmp_path / "s.json"

    add_sold_comp.main(
        _argv(path, **{"--grader": None, "--grade": None, "--parallel": None, "--card-number": None})
    )

    sale = json.loads(path.read_text())["sales"][0]
    assert "grader" not in sale
    assert "parallel" not in sale
    assert sold_comps.load(path)[0]["card_type"] == "raw"


class TestFromTitle:
    """Eleven flags per entry is why config/sold_comps.json has been empty
    since the day it was created."""

    def _run(self, argv):
        from scripts import add_sold_comp

        return add_sold_comp.main(argv)

    def test_a_pasted_title_supplies_the_whole_identity(self):
        from scripts.add_sold_comp import identity_from_title

        read = identity_from_title("2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10")
        assert read == {
            "year": 2024, "set_name": "Prizm", "parallel": "Silver Prizm",
            "card_number": "301", "grader": "PSA", "grade": "10",
        }

    def test_what_it_could_not_read_is_absent_not_guessed(self):
        from scripts.add_sold_comp import identity_from_title

        read = identity_from_title("Caleb Williams rookie card")
        assert read == {}

    def test_it_keys_the_comp_the_way_the_engine_will(self, tmp_path, capsys):
        # The point is not the typing saved. A hand-typed "Silver" against an
        # extracted "Silver Prizm" is a comp that silently never matches
        # anything, which looks like progress and is worse than nothing.
        path = tmp_path / "sold.json"
        assert self._run([
            "--from-title", "2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10",
            "--price", "348", "--date", "2026-08-15", "--path", str(path),
        ]) == 0
        sale = json.loads(path.read_text())["sales"][0]
        assert sale["parallel"] == "Silver Prizm"
        assert sale["set_name"] == "Prizm"

    def test_an_explicit_flag_beats_the_parser(self, tmp_path):
        # You looked at the card; the parser only looked at the title.
        path = tmp_path / "sold.json"
        self._run([
            "--from-title", "2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10",
            "--parallel", "Silver Mojo", "--price", "348", "--date", "2026-08-15",
            "--path", str(path),
        ])
        assert json.loads(path.read_text())["sales"][0]["parallel"] == "Silver Mojo"

    def test_it_prints_what_it_read_before_writing(self, tmp_path, capsys):
        path = tmp_path / "sold.json"
        self._run([
            "--from-title", "2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10",
            "--price", "348", "--date", "2026-08-15", "--path", str(path),
        ])
        printed = capsys.readouterr().out
        assert "Read from the title:" in printed
        assert "Silver Prizm" in printed

    def test_a_title_with_no_watchlist_player_refuses_rather_than_guessing(self, tmp_path, capsys):
        path = tmp_path / "sold.json"
        assert self._run([
            "--from-title", "2024 Panini Prizm Some Unknown Person #301",
            "--price", "348", "--date", "2026-08-15", "--path", str(path),
        ]) == 2
        assert not path.exists()

    def test_player_and_price_still_work_without_a_title(self, tmp_path):
        path = tmp_path / "sold.json"
        assert self._run([
            "--player", "Caleb Williams", "--price", "348", "--date", "2026-08-15",
            "--path", str(path),
        ]) == 0


# -- scripts/add_sold_comp.py --paste ----------------------------------------


PASTED_PAGE = """\
2024 Panini Prizm Caleb Williams Silver Prizm RC #301 PSA 10
Pre-Owned
$344.00
+$5.99 shipping
Sold  Aug 15, 2026

2024 Prizm Caleb Williams Silver #301 PSA 10
Pre-Owned
$375.00
Free shipping
Sold  Jul 28, 2026
"""


class TestPasteMode:
    """Seeding one card costs one paste instead of one invocation per sale --
    which is the difference between the sold-comp store being populated and
    staying empty. It is also the one place a PARSER decides what a sale was,
    so nothing reaches the file until the user has seen every row."""

    def _identity(self, path):
        return [
            "--paste-file", None,  # filled in by callers
            "--player", "Caleb Williams",
            "--year", "2024",
            "--set", "Prizm",
            "--parallel", "Silver Prizm",
            "--card-number", "301",
            "--grader", "PSA",
            "--grade", "10",
            "--path", str(path),
        ]

    def _run(self, tmp_path, path, text=PASTED_PAGE, extra=()):
        source = tmp_path / "pasted.txt"
        source.write_text(text)
        argv = self._identity(path)
        argv[1] = str(source)
        return add_sold_comp.main(argv + list(extra))

    def test_preview_is_the_default_and_writes_nothing(self, tmp_path, capsys):
        path = tmp_path / "s.json"

        assert self._run(tmp_path, path) == 0

        assert not path.exists()
        printed = capsys.readouterr().out
        assert "Preview only" in printed
        assert "344.00" in printed and "375.00" in printed

    def test_confirm_writes_every_row_under_the_one_identity(self, tmp_path):
        path = tmp_path / "s.json"

        assert self._run(tmp_path, path, extra=["--confirm"]) == 0

        stored = sold_comps.load(path)
        assert [entry["price"] for entry in stored] == [344.00, 375.00]
        assert {entry["date"] for entry in stored} == {"2026-08-15", "2026-07-28"}
        assert {entry["player"] for entry in stored} == {"Caleb Williams"}

    def test_a_second_import_of_the_same_page_adds_nothing(self, tmp_path, capsys):
        """Re-pasting after adding one more sale to the page must not double
        every comp already banked -- the engine would read the duplicates as
        independent evidence and tighten its dispersion gate on them."""
        path = tmp_path / "s.json"
        self._run(tmp_path, path, extra=["--confirm"])

        assert self._run(tmp_path, path, extra=["--confirm"]) == 0

        assert len(sold_comps.load(path)) == 2
        assert "Nothing new to add." in capsys.readouterr().out

    def test_an_active_listing_page_is_refused_and_nothing_is_written(self, tmp_path, capsys):
        path = tmp_path / "s.json"

        assert self._run(
            tmp_path, path, text="Caleb Williams PSA 10\n$425.00\nBuy It Now\n", extra=["--confirm"]
        ) == 2

        assert not path.exists()
        assert "Refused to import" in capsys.readouterr().err

    def test_text_with_no_readable_pairs_says_so_rather_than_writing_nothing_quietly(
        self, tmp_path, capsys
    ):
        path = tmp_path / "s.json"

        assert self._run(tmp_path, path, text="Sold items\nno numbers here\n", extra=["--confirm"]) == 2

        assert not path.exists()
        assert "no date-and-price pair" in capsys.readouterr().err

    def test_price_and_date_are_refused_alongside_paste(self, tmp_path, capsys):
        """--price with --paste reads as "use this price", and it cannot mean
        that for a block of sales that each carry their own."""
        path = tmp_path / "s.json"

        assert self._run(tmp_path, path, extra=["--price", "348"]) == 2

        assert not path.exists()
        assert "--price and --date describe one sale" in capsys.readouterr().err

    def test_an_inferred_year_is_flagged_in_the_preview(self, tmp_path, capsys):
        path = tmp_path / "s.json"

        self._run(tmp_path, path, text="Sold  Aug 15\n$344.00\n")

        assert "YEAR ASSUMED" in capsys.readouterr().out

    def test_a_corrupt_file_is_never_overwritten(self, tmp_path, capsys):
        path = tmp_path / "s.json"
        path.write_text("{not json")

        assert self._run(tmp_path, path, extra=["--confirm"]) == 2

        assert path.read_text() == "{not json"
        assert "Refusing to write" in capsys.readouterr().err

    def test_existing_sales_survive_the_import(self, tmp_path):
        path = _write(tmp_path / "s.json", [_sale(price=300.0, date="2026-07-01")])

        assert self._run(tmp_path, path, extra=["--confirm"]) == 0

        document = json.loads(path.read_text())
        assert document["_comment"] == "hand-edited"
        assert [entry["price"] for entry in document["sales"]] == [300.0, 344.00, 375.00]

    def test_confirm_without_paste_is_refused_rather_than_ignored(self, tmp_path):
        path = tmp_path / "s.json"

        assert add_sold_comp.main(_argv(path) + ["--confirm"]) == 2

        assert not path.exists()

    def test_missing_price_without_paste_is_refused(self, tmp_path, capsys):
        path = tmp_path / "s.json"

        assert add_sold_comp.main(_argv(path, **{"--price": None})) == 2

        assert not path.exists()
        assert "--price" in capsys.readouterr().err
