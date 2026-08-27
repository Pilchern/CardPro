import pytest

from src import card_identity


def test_extracts_year_manufacturer_set_parallel_card_number_serial():
    identity = card_identity.extract_card_identity(
        "2024 Panini Prizm Silver Caleb Williams #123 RC 23/99 PSA 10"
    )
    assert identity.year.value == 2024
    assert identity.year.confidence == "high"
    assert identity.manufacturer.value == "Panini"
    assert identity.set_name.value == "Prizm"
    assert identity.parallel.value == "Silver"
    assert identity.card_number.value == "123"
    assert identity.serial_number.value == "23/99"


def test_longer_set_keyword_wins_over_shorter_substring():
    identity = card_identity.extract_card_identity("2024 Donruss Optic Caleb Williams Rated Rookie")
    assert identity.set_name.value == "Donruss Optic"


def test_unknown_fields_are_none_not_guessed():
    identity = card_identity.extract_card_identity("Michael Jordan card")
    assert identity.year.value is None
    assert identity.year.confidence == "none"
    assert identity.manufacturer.value is None
    assert identity.set_name.value is None
    assert identity.parallel.value is None
    assert identity.card_number.value is None
    assert identity.serial_number.value is None


def test_autograph_keyword_detected():
    identity = card_identity.extract_card_identity("2024 Panini Contenders Caleb Williams Autograph RC")
    assert identity.is_autograph.value is True
    assert identity.is_autograph.confidence == "high"


def test_autograph_false_when_absent():
    identity = card_identity.extract_card_identity("1996-97 Topps Michael Jordan #23")
    assert identity.is_autograph.value is False


def test_memorabilia_keyword_detected():
    identity = card_identity.extract_card_identity("2024 Panini National Treasures Patch Auto Caleb Williams")
    assert identity.is_memorabilia.value is True


def test_lot_of_n_detected_high_confidence():
    identity = card_identity.extract_card_identity("Lot of 5 Michael Jordan cards")
    assert identity.is_lot.value is True
    assert identity.is_lot.confidence == "high"


def test_n_card_lot_detected_high_confidence():
    identity = card_identity.extract_card_identity("Michael Jordan 10 card lot vintage")
    assert identity.is_lot.value is True
    assert identity.is_lot.confidence == "high"


def test_bare_lot_keyword_detected_medium_confidence():
    identity = card_identity.extract_card_identity("Michael Jordan lot vintage cards")
    assert identity.is_lot.value is True
    assert identity.is_lot.confidence == "medium"


def test_not_a_lot_when_keyword_absent():
    identity = card_identity.extract_card_identity("1986 Fleer Michael Jordan Rookie PSA 9")
    assert identity.is_lot.value is False


def test_serial_number_not_confused_with_card_number():
    identity = card_identity.extract_card_identity("2024 Prizm Caleb Williams #201 15/25")
    assert identity.card_number.value == "201"
    assert identity.serial_number.value == "15/25"


# --- Audit regression tests (docs/CARDPRO_2_AUDIT.md failure mode #2, #6,
# and the honourable mentions). Every title below is verbatim from the
# audit, and every one of them extracted something wrong before this pass.


def test_team_name_is_not_a_parallel_white_sox():
    identity = card_identity.extract_card_identity(
        "2024 Topps Chrome Kyle Teel Chicago White Sox RC #150"
    )
    assert identity.parallel.value is None
    assert identity.parallel.confidence == "none"
    # masking must not eat the rest of the identity
    assert identity.set_name.value == "Topps Chrome"
    assert identity.card_number.value == "150"


def test_team_name_is_not_a_parallel_blue_jays():
    identity = card_identity.extract_card_identity("1993 Upper Deck Blue Jays Team Card Joe Carter")
    assert identity.parallel.value is None
    assert identity.manufacturer.value == "Upper Deck"


def test_team_name_is_not_a_parallel_red_sox():
    identity = card_identity.extract_card_identity("2023 Bowman Red Sox Prospect Auto")
    assert identity.parallel.value is None
    assert identity.is_autograph.value is True


def test_award_name_is_not_a_parallel_gold_glove():
    identity = card_identity.extract_card_identity("Michael Jordan Gold Glove Award unsigned photo card")
    assert identity.parallel.value is None


def test_masked_phrases_cover_every_listed_team_and_award():
    # Each mask phrase must actually suppress a parallel on its own.
    for phrase in card_identity.TEAM_AND_PHRASE_MASKS:
        identity = card_identity.extract_card_identity(f"2024 Topps {phrase} Some Player")
        assert identity.parallel.value is None, phrase


def test_bare_color_is_medium_confidence_not_high():
    identity = card_identity.extract_card_identity("2024 Panini Prizm Caleb Williams Blue #14")
    assert identity.parallel.value == "Blue"
    assert identity.parallel.confidence == "medium"


def test_compound_parallel_is_high_confidence():
    identity = card_identity.extract_card_identity("2024 Panini Prizm Rome Odunze Orange Ice /249 SSP")
    assert identity.parallel.value == "Orange Ice"
    assert identity.parallel.confidence == "high"
    assert identity.print_run.value == 249
    assert identity.serial_number.value is None


def test_color_adjacent_to_qualifier_becomes_the_compound():
    identity = card_identity.extract_card_identity("2022 Topps Chrome Julio Rodriguez Green Refractor /99")
    assert identity.parallel.value == "Green Refractor"
    assert identity.parallel.confidence == "high"


def test_color_adjacent_to_wave_becomes_the_compound():
    identity = card_identity.extract_card_identity("2023 Topps Chrome Gold Wave Corbin Carroll")
    assert identity.parallel.value == "Gold Wave"
    assert identity.parallel.confidence == "high"


def test_unambiguous_single_word_parallel_is_high_confidence():
    identity = card_identity.extract_card_identity("2024 Bowman Chrome Superfractor Jackson Holliday 1/1")
    assert identity.parallel.value == "Superfractor"
    assert identity.parallel.confidence == "high"


