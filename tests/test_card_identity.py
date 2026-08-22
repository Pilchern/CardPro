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
        ("2024 Donruss Caleb Williams Rated Rookie #301", "Rated Rookie"),
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
