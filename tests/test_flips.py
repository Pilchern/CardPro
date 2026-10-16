"""data/flips.csv -> sold comps, suppressed listings and a footer line."""
import logging

from src import comps, flips, observability
from src import main as main_module

HEADER = "date,item_id,action,price,ship,fees,grader,grade,title,note\n"
BUY_TITLE = "2024 Topps Chrome Pete Crow-Armstrong #16 Refractor Rookie"


def _csv(tmp_path, *lines):
    path = tmp_path / "flips.csv"
    path.write_text(HEADER + "".join(line + "\n" for line in lines))
    return path


def _rows(tmp_path):
    return flips.load(_csv(
        tmp_path,
        "2026-10-01,111,buy,32.50,4.50,0,,,{},".format(BUY_TITLE),
        "2026-10-11,111,sell,58.00,0.82,8.66,,,,",
        "2026-10-02,222,pass,40.00,,,,,,too dear",
    ))


def test_bad_rows_are_skipped_with_a_warning(tmp_path, caplog):
    path = _csv(tmp_path, "2026-10-01,111,keep,10,,,,,,", "not-a-date,111,buy,10,,,,,,", "2026-10-01,,buy,10,,,,,,")
    with caplog.at_level(logging.WARNING):
        assert flips.load(path) == []
    assert caplog.text.count("skipped") == 3


def test_a_missing_file_is_no_flips(tmp_path):
    assert flips.load(tmp_path / "none.csv") == []


def test_a_sold_flip_becomes_a_sold_comp_keyed_from_the_buy_title(tmp_path):
    [obs] = flips.sold_observations(_rows(tmp_path), ["Pete Crow-Armstrong"])
    assert obs["basis"] == comps.BASIS_SOLD and obs["source"] == "own-sale"
    assert (obs["player"], obs["year"], obs["set_name"], obs["parallel"], obs["card_number"]) == (
        "Pete Crow-Armstrong", 2024, "Topps Chrome", "Refractor", "16")
    assert (obs["price"], obs["date"]) == (58.0, "2026-10-11")


def test_a_sell_without_its_buy_is_not_a_comp(tmp_path):
    rows = flips.load(_csv(tmp_path, "2026-10-11,999,sell,58.00,0.82,8.66,,,,"))
    assert flips.sold_observations(rows, ["Pete Crow-Armstrong"]) == []


def test_bought_and_passed_cards_are_suppressed_unless_the_price_drops(tmp_path):
    seen = {}
    flips.mark_seen(_rows(tmp_path), seen, "2026-10-12")
    assert set(seen) == {flips.listing_id("111"), flips.listing_id("222")}
    from tests.test_main import EXACT_TITLE, make_listing

    same = make_listing(EXACT_TITLE, 40.0, listing_id=flips.listing_id("222"), listing_type="fixed_price")
    lower = make_listing(EXACT_TITLE, 30.0, listing_id=flips.listing_id("222"), listing_type="fixed_price")
    stats = observability.RunStats()
    assert main_module.apply_dedupe([same], dict(seen), "2026-10-12", stats) == []
    assert main_module.apply_dedupe([lower], dict(seen), "2026-10-12", stats) == [lower]


def test_footer_line_states_net_and_days(tmp_path):
    # 58.00 - 8.66 - 0.82 - 32.50 - 4.50 = 11.52; bought 10-01, sold 10-11.
    assert flips.summary(_rows(tmp_path)) == (
        "Flips: 3 logged (1 sold), realised net $11.52, median 10 days to sell."
    )


def test_no_rows_no_footer_line():
    assert flips.summary([]) is None


def test_the_committed_file_is_just_the_header():
    assert main_module.FLIPS_PATH.read_text() == HEADER