def test_draft_pick_idiom_is_not_a_card_number_and_team_is_not_a_parallel():
    identity = card_identity.extract_card_identity(
        "Pete Crow-Armstrong 2025 Topps #1 Draft Pick Green Wave /99"
    )
    assert identity.card_number.value is None
    assert identity.parallel.value is None
    assert identity.print_run.value == 99
    assert identity.serial_number.value is None


def test_card_number_idioms_rejected():
    for title in [
        "2024 Topps #1 Overall Pick Caleb Williams",
        "Caleb Williams #1 Pick 2024",
        "Jackson Holliday #1 Prospect in baseball",
        "Michael Jordan #1 Fan club card",
        "Michael Jordan card #2 of 10",
        "2024 Topps Caleb Williams #23 jersey number",
    ]:
        assert card_identity.extract_card_identity(title).card_number.value is None, title


def test_alphanumeric_card_numbers_still_supported():
    assert card_identity.extract_card_identity("2024 Bowman Chrome #BDC-25 Auto").card_number.value == "BDC-25"
    assert card_identity.extract_card_identity("2023 Topps Update #US150 RC").card_number.value == "US150"
    assert card_identity.extract_card_identity("2024 Panini #RC-12 Rated Rookie").card_number.value == "RC-12"


def test_card_number_falls_through_idiom_to_the_real_number():
    identity = card_identity.extract_card_identity("2025 Topps #1 Draft Pick Caleb Williams #150")
    assert identity.card_number.value == "150"


def test_bare_print_run_with_space():
    identity = card_identity.extract_card_identity("2024 Panini Select Caleb Williams Tie-Dye / 25")
    assert identity.print_run.value == 25
    assert identity.serial_number.value is None


def test_serial_number_sets_both_serial_and_print_run():
    identity = card_identity.extract_card_identity("2024 Panini Prizm Caleb Williams 23/99")
    assert identity.serial_number.value == "23/99"
    assert identity.print_run.value == 99


def test_grade_fraction_is_not_a_print_run():
    identity = card_identity.extract_card_identity("2021 Bowman Chrome Auto centering 9.5/10 sharp")
    assert identity.print_run.value is None
    assert identity.serial_number.value is None


def test_date_fraction_is_not_a_print_run():
    identity = card_identity.extract_card_identity("2024 Topps Now Aaron Judge sold 7/14/2024")
    assert identity.print_run.value is None
    assert identity.serial_number.value is None


def test_print_run_denominator_is_sanity_bounded():
    assert card_identity.extract_card_identity("2024 Topps card /0").print_run.value is None
    assert card_identity.extract_card_identity("2024 Topps card /999999").print_run.value is None


def test_season_is_kept_alongside_year():
    identity = card_identity.extract_card_identity("Connor Bedard 2023-24 Upper Deck Young Guns #451")
    assert identity.season.value == "2023-24"
    assert identity.season.confidence == "high"
    assert identity.year.value == 2023  # unchanged for existing callers
    assert identity.set_name.value == "Young Guns"
    assert identity.card_number.value == "451"


def test_season_absent_on_single_year_titles():
    identity = card_identity.extract_card_identity("1986 Fleer Michael Jordan #57")
    assert identity.season.value is None
    assert identity.season.confidence == "none"


def test_new_set_keywords_extracted():
    for title, expected in [
        # "Rated Rookie" is a Donruss ROOKIE DESIGNATION, printed across
        # Donruss, Optic, Score and Elite alike -- reading it as a set pooled
        # four products into one comp bucket. The set here is the flagship.
        ("2024 Donruss Caleb Williams Rated Rookie #301", "Donruss"),
        ("2024 Bowman Chrome Draft Jackson Holliday", "Bowman Chrome Draft"),
        ("2023 Topps Allen & Ginter Julio Rodriguez", "Allen & Ginter"),
        ("2024 Topps Gypsy Queen Bobby Witt Jr", "Gypsy Queen"),
        ("2024 Panini Instant Caleb Williams", "Panini Instant"),
        # longest keyword still wins, so this one deliberately has no
        # longer set phrase competing with it
        ("2023 Topps Sapphire Corbin Carroll", "Sapphire"),
    ]:
        assert card_identity.extract_card_identity(title).set_name.value == expected, title


# --- Negative signals ---------------------------------------------------


def test_reprint_detected():
    identity = card_identity.extract_card_identity("1986 Fleer Michael Jordan #57 REPRINT")
    assert "reprint" in identity.negative_signals.value
    assert identity.negative_signals.confidence == "high"
    assert card_identity.is_excluded_from_deals(identity) is True


def test_reprint_hyphenated_and_lowercase():
    assert "reprint" in card_identity.extract_card_identity("Jordan RE-PRINT card").negative_signals.value
    assert "reprint" in card_identity.extract_card_identity("Jordan reprint card").negative_signals.value


def test_rp_is_not_a_reprint():
    # "RP" is Rookie Patch in this hobby, not reprint -- a false positive
    # here would silently hide the most valuable rookie cards.
    identity = card_identity.extract_card_identity("2024 National Treasures RP Auto Caleb Williams")
    assert "reprint" not in identity.negative_signals.value


def test_replica_and_reproduction_detected():
    assert "replica" in card_identity.extract_card_identity("1952 Topps Mantle replica").negative_signals.value
    assert "replica" in card_identity.extract_card_identity("Mantle reproduction card").negative_signals.value


def test_custom_and_art_card_detected():
    assert "custom" in card_identity.extract_card_identity("Michael Jordan custom card").negative_signals.value
    assert "custom" in card_identity.extract_card_identity("Jordan ACEO art card").negative_signals.value
    assert "custom" in card_identity.extract_card_identity("Jordan fantasy card novelty").negative_signals.value


def test_digital_detected():
    assert "digital" in card_identity.extract_card_identity("Topps Now Digital Aaron Judge").negative_signals.value
    assert "digital" in card_identity.extract_card_identity("Aaron Judge NFT card").negative_signals.value


def test_facsimile_auto_detected():
    for title in [
        "Michael Jordan facsimile signature card",
        "Jordan stamped signature card",
        "Jordan printed autograph rookie",
        "Jordan pre-print auto",
    ]:
        assert "facsimile_auto" in card_identity.extract_card_identity(title).negative_signals.value, title


