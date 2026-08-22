from src import matcher

PLAYERS = ["Michael Jordan", "Walter Payton"]


def test_match_player_finds_full_name():
    assert matcher.match_player("1986 Fleer Michael Jordan Rookie PSA 9", PLAYERS) == "Michael Jordan"


def test_match_player_requires_both_name_parts():
    assert matcher.match_player("Michael card of some other guy", PLAYERS) is None


def test_match_player_no_match():
    assert matcher.match_player("Totally unrelated listing", PLAYERS) is None


def test_match_player_case_insensitive():
    assert matcher.match_player("michael jordan rookie", PLAYERS) == "Michael Jordan"


def test_detect_grading_psa():
    assert matcher.detect_grading("Michael Jordan PSA 9 rookie") == ("graded", "PSA", "9")


def test_detect_grading_half_grade():
    assert matcher.detect_grading("Walter Payton BGS 9.5") == ("graded", "BGS", "9.5")


def test_detect_grading_raw():
    assert matcher.detect_grading("Michael Jordan raw rookie card") == ("raw", None, None)


def test_detect_grading_sgc():
    assert matcher.detect_grading("Walter Payton SGC 10 rookie") == ("graded", "SGC", "10")


def test_detect_rookie_card_rc_abbreviation():
    assert matcher.detect_rookie_card("JOSH GIDDEY RC 2021-22 PANINI PRIZM") is True


def test_detect_rookie_card_full_word():
    assert matcher.detect_rookie_card("2024 Panini Donruss-Rookie Card Caleb Williams") is True


def test_detect_rookie_card_false_when_absent():
    assert matcher.detect_rookie_card("1996-97 Topps Michael Jordan #23") is False


def test_detect_rookie_card_case_insensitive():
    assert matcher.detect_rookie_card("2025 bowman chrome rc colston loveland") is True


def test_detect_rookie_card_does_not_match_substring():
    # "RC" inside another word (e.g. a set name) shouldn't false-positive
    assert matcher.detect_rookie_card("1996-97 Topps Chrome ARCHIVE Michael Jordan") is False


# --- Multi-player matching (audit: a dual auto is its own market) --------

DUAL_PLAYERS = ["Michael Jordan", "Walter Payton", "Caleb Williams"]


def test_match_players_returns_all_hits_in_watchlist_order():
    matched = matcher.match_players("Michael Jordan Walter Payton dual auto 1/1", DUAL_PLAYERS)
    assert matched == ["Michael Jordan", "Walter Payton"]


def test_match_players_single_hit():
    assert matcher.match_players("1986 Fleer Michael Jordan Rookie", DUAL_PLAYERS) == ["Michael Jordan"]


def test_match_players_no_hits_is_empty_list():
    assert matcher.match_players("Totally unrelated listing", DUAL_PLAYERS) == []


def test_match_player_still_returns_the_first_hit_only():
    # Existing callers must be unaffected by match_players.
    assert matcher.match_player("Walter Payton Michael Jordan dual", DUAL_PLAYERS) == "Michael Jordan"


# --- Richer grade detection ---------------------------------------------


def test_detect_grade_details_basic_psa():
    info = matcher.detect_grade_details("Michael Jordan PSA 9 rookie")
    assert (info.card_type, info.grader, info.grade) == ("graded", "PSA", "9")
    assert info.qualifier is None
    assert info.authentic_only is False


def test_detect_grade_details_no_space():
    info = matcher.detect_grade_details("Michael Jordan PSA10 rookie")
    assert (info.card_type, info.grader, info.grade) == ("graded", "PSA", "10")


def test_detect_grade_details_new_graders():
    for title, grader in [
        ("Connor Bedard CGC 9.5", "CGC"),
        ("Connor Bedard HGA 10", "HGA"),
        ("Connor Bedard TAG 9", "TAG"),
        ("Connor Bedard CSG 8.5", "CSG"),
        ("Connor Bedard SGC 10", "SGC"),
        ("Connor Bedard BGS 9.5", "BGS"),
    ]:
        assert matcher.detect_grade_details(title).grader == grader, title


def test_gem_mt_only_counts_with_a_grader_in_front():
    assert matcher.detect_grade_details("Michael Jordan PSA GEM MT 10").grade == "10"
    # No grader token -> seller hype, not a slab. Unknown, never a guess.
    assert matcher.detect_grade_details("Michael Jordan GEM MT 10 rookie").card_type == "raw"


def test_laundry_tag_is_not_the_tag_grader():
    info = matcher.detect_grade_details("2024 National Treasures laundry tag 1 of 1 Caleb Williams")
    assert info.card_type == "raw"
    assert info.grader is None


def test_grade_qualifier_captured():
    info = matcher.detect_grade_details("1986 Fleer Michael Jordan PSA 8 OC")
    assert (info.grader, info.grade, info.qualifier) == ("PSA", "8", "OC")


def test_all_grade_qualifiers_recognized():
    for qualifier in matcher.GRADE_QUALIFIERS:
        info = matcher.detect_grade_details(f"1986 Fleer Michael Jordan PSA 8 {qualifier}")
        assert info.qualifier == qualifier, qualifier


def test_lowercase_of_is_not_a_qualifier():
    # "PSA 9 of 12" is a lot count, not an off-centre qualifier.
    assert matcher.detect_grade_details("Michael Jordan PSA 9 of 12 cards").qualifier is None


def test_no_qualifier_on_a_clean_grade():
    assert matcher.detect_grade_details("Michael Jordan PSA 10 RC").qualifier is None


def test_authentic_only_slab():
    info = matcher.detect_grade_details("1986 Fleer Michael Jordan PSA Authentic")
    assert (info.card_type, info.grader, info.grade) == ("graded", "PSA", "AUTH")
    assert info.authentic_only is True


def test_authentic_only_slab_sgc():
    info = matcher.detect_grade_details("1986 Fleer Michael Jordan SGC Authentic")
    assert (info.card_type, info.grader, info.grade) == ("graded", "SGC", "AUTH")


def test_authentic_altered_without_a_grader_is_still_a_slab():
    info = matcher.detect_grade_details("1986 Fleer Michael Jordan Authentic Altered")
    assert (info.card_type, info.grade, info.authentic_only) == ("graded", "AUTH", True)


def test_bare_authentic_is_not_a_slab():
    # "100% authentic" is on half the raw listings on the site.
    info = matcher.detect_grade_details("1986 Fleer Michael Jordan 100% authentic guaranteed")
    assert info.card_type == "raw"
    assert info.grade is None


def test_numeric_grade_wins_over_authentic_wording():
    info = matcher.detect_grade_details("Michael Jordan PSA 10 authentic auto")
    assert (info.grade, info.authentic_only) == ("10", False)


def test_detect_grade_details_raw_is_all_unknown():
    info = matcher.detect_grade_details("Michael Jordan raw rookie card")
    assert (info.card_type, info.grader, info.grade, info.qualifier, info.authentic_only) == (
        "raw", None, None, None, False,
    )


def test_detect_grading_is_a_wrapper_and_unchanged():
    for title in [
        "Michael Jordan PSA 9 rookie",
        "Walter Payton BGS 9.5",
        "Michael Jordan raw rookie card",
        "Walter Payton SGC 10 rookie",
        "1986 Fleer Michael Jordan PSA 8 OC",
    ]:
        details = matcher.detect_grade_details(title)
        assert matcher.detect_grading(title) == (details.card_type, details.grader, details.grade), title
