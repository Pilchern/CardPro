import pytest

from src.reasons import (
    ALL_CATEGORIES,
    ALL_REASONS,
    REASON_CATEGORIES,
    REASON_LABELS,
    Reason,
    RejectionLog,
    UnknownReasonError,
    category,
    label,
    validate,
)

# The reasons the pipeline is contractually required to be able to name.
# Hard-coded on purpose: if someone deletes one, this list -- not the
# module under test -- is the thing that says it used to exist.
REQUIRED_REASONS = [
    "no_price", "no_player_match", "lot", "reprint", "replica", "custom_card",
    "digital_card", "facsimile_auto", "sealed_product", "break_slot",
    "pick_your_card", "multi_player_card", "no_comp_at_any_level", "thin_sample",
    "stale_comps", "dispersed_comps", "context_only_level",
    "below_discount_threshold", "below_min_savings",
    "auction_current_bid_not_a_price", "auction_bid_exceeds_max_rational_bid",
    "identity_uncertain", "grade_uncertain", "shipping_unknown",
    "already_reported", "price_not_dropped", "seller_risk", "damaged_condition",
]


# --- the vocabulary itself -------------------------------------------------

def test_every_required_reason_exists():
    for reason in REQUIRED_REASONS:
        assert reason in ALL_REASONS, "missing required reason: " + reason


def test_every_reason_has_a_label_and_a_category():
    for reason in ALL_REASONS:
        assert REASON_LABELS[reason].strip(), reason
        assert REASON_CATEGORIES[reason] in ALL_CATEGORIES, reason


def test_labels_and_categories_cover_exactly_the_same_reasons():
    assert set(REASON_LABELS) == set(REASON_CATEGORIES)


def test_reason_values_are_lowercase_snake_case():
    # These strings get persisted to JSON and counted, so the shape is a
    # contract, not a style preference.
    for reason in ALL_REASONS:
        assert reason == reason.lower()
        assert " " not in reason
        assert "-" not in reason