def test_sealed_product_detected():
    for title in [
        "2024 Topps Series 1 Hobby Box",
        "2024 Panini Prizm blaster box",
        "2024 Topps mega box sealed",
        "1990 Topps factory set",
        "2024 Topps hanger box",
        "1986 Topps wax pack unopened",
        "2024 Topps pack of 12",
        "2024 Prizm case of 12",
    ]:
        assert "sealed_product" in card_identity.extract_card_identity(title).negative_signals.value, title


def test_pack_fresh_single_card_is_not_sealed_product():
    # "Pack fresh" describes the condition of ONE raw card. Calling it a
    # sealed product would suppress exactly the listings we want.
    identity = card_identity.extract_card_identity("Pack fresh 2024 Topps Chrome Aaron Judge #100")
    assert "sealed_product" not in identity.negative_signals.value
    assert card_identity.is_excluded_from_deals(identity) is False


def test_bare_case_is_not_sealed_product():
    # A "case" in a single-card listing is almost always the holder.
    identity = card_identity.extract_card_identity("1986 Fleer Michael Jordan in acrylic case")
    assert "sealed_product" not in identity.negative_signals.value


def test_break_slot_detected():
    for title in [
        "2024 Bowman team break Braves spot",
        "PYT break 2024 Topps Chrome",
        "Pick your team random team break",
        "2024 Prizm case break slot",
    ]:
        assert "break_slot" in card_identity.extract_card_identity(title).negative_signals.value, title


def test_fast_break_parallel_is_not_a_break_slot():
    # "Fast Break" is a Panini parallel; the generic word "break" alone must
    # never trip the break-slot signal.
    identity = card_identity.extract_card_identity("2023 Panini Prizm Fast Break Caleb Williams")
    assert "break_slot" not in identity.negative_signals.value
    assert identity.parallel.value == "Fast Break"


def test_pick_your_card_detected():
    for title in [
        "2024 Topps Chrome You Pick your card",
        "1986 Fleer U Pick singles",
        "2024 Panini Prizm your choice",
        "2024 Topps choose any 3",
    ]:
        assert "pick_your_card" in card_identity.extract_card_identity(title).negative_signals.value, title


def test_lot_emits_the_lot_signal():
    identity = card_identity.extract_card_identity("Lot of 5 Michael Jordan cards")
    assert "lot" in identity.negative_signals.value
    assert card_identity.is_excluded_from_deals(identity) is True


def test_damaged_detected_but_not_a_hard_block():
    # A creased card is still a real card -- surface it as a risk, never
    # silently drop it.
    identity = card_identity.extract_card_identity("1986 Fleer Michael Jordan #57 creased poor condition")
    assert "damaged" in identity.negative_signals.value
    assert card_identity.is_excluded_from_deals(identity) is False


def test_authentic_altered_is_a_damaged_signal():
    identity = card_identity.extract_card_identity("1986 Fleer Michael Jordan PSA Authentic Altered")
    assert "damaged" in identity.negative_signals.value


def test_negative_signals_empty_and_unconfident_on_a_clean_title():
    identity = card_identity.extract_card_identity("2024 Panini Prizm Caleb Williams #123 RC")
    assert identity.negative_signals.value == ()
    assert identity.negative_signals.confidence == "none"
    assert card_identity.is_excluded_from_deals(identity) is False


def test_negative_signals_are_canonical_and_ordered():
    identity = card_identity.extract_card_identity("REPRINT custom art card lot of 5 damaged")
    assert identity.negative_signals.value == ("reprint", "custom", "lot", "damaged")
    for signal in identity.negative_signals.value:
        assert signal in card_identity.NEGATIVE_SIGNAL_VOCABULARY


def test_every_signal_has_a_report_label():
    assert set(card_identity.NEGATIVE_SIGNAL_LABELS) == set(card_identity.NEGATIVE_SIGNAL_VOCABULARY)


def test_hard_blocks_exclude_damaged_only():
    assert card_identity.HARD_BLOCK_SIGNALS == set(card_identity.NEGATIVE_SIGNAL_VOCABULARY) - {"damaged"}


# --- Patch --------------------------------------------------------------


def test_patch_is_flagged_separately_from_memorabilia():
    identity = card_identity.extract_card_identity("2024 National Treasures Caleb Williams Patch Auto /99")
    assert identity.is_patch.value is True
    assert identity.is_memorabilia.value is True


def test_rpa_counts_as_patch_and_memorabilia():
    identity = card_identity.extract_card_identity("2024 Panini Prizm Caleb Williams RPA /25")
    assert identity.is_patch.value is True
    assert identity.is_memorabilia.value is True


def test_plain_relic_is_memorabilia_but_not_patch():
    identity = card_identity.extract_card_identity("2024 Panini Prizm Caleb Williams Jersey Relic")
    assert identity.is_memorabilia.value is True
    assert identity.is_patch.value is False


# --- The never-guess rule ------------------------------------------------


def test_unmatchable_title_yields_unknown_everywhere_not_a_guess():
    identity = card_identity.extract_card_identity("nice card of a guy, ships fast")
    assert identity.year.value is None
    assert identity.season.value is None
    assert identity.manufacturer.value is None
    assert identity.set_name.value is None
    assert identity.parallel.value is None
    assert identity.card_number.value is None
    assert identity.serial_number.value is None
    assert identity.print_run.value is None
    assert identity.negative_signals.value == ()
    for field in (
        identity.year, identity.season, identity.manufacturer, identity.set_name,
        identity.parallel, identity.card_number, identity.serial_number, identity.print_run,
    ):
        assert field.confidence == "none"


def test_empty_title_is_all_unknown():
    identity = card_identity.extract_card_identity("")
    assert identity.parallel.value is None
    assert identity.print_run.value is None
    assert identity.is_lot.value is False
    assert card_identity.is_excluded_from_deals(identity) is False


