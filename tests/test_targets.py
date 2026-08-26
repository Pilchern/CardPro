from src import targets

RAW = [
    {
        "label": "2024 Panini Prizm Caleb Williams Silver PSA 10",
        "player": "Caleb Williams",
        "year": 2024,
        "set_name": "Prizm",
        "parallel": "Silver",
        "grader": "PSA",
        "grade": "10",
        "buy_zone": 400,
        "great_buy": 350,
        "immediate_alert": 300,
    },
    {"label": "Any Bedard Young Guns", "player": "Connor Bedard", "set_name": "Young Guns", "buy_zone": 150},
    {"label": "broken entry with no player", "buy_zone": 10},
]

FULL_MATCH = dict(
    player="Caleb Williams",
    year=2024,
    set_name="Prizm",
    parallel="Silver",
    grader="PSA",
    grade="10",
    card_type="graded",
)


def _targets():
    return targets.load_targets(RAW)


def test_entries_without_a_player_are_skipped_not_fatal():
    loaded = _targets()
    assert [t.player for t in loaded] == ["Caleb Williams", "Connor Bedard"]


def test_price_bands_are_assigned_strongest_first():
    loaded = _targets()
    assert targets.best_hit(loaded, total_cost=290, **FULL_MATCH).band == targets.BAND_IMMEDIATE
    assert targets.best_hit(loaded, total_cost=340, **FULL_MATCH).band == targets.BAND_GREAT
    assert targets.best_hit(loaded, total_cost=399, **FULL_MATCH).band == targets.BAND_BUY_ZONE


def test_match_above_every_band_still_reports_the_hit():
    hit = targets.best_hit(_targets(), total_cost=900, **FULL_MATCH)
    assert hit is not None
    assert hit.band is None
    assert hit.in_buy_zone is False
    assert hit.label == "ABOVE BUY ZONE"


def test_wrong_parallel_is_never_a_target_hit():
    fields = dict(FULL_MATCH, parallel="Gold")
    assert targets.best_hit(_targets(), total_cost=100, **fields) is None


def test_wrong_grade_is_never_a_target_hit():
    fields = dict(FULL_MATCH, grade="9")
    assert targets.best_hit(_targets(), total_cost=100, **fields) is None


def test_unknown_field_does_not_satisfy_a_target():
    # The target asks for Silver; the listing's parallel wasn't identified.
    # Unknown must not count as a match -- being told "your target showed up"
    # and finding a different parallel is the exact failure this prevents.
    fields = dict(FULL_MATCH, parallel=None)
    assert targets.best_hit(_targets(), total_cost=100, **fields) is None


def test_target_only_matches_on_fields_it_specifies():
    # The Bedard target names no year, parallel, grader or grade, so a
    # listing that leaves those unknown still matches.
    hit = targets.best_hit(
        _targets(),
        total_cost=120,
        player="Connor Bedard",
        set_name="Young Guns",
        year=None,
        parallel=None,
        grader=None,
        grade=None,
        card_type="raw",
    )
    assert hit is not None
    assert hit.band == targets.BAND_BUY_ZONE


def test_different_player_never_matches():
    fields = dict(FULL_MATCH, player="Rome Odunze")
    assert targets.best_hit(_targets(), total_cost=100, **fields) is None


def test_unknown_total_cost_reports_a_hit_with_no_band():
    hit = targets.best_hit(_targets(), total_cost=None, **FULL_MATCH)
    assert hit is not None and hit.band is None


def test_matching_is_case_and_whitespace_insensitive():
    fields = dict(FULL_MATCH, player=" caleb williams ", parallel="silver", set_name="prizm")
    assert targets.best_hit(_targets(), total_cost=290, **fields).band == targets.BAND_IMMEDIATE


def _target():
    return targets.TargetCard(label="CW Prizm", player="Caleb Williams", buy_zone=150.0)


def test_an_unreadable_price_is_not_the_same_as_above_every_band():
    """They were the same value, so the report printed "above every price
    band you set" about a card whose price it did not know -- a price claim
    made out of an unknown."""
    unknown = targets.match_target(_target(), player="Caleb Williams", total_cost=None)
    above = targets.match_target(_target(), player="Caleb Williams", total_cost=9999.0)
    assert unknown.label != above.label
    assert unknown.label == "PRICE UNKNOWN"
    assert above.label == "ABOVE BUY ZONE"
    assert unknown.price_known is False and above.price_known is True


def test_a_priced_hit_is_still_price_known():
    hit = targets.match_target(_target(), player="Caleb Williams", total_cost=100.0)
    assert hit.price_known is True
    assert hit.in_buy_zone is True


def test_thresholds_are_inclusive_at_exactly_the_band():
    # The <= is correct today; nothing stopped a future < from silently
    # moving every band by a penny.
    target = targets.TargetCard(
        label="CW Prizm", player="Caleb Williams",
        buy_zone=400.0, great_buy=350.0, immediate_alert=300.0,
    )
    for total, expected in ((300.0, "immediate_alert"), (350.0, "great_buy"), (400.0, "buy_zone")):
        hit = targets.match_target(target, player="Caleb Williams", total_cost=total)
        assert hit.band == expected, total
    assert targets.match_target(target, player="Caleb Williams", total_cost=400.01).band is None




def test_bands_written_out_of_order_are_refused_rather_than_mislabelled():
    """match_target walks the bands strongest-first and takes the first
    threshold the cost clears. With the ladder inverted, a $250 card came
    back IMMEDIATE -- the strongest band there is -- at a price the same
    target's own buy zone calls too dear. A typo in a personal config file
    produced a confident wrong label, which is the one outcome this project
    ranks below an error."""
    loaded = targets.load_targets([{
        "label": "Inverted", "player": "Caleb Williams",
        "buy_zone": 100.0, "great_buy": 200.0, "immediate_alert": 300.0,
    }])
    assert loaded == []


def test_a_correct_ladder_loads():
    loaded = targets.load_targets([{
        "label": "Sane", "player": "Caleb Williams",
        "buy_zone": 400.0, "great_buy": 350.0, "immediate_alert": 300.0,
    }])
    assert [t.label for t in loaded] == ["Sane"]


def test_equal_thresholds_are_a_ladder():
    # Setting two bands to the same number is a legitimate way to say "these
    # two mean the same thing to me", not a typo.
    loaded = targets.load_targets([{
        "player": "Caleb Williams", "buy_zone": 300.0, "great_buy": 300.0,
    }])
    assert len(loaded) == 1


def test_a_partial_ladder_is_fine():
    # Leaving bands out is normal. Only the ones that are set take part.
    loaded = targets.load_targets([
        {"player": "Caleb Williams", "buy_zone": 400.0},
        {"player": "Connor Bedard", "immediate_alert": 50.0, "buy_zone": 400.0},
    ])
    assert len(loaded) == 2


def test_an_entry_with_no_player_is_still_skipped(caplog):
    with caplog.at_level("WARNING"):
        assert targets.load_targets([{"buy_zone": 100.0}]) == []
    assert "no player" in caplog.text
