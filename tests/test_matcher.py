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