# ---------------------------------------------------------------------------
# Wrong values, not missing ones
#
# Every case below shipped a CONFIDENTLY WRONG answer, which this module's
# own docstring says is worse than no answer: a wrong parallel or card number
# does not merely fail to find a comp, it keys a bucket that pools cards
# which are not the same card.
# ---------------------------------------------------------------------------

extract = card_identity.extract_card_identity


class TestParallelRuns:
    def test_red_white_and_blue_survives_seller_punctuation(self):
        # Was "Blue Prizm" at high confidence: the compound missed on the
        # ampersand, and the colour-plus-qualifier rule then grabbed the tail
        # of the phrase.
        for title in [
            "2024 Panini Prizm Caleb Williams Red White & Blue Prizm #301",
            "2024 Panini Prizm Caleb Williams Red, White & Blue Prizm #301",
            "2024 Panini Prizm Caleb Williams Red White and Blue Prizm #301",
        ]:
            identity = extract(title)
            assert identity.parallel.value == "Red White Blue", title
            assert identity.parallel.confidence == "high"

    def test_a_named_refractor_is_not_the_base_refractor(self):
        # Was "Refractor" for all three, because the old rule picked the
        # longest vocabulary word anywhere in the title. An Aqua Lava /199
        # then keyed the same bucket as a base refractor.
        for title, expected in [
            ("2024 Topps Chrome PCA Aqua Lava Refractor /199 #150", "Aqua Lava Refractor"),
            ("2024 Topps Chrome PCA Sepia Refractor #150", "Sepia Refractor"),
            ("2024 Topps Chrome PCA Raywave Refractor #150", "Raywave Refractor"),
        ]:
            assert extract(title).parallel.value == expected, title

    def test_a_plain_refractor_is_still_a_plain_refractor(self):
        assert extract("2024 Topps Chrome Caleb Williams Refractor #150").parallel.value == "Refractor"

    def test_a_run_must_be_adjacent(self):
        # A colour at one end of a title must never join a parallel word at
        # the other end just because both appear somewhere.
        identity = extract("2024 Topps Chrome Gold Rush Caleb Williams RC Refractor #150")
        assert identity.parallel.value == "Refractor"

    def test_the_set_word_is_not_swallowed_into_the_parallel(self):
        # "Prizm" doubles as a set name, which is why the run vocabulary
        # excludes the qualifier words. Otherwise this reads "Prizm Silver".
        identity = extract("2024 Panini Prizm Silver Caleb Williams #123 RC 23/99 PSA 10")
        assert identity.parallel.value == "Silver"
        assert identity.parallel.confidence == "medium"

    def test_a_colour_only_run_stays_medium(self):
        identity = extract("2024 Panini Prizm Caleb Williams Blue #14")
        assert (identity.parallel.value, identity.parallel.confidence) == ("Blue", "medium")


class TestCollectorsChoice:
    def test_it_is_a_set_not_the_choice_parallel(self):
        identity = extract("2024-25 Upper Deck Collector's Choice Connor Bedard #201")
        assert identity.parallel.value is None
        assert identity.set_name.value == "Collector's Choice"

    def test_both_spellings_give_one_name(self):
        # Two names for one set is two comp buckets, each half as deep.
        assert extract("2024-25 Upper Deck Collectors Choice Connor Bedard").set_name.value == (
            extract("2024-25 Upper Deck Collector's Choice Connor Bedard").set_name.value
        )


class TestBareChromeIsResolvedByBrand:
    def test_topps_and_bowman_chrome_are_different_products(self):
        assert extract("2026 Chrome Kyle Teel Refractor RA-KT").set_name.value == "Chrome"
        assert extract("2026 Topps Chrome Kyle Teel Refractor").set_name.value == "Topps Chrome"
        assert extract("2025 Bowman Chrome Caleb Wilson Refractor").set_name.value == "Bowman Chrome"

    def test_a_brand_that_makes_no_chrome_line_leaves_the_name_alone(self):
        # Resolving is allowed to replace a name the vocabulary found. It is
        # never allowed to invent a product.
        assert extract("2024 Panini Chronicles Chrome Caleb Williams").set_name.value == "Chronicles"

    def test_a_longer_chrome_product_is_not_collapsed(self):
        assert extract("2026 Topps Chrome Update Kyle Teel").set_name.value == "Topps Chrome Update"


class TestCardNumberIsNotSomethingElse:
    def test_a_hashed_serial_is_not_a_card_number(self):
        # "#25/99" was producing card_number "25" -- an exact key for a card
        # that does not exist, and two Golds with different serials got two
        # different phantom buckets.
        identity = extract("2024 Panini Prizm Caleb Williams Gold #25/99")
        assert identity.card_number.value is None
        assert identity.serial_number.value == "25/99"
        assert identity.print_run.value == 99

    def test_a_one_of_one_is_not_card_number_one(self):
        assert extract("2024 Bowman Chrome Superfractor Caleb Williams #1/1").card_number.value is None

    def test_a_separated_serial_still_leaves_the_card_number_alone(self):
        identity = extract("2024 Panini Prizm Caleb Williams #201 15/25")
        assert (identity.card_number.value, identity.serial_number.value) == ("201", "15/25")

    def test_a_relic_description_does_not_suppress_a_real_card_number(self):
        # "jersey" used to suppress unconditionally, throwing away the number
        # on exactly the kind of listing worth valuing.
        assert extract("2024 Panini Prizm Caleb Williams #23 Jersey Relic /99").card_number.value == "23"

    def test_a_shirt_number_is_still_suppressed(self):
        assert extract("2024 Topps Chrome Caleb Williams #23 jersey number").card_number.value is None

    def test_the_draft_pick_idiom_is_still_suppressed(self):
        assert extract("2024 Panini Prizm Caleb Williams #1 Draft Pick").card_number.value is None


