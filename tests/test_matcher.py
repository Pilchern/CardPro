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
