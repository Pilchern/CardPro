import pytest

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


class TestNamePartsMustBeAdjacent:
    """Testing each part of a name independently anywhere in the title reads
    a THIRD person out of two. The cheap-card-attributed-to-a-legend
    direction is the dangerous one: it is exactly the shape that becomes a
    DEALS headline, and it puts the ask in that legend's comp bucket."""

    WATCHLIST = ["Michael Jordan", "Frank Thomas", "Caleb Williams", "Caleb Wilson",
                 "Luther Burden", "Pete Crow-Armstrong", "Scottie Pippen"]

    @pytest.mark.parametrize("title", [
        "2024 Panini Prizm Jordan Love RC Michael Penix Jr Dual Rookies",
        "1998 Upper Deck Michael Finley / Jordan Clarkson",
        "2022 Topps Frank Schwindel RC Thomas Hatch Cubs Team Set",
        "1970 Topps Isiah Thomas Frank Robinson Combo",
    ])
    def test_two_other_peoples_names_do_not_add_up_to_a_watchlist_player(self, title):
        assert matcher.match_players(title, self.WATCHLIST) == []

    def test_a_real_card_is_not_discarded_as_a_multi_player_lot(self):
        """Both watchlist Calebs matched any title containing a Wilson and a
        Williams, and main.py drops a listing that matches two players. A
        genuine Caleb Wilson card was being thrown away because someone
        called Williams was on the same card."""
        title = "2025 Bowman Caleb Wilson RC Jameson Williams Refractor Dual"

        assert matcher.match_players(title, self.WATCHLIST) == ["Caleb Wilson"]

    @pytest.mark.parametrize("title,expected", [
        ("2024 Panini Prizm Caleb Williams Silver #301 PSA 10", ["Caleb Williams"]),
        ("MICHAEL JORDAN 1986 FLEER #57 PSA 9", ["Michael Jordan"]),
        ("Michael-Jordan 1986 Fleer", ["Michael Jordan"]),
        ("Michael Jordan's 1986 Fleer Rookie", ["Michael Jordan"]),
        ("Michael  Jordan 1986 Fleer", ["Michael Jordan"]),
        ("Luther Burden III 2025 Prizm", ["Luther Burden"]),
        ("Pete Crow-Armstrong 2024 Topps", ["Pete Crow-Armstrong"]),
    ])
    def test_the_spellings_sellers_actually_use_still_match(self, title, expected):
        assert matcher.match_players(title, self.WATCHLIST) == expected

    def test_a_reversed_name_needs_its_comma(self):
        """"Jordan, Michael" is a real notation and keeps working. Bare
        reversed adjacency does not, because that is how "Isiah Thomas Frank
        Robinson" would become a Frank Thomas card."""
        assert matcher.match_players("Jordan, Michael 1986 Fleer", self.WATCHLIST) == [
            "Michael Jordan"
        ]

    def test_a_genuine_dual_still_matches_both(self):
        title = "2024 Topps Michael Jordan / Scottie Pippen Dual Auto"

        assert matcher.match_players(title, self.WATCHLIST) == [
            "Michael Jordan", "Scottie Pippen",
        ]


class TestAGradeIsNotASerialNumber:
    @pytest.mark.parametrize("title", [
        "Walter Payton Game Used Patch Tag 1/1 Bears",
        "Michael Jordan Nameplate Tag 1/1 Jersey Relic",
        "Caleb Williams Shoe Tag 1/1 Rookie Patch Auto",
    ])
    def test_tag_one_of_one_is_a_print_run_not_a_tag_1(self, title):
        """Reading "TAG 1/1" as a TAG 1 comps a patch card against the worst
        slabs on the market -- the exact harm the false-friend list was
        written to prevent, arriving through a shape it did not cover."""
        info = matcher.detect_grade_details(title)

        assert info.card_type == "raw"
        assert info.grade is None

    def test_a_hyphen_no_longer_defeats_the_false_friend_guard(self):
        """The guard read exactly one space-delimited token, so
        "laundry-tag" was a single token matching nothing in the list -- on
        the very phrase the list exists to catch."""
        info = matcher.detect_grade_details("Michael Jordan laundry-tag TAG 9")

        assert info.card_type == "raw"

    def test_a_real_tag_slab_still_reads(self):
        info = matcher.detect_grade_details("Michael Jordan TAG 9 slab")

        assert (info.card_type, info.grader, info.grade) == ("graded", "TAG", "9")

    def test_a_graded_card_numbered_to_99_keeps_its_grade(self):
        """"PSA 9 /99" is a PSA 9 of a card numbered to 99. The space is what
        separates it from "TAG 1/1"."""
        info = matcher.detect_grade_details("Caleb Williams PSA 9 /99 Silver")

        assert (info.grader, info.grade) == ("PSA", "9")
