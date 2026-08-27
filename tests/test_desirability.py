from src import card_identity, desirability, matcher
from src.models import Listing


def listing_for(title, price=5.0, **overrides):
    """Built the way main._build_listing builds one, so these tests exercise
    the real extractors rather than a hand-written stand-in."""
    identity = card_identity.extract_card_identity(title)
    grade = matcher.detect_grade_details(title)
    fields = dict(
        id="L1",
        source="ebay-alert",
        title=title,
        price=price,
        url="https://example.test/1",
        player="Caleb Williams",
        card_type=grade.card_type,
        grader=grade.grader,
        grade=grade.grade,
        card_identity=identity,
        is_rookie_card=matcher.detect_rookie_card(title),
    )
    fields.update(overrides)
    listing = Listing(**fields)
    # main.evaluate_listings fills this in before anything reads it, and it
    # is deliberately left empty for a listing the pipeline rejected -- so a
    # fixture that skips it is testing a listing that cannot exist.
    if "desirable_attributes" not in overrides:
        listing.desirable_attributes = desirability.attributes_of(listing)
    return listing


class TestAttributes:
    def test_rookie_detected(self):
        assert desirability.ROOKIE in desirability.attributes_of(listing_for("2024 Topps Caleb Williams RC"))

    def test_autograph_detected(self):
        attrs = desirability.attributes_of(listing_for("2024 Panini Contenders Caleb Williams Autograph"))
        assert desirability.AUTOGRAPH in attrs

    def test_serial_numbering_detected_from_a_bare_print_run(self):
        attrs = desirability.attributes_of(listing_for("2024 Panini Select Caleb Williams Tie-Dye /25"))
        assert desirability.SERIAL_NUMBERED in attrs

    def test_parallel_detected(self):
        attrs = desirability.attributes_of(listing_for("2024 Panini Prizm Caleb Williams Orange Ice /249"))
        assert desirability.PARALLEL in attrs

    def test_graded_detected(self):
        attrs = desirability.attributes_of(listing_for("2024 Prizm Caleb Williams #301 PSA 10"))
        assert desirability.GRADED in attrs

    def test_patch_preferred_over_generic_memorabilia(self):
        attrs = desirability.attributes_of(listing_for("2024 National Treasures Caleb Williams Patch"))
        assert desirability.PATCH in attrs
        assert desirability.MEMORABILIA not in attrs

    def test_a_relic_that_is_not_a_patch_is_still_memorabilia(self):
        # The patch and memorabilia arms are one if/elif, so a change that let
        # the patch arm swallow both would leave a plain jersey relic with no
        # attribute at all -- and a cheap one would then read as commodity and
        # be thrown out as a base common. That is the whole cheap-card gate
        # firing on a card that is the opposite of commodity.
        relic = listing_for("2024 Panini Absolute Caleb Williams Game-Used Jersey Relic", price=6.0)
        attrs = desirability.attributes_of(relic)
        assert desirability.MEMORABILIA in attrs
        assert desirability.PATCH not in attrs
        assert desirability.is_commodity(relic, 10.0) is False

    def test_plain_base_card_has_no_attributes(self):
        assert desirability.attributes_of(listing_for("Caleb Williams 2024 Panini card")) == ()

    def test_a_team_name_is_not_mistaken_for_a_parallel_attribute(self):
        # Guards the identity fix from the audit: "White Sox" used to extract
        # parallel "White", which would have made every White Sox common look
        # like a non-base parallel and defeated the whole commodity filter.
        attrs = desirability.attributes_of(listing_for("2024 Topps Kyle Teel Chicago White Sox"))
        assert desirability.PARALLEL not in attrs


class TestCommodity:
    def test_cheap_card_with_no_attributes_is_commodity(self):
        assert desirability.is_commodity(listing_for("Caleb Williams 2024 Panini card", price=4.0), 10.0) is True

    def test_cheap_rookie_is_not_commodity(self):
        assert desirability.is_commodity(listing_for("2024 Topps Caleb Williams RC", price=4.0), 10.0) is False

    def test_expensive_card_is_never_commodity_even_with_no_attributes(self):
        # Above the ceiling the market has already voted that this card is
        # not commodity. A keyword list does not get to overrule that.
        plain = listing_for("Caleb Williams 2024 Panini card", price=250.0)
        assert desirability.attributes_of(plain) == ()
        assert desirability.is_commodity(plain, 10.0) is False

    def test_price_exactly_at_the_ceiling_is_not_cheap(self):
        assert desirability.is_commodity(listing_for("Caleb Williams card", price=10.0), 10.0) is False

    def test_unknown_price_is_not_a_commodity_verdict(self):
        # Unknown is never a verdict. It gets rejected upstream for having no
        # price; it must not be labelled common on the way past.
        assert desirability.is_commodity(listing_for("Caleb Williams card", price=None), 10.0) is False


class TestDescribe:
    def test_describes_attributes_in_english(self):
        text = desirability.describe((desirability.ROOKIE, desirability.AUTOGRAPH))
        assert text == "rookie card, autograph"

    def test_empty_when_there_are_none(self):
        assert desirability.describe(()) == ""


# ---------------------------------------------------------------------------
# Interest ranking
#
# This orders a list. It is never a price, a discount or a score, and the
# tests below are about relative order rather than any absolute number --
# asserting a specific total would turn a ranking hint into a contract.
# ---------------------------------------------------------------------------


class TestInterestScore:
    def test_a_signed_numbered_rookie_outranks_a_graded_base_parallel(self):
        signed = listing_for("2024 Panini Prizm Caleb Williams Auto RC #301 /99")
        graded = listing_for("2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 9")
        assert desirability.interest_score(signed) > desirability.interest_score(graded)

    def test_scarcity_compounds(self):
        one_of_one = listing_for("2024 Panini Prizm Caleb Williams Gold Vinyl #301 1/1")
        of_499 = listing_for("2024 Panini Prizm Caleb Williams Gold #301 /499")
        assert desirability.interest_score(one_of_one) > desirability.interest_score(of_499)

    def test_a_base_common_scores_nothing(self):
        assert desirability.interest_score(listing_for("2024 Topps Caleb Williams #150")) == 0

    def test_a_listing_with_no_identity_does_not_crash(self):
        assert desirability.interest_score(listing_for("x", card_identity=None)) == 0


class TestIsStandout:
    def test_one_strong_attribute_is_enough(self):
        # An autograph is an autograph whatever else is true of the listing.
        assert desirability.is_standout(listing_for("2024 Topps Chrome Caleb Williams Auto"))

    def test_one_common_attribute_is_not(self):
        # A bare parallel describes most of the hobby, and a section that
        # prints most of the hobby is the pile the reader is already in.
        assert not desirability.is_standout(
            listing_for("2024 Panini Prizm Caleb Williams Silver Prizm #301", is_rookie_card=False)
        )

    def test_two_common_attributes_are(self):
        assert desirability.is_standout(
            listing_for("2024 Panini Prizm Caleb Williams Silver Prizm RC #301")
        )

    def test_a_base_common_is_not(self):
        assert not desirability.is_standout(listing_for("2024 Topps Caleb Williams #150"))