class TestManufacturer:
    def test_the_brand_wins_over_the_sub_brand(self):
        # Longest-first returned "Donruss" (7) over "Panini" (6). eBay titles
        # put the manufacturer first, so position beats length here.
        assert extract("2024 Panini Donruss Optic Caleb Williams #301").manufacturer.value == "Panini"

    def test_a_sub_brand_alone_is_still_the_manufacturer(self):
        assert extract("2024 Donruss Optic Caleb Williams Rated Rookie").manufacturer.value == "Donruss"

    def test_the_set_is_unaffected(self):
        assert extract("2024 Panini Donruss Optic Caleb Williams #301").set_name.value == "Donruss Optic"


class TestSetVocabulary:
    def test_2023_2026_products_resolve(self):
        for title, expected in [
            ("2024 Panini Zenith Caleb Williams RC #101", "Zenith"),
            ("2024-25 Panini Court Kings Matas Buzelis #101", "Court Kings"),
            ("2024 Leaf Metal Draft Caleb Williams Auto", "Leaf Metal Draft"),
            ("2024-25 Upper Deck SP Authentic Connor Bedard", "SP Authentic"),
            ("2024 Topps Cosmic Chrome Pete Crow-Armstrong", "Cosmic Chrome"),
            ("2024 Topps Fire Caleb Williams", "Topps Fire"),
            ("2024-25 O-Pee-Chee Connor Bedard #201", "O-Pee-Chee"),
            ("2024 Bowman Draft 1st Chrome Auto Konnor Griffin BDC-100", "Bowman Draft"),
        ]:
            assert extract(title).set_name.value == expected, title

    def test_a_brand_word_alone_never_becomes_a_set(self):
        # _find_keyword is longest-first, so listing "Panini" as a set would
        # beat "Prizm" and pool every Panini product a player has into one
        # bucket. That is the audit's original failure mode, rebuilt.
        assert extract("2024 Panini Prizm Caleb Williams #301").set_name.value == "Prizm"
        assert extract("2024 Topps Chrome Caleb Williams #150").set_name.value == "Topps Chrome"
        assert extract("2024 Topps Series 1 #150 Caleb Williams RC").set_name.value == "Topps Series 1"

    def test_a_sapphire_edition_is_not_flagship_chrome(self):
        # Sapphire trades at several times base Chrome; merging them would
        # make every base Chrome card look underpriced.
        assert extract("2024 Topps Chrome Sapphire Edition PCA #150").set_name.value == (
            "Topps Chrome Sapphire"
        )


class TestCardNumbersWithoutAHash:
    def test_the_spelled_out_forms(self):
        assert extract("2024 Topps Chrome Caleb Williams RC Card No. 150").card_number.value == "150"
        assert extract("2024 Panini Prizm Caleb Williams RC card number 301").card_number.value == "301"

    def test_a_hobby_prefixed_number(self):
        # The prefix is printed on the card -- reading it is not an inference.
        assert extract("2024 Bowman Draft Chrome Auto Konnor Griffin BDC-100").card_number.value == "BDC-100"
        assert extract("2025 Bowman Chrome Caleb Wilson Refractor BCP-83").card_number.value == "BCP-83"
        assert extract("2024 Topps Update Pete Crow-Armstrong US150").card_number.value == "US150"

    def test_a_bare_trailing_integer_is_still_not_a_card_number(self):
        # In an eBay title a bare integer is a jersey number, a lot count, a
        # grade or a year fragment as often as it is a card number.
        assert extract("Caleb Williams 2024 Panini Prizm RC 301").card_number.value is None

    def test_the_hashed_form_still_wins_and_its_idioms_still_lose(self):
        assert extract("2024 Panini Prizm Caleb Williams #301 PSA 10").card_number.value == "301"
        assert extract("Michael Jordan card #2 of 10").card_number.value is None


class TestIsBase:
    """"This is the base card" and "we could not read the parallel" are
    different facts, and parallel=None was both of them. Splitting them apart
    is the largest available gain in comp coverage -- and the easiest way to
    rebuild the pooling failure the engine exists to prevent, so the guard
    errs toward unknown everywhere it can."""

    def test_a_plain_card_from_a_known_set_is_base(self):
        assert extract("2024 Panini Prizm Caleb Williams #301 RC").is_base.value is True

    def test_a_card_with_a_parallel_is_not_base(self):
        identity = extract("2024 Panini Prizm Caleb Williams Silver Prizm #301")
        assert identity.is_base.value is False
        assert identity.is_base.confidence == "high"

    def test_a_numbered_card_is_never_base(self):
        # The parallels that would badly distort a base median are almost all
        # serial-numbered, which is why this is the guard that matters most.
        assert extract("2024 Panini Prizm Caleb Williams #301 RC /99").is_base.value is None
        assert extract("2024 Panini Prizm Caleb Williams #301 RC 23/99").is_base.value is None

    def test_a_short_print_token_blocks_the_assertion(self):
        for title in [
            "2024 Panini Prizm Caleb Williams #301 SSP",
            "2024 Panini Prizm Caleb Williams #301 SP",
            "2024 Panini Prizm Caleb Williams #301 Variation",
            "2024 Bowman Chrome 1st Caleb Wilson BCP-83",
        ]:
            assert extract(title).is_base.value is None, title

    def test_an_unknown_set_cannot_be_base(self):
        # Base of WHAT. Without a set there is no base card to be.
        assert extract("Caleb Williams rookie card").is_base.value is None

    def test_a_truncated_title_is_never_base(self):
        # The parallel may be in the part eBay cut off.
        assert extract("2024 Panini Prizm Caleb Williams RC #301 Mint Cond…").is_base.value is None
        assert extract("2024 Panini Prizm Caleb Williams RC #301 Mint Cond...").is_base.value is None

    def test_asserting_base_is_never_high_confidence(self):
        # It is a closed-world inference, not something the title states.
        assert extract("2024 Panini Prizm Caleb Williams #301 RC").is_base.confidence == "medium"


