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