def test_reason_class_constants_match_the_registry():
    constants = {
        value for name, value in vars(Reason).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    assert constants == set(ALL_REASONS)


def test_all_six_categories_are_used():
    assert set(REASON_CATEGORIES.values()) == set(ALL_CATEGORIES)


def test_labels_are_distinct():
    # Two reasons sharing a label would produce two identical footer lines
    # and make the count impossible to act on.
    assert len(set(REASON_LABELS.values())) == len(REASON_LABELS)


def test_specific_labels_read_like_english():
    assert REASON_LABELS[Reason.NO_COMP_AT_ANY_LEVEL] == "no comparable sales at any level"
    assert REASON_LABELS[Reason.STALE_COMPS] == (
        "comps are stale (newest is older than the freshness window)"
    )
    assert REASON_LABELS[Reason.AUCTION_CURRENT_BID_NOT_A_PRICE] == (
        "current bid, not a sale price -- an auction's bid is not what it will close at"
    )


def test_category_assignments_are_the_expected_ones():
    assert category(Reason.NO_PLAYER_MATCH) == "identity"
    assert category(Reason.STALE_COMPS) == "data_quality"
    assert category(Reason.BELOW_MIN_SAVINGS) == "economics"
    assert category(Reason.AUCTION_BID_EXCEEDS_MAX_RATIONAL_BID) == "auction"
    assert category(Reason.REPRINT) == "policy"
    assert category(Reason.PRICE_NOT_DROPPED) == "dedupe"


# --- helpers ---------------------------------------------------------------

def test_validate_returns_the_reason_so_it_can_be_used_inline():
    assert validate(Reason.LOT) == "lot"


def test_validate_raises_on_unknown_reason():
    with pytest.raises(UnknownReasonError):
        validate("no_comps")  # plausible typo for no_comp_at_any_level


def test_unknown_reason_error_is_a_value_error():
    assert issubclass(UnknownReasonError, ValueError)


def test_label_and_category_raise_on_unknown_reason():
    with pytest.raises(UnknownReasonError):
        label("totally_made_up")
    with pytest.raises(UnknownReasonError):
        category("totally_made_up")


def test_error_message_names_the_offending_reason():
    with pytest.raises(UnknownReasonError) as excinfo:
        validate("stale_comp")
    assert "stale_comp" in str(excinfo.value)


# --- RejectionLog ----------------------------------------------------------

def test_empty_log_is_empty_but_usable():
    log = RejectionLog()
    assert log.counts() == {}
    assert log.counts_by_category() == {}
    assert log.total() == 0
    assert log.summary_lines() == []


def test_record_counts_reasons():
    log = RejectionLog()
    log.record(Reason.NO_COMP_AT_ANY_LEVEL)
    log.record(Reason.NO_COMP_AT_ANY_LEVEL)
    log.record(Reason.REPRINT)
    assert log.counts() == {"no_comp_at_any_level": 2, "reprint": 1}
    assert log.total() == 3


def test_counts_are_sorted_by_count_descending():
    log = RejectionLog()
    log.record(Reason.REPRINT)
    for _ in range(4):
        log.record(Reason.THIN_SAMPLE)
    for _ in range(2):
        log.record(Reason.LOT)
    assert list(log.counts()) == ["thin_sample", "lot", "reprint"]


def test_ties_break_alphabetically_so_the_email_is_diffable():
    log = RejectionLog()
    log.record(Reason.THIN_SAMPLE)
    log.record(Reason.LOT)
    log.record(Reason.DIGITAL_CARD)
    assert list(log.counts()) == ["digital_card", "lot", "thin_sample"]


def test_record_rejects_an_unknown_reason():
    # The whole point of the module: a typo'd reason must explode, not
    # quietly increment a counter nobody prints.
    log = RejectionLog()
    with pytest.raises(UnknownReasonError):
        log.record("no_comps_found", listing_id="123")
    assert log.total() == 0
    assert log.counts() == {}


def test_record_many_rejects_an_unknown_reason_before_counting_anything():
    log = RejectionLog()
    with pytest.raises(UnknownReasonError):
        log.record_many("nope", ["a", "b"])
    assert log.total() == 0


def test_listing_ids_are_kept_in_order_when_supplied():
    log = RejectionLog()
    log.record(Reason.REPRINT, listing_id="1")
    log.record(Reason.REPRINT, listing_id="2")
    log.record(Reason.LOT, listing_id="3")
    assert log.listing_ids(Reason.REPRINT) == ["1", "2"]
    assert log.listing_ids(Reason.LOT) == ["3"]


def test_listing_id_is_optional_and_counting_still_works():
    log = RejectionLog()
    log.record(Reason.REPRINT)
    log.record(Reason.REPRINT, listing_id="2")
    assert log.counts() == {"reprint": 2}
    assert log.listing_ids(Reason.REPRINT) == ["2"]


def test_listing_ids_returns_a_copy():
    log = RejectionLog()
    log.record(Reason.LOT, listing_id="1")
    log.listing_ids(Reason.LOT).append("tampered")
    assert log.listing_ids(Reason.LOT) == ["1"]


def test_listing_ids_of_unrecorded_reason_is_empty():
    assert RejectionLog().listing_ids(Reason.SELLER_RISK) == []


def test_listing_ids_raises_on_unknown_reason():
    with pytest.raises(UnknownReasonError):
        RejectionLog().listing_ids("made_up")


def test_record_many_counts_one_per_id():
    log = RejectionLog()
    log.record_many(Reason.NO_PRICE, ["a", "b", "c"])
    assert log.counts() == {"no_price": 3}
    assert log.listing_ids(Reason.NO_PRICE) == ["a", "b", "c"]


def test_summary_lines_use_labels_and_counts():
    log = RejectionLog()
    for _ in range(12):
        log.record(Reason.NO_COMP_AT_ANY_LEVEL)
    log.record(Reason.REPRINT)
    assert log.summary_lines() == [
        "12 x no comparable sales at any level",
        "1 x a reprint, not the original card",
    ]


def test_counts_by_category_groups_and_sorts():
    log = RejectionLog()
    log.record(Reason.REPRINT)
    log.record(Reason.LOT)
    log.record(Reason.DIGITAL_CARD)
    log.record(Reason.THIN_SAMPLE)
    log.record(Reason.STALE_COMPS)
    log.record(Reason.PRICE_NOT_DROPPED)
    assert log.counts_by_category() == {"policy": 3, "data_quality": 2, "dedupe": 1}


def test_two_logs_do_not_share_state():
    first = RejectionLog()
    second = RejectionLog()
    first.record(Reason.LOT, listing_id="1")
    assert second.total() == 0
    assert second.listing_ids(Reason.LOT) == []


def test_every_reason_can_be_recorded_and_summarised():
    log = RejectionLog()
    for reason in ALL_REASONS:
        log.record(reason)
    assert log.total() == len(ALL_REASONS)
    assert len(log.summary_lines()) == len(ALL_REASONS)