class TestFlagshipBaseSets:
    """For six brands the set IS the brand -- "2024 Topps", "1986 Fleer".
    Those titles were the largest single class of set_name=None, and the set
    name is the first missing field for 66% of the corpus. The whole reason
    this is a guarded fallback rather than six new SET_KEYWORDS entries is in
    test_the_brand_word_still_never_outranks_a_named_product."""

    def test_the_brand_word_becomes_the_set_when_nothing_else_does(self):
        for title, expected in [
            ("2024 Topps #150 Caleb Williams RC", "Topps"),
            ("2024 Panini Donruss Caleb Williams #301 RC", "Donruss"),
            ("2024 Upper Deck Connor Bedard #201", "Upper Deck"),
            ("1986 Fleer Michael Jordan #57 RC PSA 8", "Fleer"),
        ]:
            assert extract(title).set_name.value == expected, title

    def test_the_sub_brand_wins_even_though_panini_is_the_manufacturer(self):
        # "2024 Panini Donruss" is the commonest way that set is titled, and
        # manufacturer resolves to Panini there because the leftmost brand
        # word wins. Requiring the flagship word to BE the manufacturer would
        # refuse exactly the spelling this case exists for.
        identity = extract("2024 Panini Donruss Caleb Williams #301 RC")
        assert identity.manufacturer.value == "Panini"
        assert identity.set_name.value == "Donruss"

    def test_the_flagship_is_never_high_confidence(self):
        # The title states a brand. That the brand word is also this card's
        # set is our inference, not something the seller wrote.
        assert extract("2024 Topps #150 Caleb Williams RC").set_name.confidence == "medium"

    def test_the_brand_word_still_never_outranks_a_named_product(self):
        # THE TRAP. _find_keyword is longest-first, so "Panini" (6 letters)
        # beats "Prizm" (5): a vocabulary entry would pool every Panini
        # product a player has into one bucket, which is the audit's "$1.25
        # base card is 95% under market" failure rebuilt. The flagship only
        # runs after the ordinary lookup has already come back empty.
        for title, expected in [
            ("2024 Panini Prizm Caleb Williams #301", "Prizm"),
            ("2024 Topps Chrome Caleb Williams #150", "Topps Chrome"),
            ("2024 Panini Donruss Optic Caleb Williams #301", "Donruss Optic"),
            ("2025 Bowman Chrome Caleb Wilson #BCP-83 Refractor", "Bowman Chrome"),
            ("2024 Topps Fire Caleb Williams #150", "Topps Fire"),
        ]:
            assert extract(title).set_name.value == expected, title

    def test_an_unrecognised_product_word_blocks_the_flagship(self):
        # "Topps Fire" is a real, separate product at a different price, and
        # it is in SET_KEYWORDS -- so the case that matters is a product we
        # have never heard of. One unrecognised word where a first name would
        # go, or three where a name would be two, and the assertion is off.
        for title in [
            "2024 Topps Cornerstone Caleb Williams #150",
            "2024 Topps Cornerstone #150 Caleb Williams",
            "2024 Topps Caleb Williams Cornerstone Edition #150",
            "2020 Topps 206 Caleb Williams #150",
        ]:
            assert extract(title).set_name.value is None, title

    def test_a_leftover_word_from_a_product_name_blocks_it_too(self):
        # The seller typed "Topps Museum" and the vocabulary wants "Museum
        # Collection", so the lookup misses. Waving "Museum" through as a
        # familiar word would file a $200 Museum Collection card as flagship
        # base Topps.
        assert extract("2024 Topps Museum Caleb Williams #150").set_name.value is None

    def test_a_truncated_title_never_gets_the_flagship(self):
        # The part eBay cut off is exactly where the product name would be.
        assert extract("2024 Topps Caleb Williams RC #301 Mint Cond…").set_name.value is None
        assert extract("2024 Topps Caleb Williams RC #301 Mint Cond...").set_name.value is None

    def test_a_title_with_no_manufacturer_gets_no_set(self):
        assert extract("Caleb Williams 2024 #301 RC").set_name.value is None
        assert extract("Connor Bedard rookie #201").set_name.value is None

    def test_a_brand_that_names_no_set_is_not_a_flagship(self):
        # Panini's name is on every product it makes and names none of them.
        # "2024 Panini Caleb Williams" states a company, not a card.
        assert extract("2024 Panini Caleb Williams #301 RC").set_name.value is None

    def test_a_stated_series_is_used_as_stated_not_inferred(self):
        # Series 1 / Series 2 / Update in the title is a fact, not a guess,
        # and the vocabulary already carries every spelling.
        for title, expected in [
            ("2024 Topps Series 1 #150 Caleb Williams RC Chicago Bears", "Topps Series 1"),
            ("2024 Topps Series One #150 Caleb Williams", "Topps Series 1"),
            ("2024 Topps Series 2 #401 Caleb Williams", "Topps Series 2"),
            ("2024 Topps Update Series Aaron Judge #US150", "Topps Update"),
            ("2024-25 Upper Deck Series 1 Connor Bedard #201", "Upper Deck Series 1"),
        ]:
            identity = extract(title)
            assert identity.set_name.value == expected, title
            assert identity.set_name.confidence == "high", title

    def test_a_printed_number_prefix_names_the_line(self):
        # The prefix is printed on the card, so reading US150 as Update is
        # transcription, not inference -- the same reason
        # PREFIXED_CARD_NUMBER_RE is allowed to trust these at all.
        for title, expected in [
            ("2024 Topps Pete Crow-Armstrong US150", "Topps Update"),
            ("2024 Bowman Konnor Griffin BDC-100 Auto", "Bowman Chrome Draft"),
            ("2025 Bowman Caleb Wilson BCP-83", "Bowman Chrome Prospects"),
            ("2024 Bowman Josue Briceno BDP-25", "Bowman Draft"),
        ]:
            assert extract(title).set_name.value == expected, title

    def test_a_card_number_is_never_read_as_a_series(self):
        # #1-350 is Series 1 and #351-700 is Series 2 in SOME years -- the
        # boundary moves with the set size. Inferring the series from the
        # number is a guess wearing a rule's clothes, and it would split one
        # player-year's base cards across two buckets on nothing.
        for title in ["2024 Topps #150 Caleb Williams", "2024 Topps #401 Caleb Williams"]:
            assert extract(title).set_name.value == "Topps", title

    def test_the_flagship_needs_a_card_number(self):
        # "Topps" is Series 1, Series 2 and Update within one year -- three
        # cards at three prices. The card number is the printed thing that
        # tells them apart, so the flagship is not asserted without it. It
        # costs little: with no card number a listing can reach at most the
        # context-only same_set level, which cannot flag a deal anyway.
        assert extract("2024 Topps Caleb Williams RC").set_name.value is None
        assert extract("2024 Topps Caleb Williams RC #150").set_name.value == "Topps"

    def test_without_that_requirement_the_same_title_would_resolve(self):
        # Documents what FLAGSHIP_REQUIRES_CARD_NUMBER is actually buying, so
        # the tradeoff is visible if anyone flips it.
        title = "2024 Topps Caleb Williams RC"
        masked = card_identity.mask_for_set_lookup(title)
        assert card_identity._extract_flagship_set(title, masked, "Topps", None).value is None
        assert card_identity._extract_flagship_set(title, masked, "Topps", "150").value == "Topps"

    def test_the_flagship_makes_more_listings_eligible_to_be_base(self):
        # The compound effect worth watching: _extract_is_base requires a
        # known set, so resolving the flagship turns listings that were
        # is_base=unknown into is_base=True. Both facts stay at medium and
        # neither is high.
        identity = extract("2024 Topps #150 Caleb Williams RC")
        assert identity.is_base.value is True
        assert identity.is_base.confidence == "medium"
        assert identity.set_name.confidence == "medium"

    def test_a_flagship_card_with_a_parallel_is_still_not_base(self):
        identity = extract("2024 Topps Gold Caleb Williams #150")
        assert identity.set_name.value == "Topps"
        assert identity.is_base.value is False

    def test_an_inferred_set_says_so_in_its_source(self):
        # Every number traces to a rule and a data point (principle 3): a
        # reader can tell a set we read from one we inferred.
        assert extract("2024 Topps #150 Caleb Williams RC").set_name.source == "title:flagship"
        assert extract("2024 Panini Prizm Caleb Williams #301").set_name.source == "title"

    def test_every_flagship_brand_is_a_known_manufacturer(self):
        for brand in card_identity.FLAGSHIP_MANUFACTURERS:
            assert brand in card_identity.MANUFACTURERS, brand

    def test_every_prefix_mapping_names_a_set_in_the_vocabulary(self):
        # Two names for one product is two comp buckets, each half as deep.
        for name in card_identity.FLAGSHIP_NUMBER_PREFIX_SETS.values():
            assert name in card_identity.SET_KEYWORDS, name


class TestPlayerSurnamesAreNotParallels:
    """A great many players have a colour for a surname, and nothing
    downstream gates a parallel on its confidence -- main passes the value
    straight into the comp lookup and the exact/same_card keys use it
    verbatim. So a phantom parallel is not a cosmetic error, it keys a
    bucket."""

    def test_a_colour_surname_before_a_real_parallel(self):
        for title, expected in [
            ("2023-24 Panini Prizm Jalen Green Gold Prizm #10 Rockets /10", "Gold Prizm"),
            ("2019-20 Panini Prizm Coby White Gold Prizm RC /10 Bulls", "Gold Prizm"),
            ("2019-20 Panini Prizm Coby White Red Wave RC Bulls", "Red Wave"),
            ("2022 Panini Prizm Draymond Green Green Prizm #45 Warriors", "Green Prizm"),
        ]:
            assert extract(title).parallel.value == expected, title

    def test_the_named_refractors_still_resolve(self):
        # The reordering that fixed the surnames must not undo the fix that
        # stopped a /199 parallel keying the base refractor bucket.
        for title, expected in [
            ("2024 Topps Chrome PCA Aqua Lava Refractor /199 #150", "Aqua Lava Refractor"),
            ("2024 Topps Chrome PCA Sepia Refractor #150", "Sepia Refractor"),
            ("2022 Topps Chrome Julio Rodriguez Green Refractor /99", "Green Refractor"),
        ]:
            assert extract(title).parallel.value == expected, title


class TestMultiWordParallelsAreReachable:
    """UNAMBIGUOUS_PARALLELS is consulted one word at a time by the
    adjacency run, so a two-word entry in it could never match anything --
    "Aqua Vapor Refractor" came out as plain "Refractor" at high confidence,
    the exact pooling the entry was added to prevent. They belong in
    COMPOUND_PARALLELS, which matches whole phrases."""

    def test_they_resolve_now(self):
        for title, expected in [
            ("2024 Topps Chrome Aqua Vapor Refractor Paul Skenes RC /199", "Aqua Vapor"),
            ("2024 Topps Chrome Dragon Scale Refractor Bobby Witt Jr /75", "Dragon Scale"),
            ("2023 Topps Chrome Corbin Carroll Rose Gold /50 RC", "Rose Gold"),
            ("2024 Donruss Caleb Williams Press Proof #301", "Press Proof"),
        ]:
            assert extract(title).parallel.value == expected, title

    def test_none_of_them_is_left_in_the_single_word_list(self):
        assert not any(" " in word for word in card_identity.UNAMBIGUOUS_PARALLELS)


class TestSetsThatWereCollidingOnOneCompKey:
    def test_topps_gold_label_is_its_own_set(self):
        # It was resolving to flagship "Topps" with parallel "Gold", putting
        # a Gold Label base card and a flagship Topps Gold parallel of the
        # same player, year and number into ONE exact bucket -- the level
        # allowed to declare a deal.
        label = extract("2022 Topps Gold Label Class 1 Aaron Judge #45 PSA 10")
        flagship = extract("2022 Topps Aaron Judge #45 Gold PSA 10")
        assert label.set_name.value == "Topps Gold Label"
        assert label.set_name.value != flagship.set_name.value

    def test_rated_rookie_is_a_designation_not_a_set(self):
        # Printed across Donruss, Optic, Score and Elite alike; reading it as
        # a set pooled four products into one bucket.
        assert extract("2024 Donruss Optic Caleb Williams Rated Rookie #301").set_name.value == (
            "Donruss Optic"
        )
        assert extract("2024 Score Caleb Williams Rated Rookie #301").set_name.value == "Score"
        assert extract("2024 Donruss Caleb Williams Rated Rookie #301").set_name.value == "Donruss"

    def test_a_players_name_is_not_a_parallel(self):
        assert extract("Tiger Woods 2001 Upper Deck #1 Rookie").parallel.value is None


class TestNegativeSignalsBlockTheBaseAssertion:
    def test_a_reprint_is_never_the_base_card(self):
        # The worst possible value for this field: its whole purpose is to
        # key a bucket of base copies, and a reprint in one would drag its
        # median to a fraction of the real card's.
        assert extract("1986 Fleer Michael Jordan #57 Reprint RP Bulls").is_base.value is None

    def test_a_lot_is_not_a_base_card_either(self):
        assert extract("2024 Panini Prizm Caleb Williams #301 RC lot of 3").is_base.value is None

    def test_a_named_foil_parallel_is_not_base(self):
        # These were coming back True: their modifier was absent from the
        # vocabulary, and the guard's safety argument -- that the parallels
        # which would distort a base median are almost all serial-numbered
        # -- does not hold for them.
        for title in [
            "2024 Topps Series 1 Shohei Ohtani #17 Rainbow Foil Dodgers",
            "2024 Topps Chrome Logofractor Shohei Ohtani #150",
        ]:
            assert extract(title).is_base.value is False, title

    def test_an_ordinary_base_card_is_unaffected(self):
        assert extract("2024 Panini Prizm Caleb Williams #301 RC").is_base.value is True


# ---------------------------------------------------------------------------
# eBay's cut
# ---------------------------------------------------------------------------


class TestFieldsThatRunIntoTheCut:
    """Measured on the live corpus: twenty stored rows held a value that ends
    exactly where eBay truncated the title. Each was written into the 180-day
    comp corpus as a known field, and card_number and parallel are the keys
    for the only two comp levels allowed to declare a deal.
    """

    @pytest.mark.parametrize("title,field", [
        ("Caleb Williams 2024 Score #40…", "card_number"),
        ("Kyle Teel 2025 Bowman Chrome Prospects #B...", "card_number"),
        ("2022-23 Flux JOSH GIDDEY #EA...", "card_number"),
        ("Matas Buzelis /249 Rookie Red…", "parallel"),
        ("Luther Burden III Blue Hyper...", "parallel"),
        ("KYLE TEEL 2026 TOPPS CHROME...", "set_name"),
    ])
    def test_a_value_ending_at_the_cut_is_unknown(self, title, field):
        identity = card_identity.extract_card_identity(title)

        assert getattr(identity, field).value is None
        assert getattr(identity, field).confidence == "none"

    def test_a_colour_one_word_from_the_cut_is_unknown_too(self):
        """"White S..." is a White Sox card, and the whole word "White" reads
        as a parallel. Team masking exists to catch exactly this, and the cut
        walks straight past the masking -- so a parallel needs a complete
        word between it and the cut, not just its own last letter."""
        whole = card_identity.extract_card_identity("Munetaka Murakami RC White Sox")
        cut = card_identity.extract_card_identity("Munetaka Murakami RC White S...")

        assert whole.parallel.value is None
        assert cut.parallel.value is None

    def test_a_print_run_ending_at_the_cut_is_unknown(self):
        """"/2…" is /250. Read as 2 it is not merely wrong, it is scored as
        the scarcest thing the ranking knows about."""
        identity = card_identity.extract_card_identity("Caleb Williams 2022 Bowman Chrome /2…")

        assert identity.print_run.value is None
        assert identity.serial_number.value is None

    def test_the_slash_survives_the_cut_even_though_the_number_does_not(self):
        """"This card is serial numbered" is readable off "/2…" and "this
        card is one of 2" is not. The browse sections show a card for what it
        IS, so losing the first to a correction about the second would trade
        a certainty for nothing."""
        identity = card_identity.extract_card_identity("Caleb Williams 2022 Bowman Chrome /2…")

        assert identity.is_serial_numbered.value is True

    def test_a_whole_title_is_not_second_guessed(self):
        identity = card_identity.extract_card_identity(
            "2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10"
        )

        assert identity.set_name.value == "Prizm"
        assert identity.parallel.value == "Silver Prizm"
        assert identity.card_number.value == "301"

    def test_a_field_the_cut_did_not_reach_survives(self):
        """The rule is about the fragment, not about the title being cut --
        blanket-dropping every field on a truncated title would throw away
        the 98% of readable evidence that arrives that way."""
        identity = card_identity.extract_card_identity(
            "2024 Topps Chrome Kyle Teel Refractor #150 PSA 9 Gem Mi..."
        )

        assert identity.set_name.value == "Topps Chrome"
        assert identity.parallel.value == "Refractor"
        assert identity.card_number.value == "150"

    def test_a_set_two_tokens_from_the_cut_survives(self):
        """A card number between the set and the cut proves the set was
        finished. Only the parallel needs the wider berth."""
        identity = card_identity.extract_card_identity(
            "Kyle Teel 2025 Bowman Chrome Prospects #B..."
        )

        assert identity.set_name.value == "Bowman Chrome Prospects"


class TestSerialNumbersBorrowedFromCardNumbers:
    def test_a_hyphenated_card_number_does_not_become_the_copy_number(self):
        """"BCP-83 /150" has no copy number in it. It was read as copy 83 of
        150, and the report then printed "(83/150)" on a card whose title
        says no such thing."""
        identity = card_identity.extract_card_identity(
            "2024 Bowman Chrome Colson Montgomery 1st Auto BCP-83 /150"
        )

        assert identity.card_number.value == "BCP-83"
        assert identity.serial_number.value is None
        assert identity.print_run.value == 150

    @pytest.mark.parametrize("title", [
        "2024 Topps Chrome Kyle Teel Gold 23/99 PSA 10",
        "2024 Prizm Silver #23/99",
    ])
    def test_a_real_serial_still_parses(self, title):
        identity = card_identity.extract_card_identity(title)

        assert identity.serial_number.value == "23/99"
        assert identity.print_run.value == 99
