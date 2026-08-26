import pytest

from src.economics import (
    Acquisition,
    DealEconomics,
    FeeModel,
    breakeven_grade_probability,
    evaluate,
    max_rational_bid,
)

# A deliberately boring fee model: round numbers so every expected value
# below can be worked out by hand, which is the point -- a test you have
# to run to know the answer to isn't checking the arithmetic, it's
# recording it.
SIMPLE_FEES = FeeModel(
    marketplace_fee_pct=10.0,
    marketplace_fixed_fee=0.0,
    payment_fee_pct=0.0,
    outbound_shipping=5.0,
    supplies=1.0,
    sales_tax_pct=0.0,
)

NO_FEES = FeeModel(
    marketplace_fee_pct=0.0,
    marketplace_fixed_fee=0.0,
    payment_fee_pct=0.0,
    outbound_shipping=0.0,
    supplies=0.0,
    sales_tax_pct=0.0,
)


def _find(economics: DealEconomics, fragment: str) -> str:
    matches = [a for a in economics.assumptions if fragment in a]
    assert matches, "no assumption containing {!r} in {!r}".format(fragment, economics.assumptions)
    return matches[0]


# --- FeeModel --------------------------------------------------------------

def test_ebay_defaults_are_the_documented_numbers():
    # These are assumptions, not facts -- but they are assumptions the
    # report prints, so a silent change to one should break a test.
    fees = FeeModel.ebay_default()
    assert fees.marketplace_fee_pct == 13.25
    assert fees.marketplace_fixed_fee == 0.30
    assert fees.payment_fee_pct == 0.0
    assert fees.outbound_shipping == 5.00
    assert fees.supplies == 1.00
    assert fees.sales_tax_pct == 0.0


def test_fee_model_is_frozen():
    fees = FeeModel.ebay_default()
    with pytest.raises(Exception):
        fees.marketplace_fee_pct = 5.0  # type: ignore[misc]


def test_total_pct_adds_payment_processing():
    fees = FeeModel(
        marketplace_fee_pct=10.0, marketplace_fixed_fee=0.0, payment_fee_pct=2.9,
        outbound_shipping=0.0, supplies=0.0, sales_tax_pct=0.0,
    )
    assert fees.total_pct == pytest.approx(12.9)
    assert fees.selling_fees_on(100.0) == pytest.approx(12.9)


def test_fixed_fee_applies_even_at_a_zero_sale_price():
    fees = FeeModel(
        marketplace_fee_pct=0.0, marketplace_fixed_fee=0.30, payment_fee_pct=0.0,
        outbound_shipping=0.0, supplies=0.0, sales_tax_pct=0.0,
    )
    assert fees.selling_fees_on(0.0) == pytest.approx(0.30)


def test_net_proceeds_can_be_negative_on_a_cheap_card():
    assert SIMPLE_FEES.net_proceeds_on(1.0) == pytest.approx(1.0 - 0.1 - 6.0)


# --- Acquisition -----------------------------------------------------------

def test_total_cost_with_known_shipping():
    acq = Acquisition(price=100.0, shipping=5.0, sales_tax_pct=0.0)
    assert acq.total_cost == pytest.approx(105.0)
    assert acq.shipping_known is True


def test_total_cost_falls_back_to_price_when_shipping_unknown():
    acq = Acquisition(price=100.0, shipping=None, sales_tax_pct=0.0)
    assert acq.total_cost == pytest.approx(100.0)
    assert acq.shipping_known is False


def test_free_shipping_is_not_the_same_as_unknown_shipping():
    free = Acquisition(price=100.0, shipping=0.0, sales_tax_pct=0.0)
    unknown = Acquisition(price=100.0, shipping=None, sales_tax_pct=0.0)
    assert free.total_cost == unknown.total_cost == 100.0
    assert free.shipping_known is True
    assert unknown.shipping_known is False


def test_sales_tax_applies_to_price_plus_known_shipping():
    acq = Acquisition(price=100.0, shipping=10.0, sales_tax_pct=10.0)
    assert acq.taxable_base == pytest.approx(110.0)
    assert acq.total_cost == pytest.approx(121.0)


def test_sales_tax_ignores_unknown_shipping():
    acq = Acquisition(price=100.0, shipping=None, sales_tax_pct=10.0)
    assert acq.total_cost == pytest.approx(110.0)


# --- evaluate: the ordinary path ------------------------------------------

def test_ordinary_deal_arithmetic_is_traceable_by_hand():
    # Buy at $50 + $5 ship, sell at $100, 10% fee, $6 to ship out.
    acq = Acquisition(price=50.0, shipping=5.0, sales_tax_pct=0.0)
    result = evaluate(acq, 100.0, SIMPLE_FEES)

    assert result.acquisition_cost == pytest.approx(55.0)
    assert result.estimated_market_value == 100.0
    assert result.gross_discount == pytest.approx(45.0)
    assert result.discount_pct == pytest.approx(45.0)
    assert result.expected_sale_price == pytest.approx(100.0)
    assert result.selling_fees == pytest.approx(10.0)
    assert result.outbound_costs == pytest.approx(6.0)
    assert result.expected_net_proceeds == pytest.approx(84.0)
    assert result.expected_profit == pytest.approx(29.0)
    assert result.roi_pct == pytest.approx(29.0 / 55.0 * 100.0)
    assert result.shipping_known is True
    assert result.is_profitable is True


def test_gross_discount_ignores_selling_costs_but_profit_does_not():
    # This gap is the whole reason the module exists: $45 "saved" is $29
    # of actual profit once eBay and the post office are paid.
    acq = Acquisition(price=50.0, shipping=5.0, sales_tax_pct=0.0)
    result = evaluate(acq, 100.0, SIMPLE_FEES)
    assert result.gross_discount > result.expected_profit


def test_no_fees_at_all_means_profit_equals_gross_discount():
    acq = Acquisition(price=50.0, shipping=None, sales_tax_pct=0.0)
    result = evaluate(acq, 100.0, NO_FEES)
    assert result.expected_profit == pytest.approx(result.gross_discount)


def test_result_is_frozen():
    result = evaluate(Acquisition(10.0, None, 0.0), 20.0, NO_FEES)
    with pytest.raises(Exception):
        result.expected_profit = 999.0  # type: ignore[misc]


def test_intermediate_values_are_not_rounded():
    # 1/3-ish numbers must survive intact; rounding here would move a
    # threshold comparison somewhere downstream.
    acq = Acquisition(price=10.0 / 3.0, shipping=None, sales_tax_pct=0.0)
    result = evaluate(acq, 10.0, NO_FEES)
    assert result.acquisition_cost == 10.0 / 3.0
    assert result.expected_profit == 10.0 - 10.0 / 3.0


# --- evaluate: the edges ---------------------------------------------------

def test_unknown_shipping_is_reported_not_assumed_zero():
    acq = Acquisition(price=50.0, shipping=None, sales_tax_pct=0.0)
    result = evaluate(acq, 100.0, SIMPLE_FEES)
    assert result.shipping_known is False
    assert result.acquisition_cost == pytest.approx(50.0)
    assert _find(result, "Inbound shipping is UNKNOWN") == (
        "Inbound shipping is UNKNOWN and is not included -- the actual cost may be "
        "higher than $50.00."
    )


def test_known_shipping_says_so_in_the_assumptions():
    result = evaluate(Acquisition(50.0, 4.5, 0.0), 100.0, SIMPLE_FEES)
    assert "Inbound shipping of $4.50 is included in the acquisition cost." in result.assumptions


def test_fees_larger_than_the_spread_report_a_loss_honestly():
    # $18 card, "worth" $20 -- the fees eat it. Nothing is clamped at zero.
    acq = Acquisition(price=18.0, shipping=None, sales_tax_pct=0.0)
    result = evaluate(acq, 20.0, SIMPLE_FEES)
    assert result.gross_discount == pytest.approx(2.0)
    assert result.expected_net_proceeds == pytest.approx(20.0 - 2.0 - 6.0)
    assert result.expected_profit == pytest.approx(-6.0)
    assert result.roi_pct == pytest.approx(-6.0 / 18.0 * 100.0)
    assert result.is_profitable is False


def test_zero_market_value_does_not_divide_by_zero():
    result = evaluate(Acquisition(10.0, None, 0.0), 0.0, NO_FEES)
    assert result.discount_pct == 0.0
    assert result.gross_discount == pytest.approx(-10.0)
    assert result.expected_profit == pytest.approx(-10.0)
    assert _find(result, "is not meaningful") == (
        "Estimated market value is $0.00, so the discount percentage is not meaningful "
        "and is reported as 0%."
    )


def test_negative_market_value_is_handled_the_same_way():
    result = evaluate(Acquisition(10.0, None, 0.0), -5.0, NO_FEES)
    assert result.discount_pct == 0.0
    assert result.expected_profit == pytest.approx(-15.0)
    assert any("is not meaningful" in a for a in result.assumptions)


def test_zero_acquisition_cost_reports_roi_as_zero_not_infinity():
    result = evaluate(Acquisition(0.0, None, 0.0), 100.0, NO_FEES)
    assert result.acquisition_cost == 0.0
    assert result.expected_profit == pytest.approx(100.0)
    assert result.roi_pct == 0.0
    assert _find(result, "ROI is undefined") == (
        "Acquisition cost is $0.00, so ROI is undefined and is reported as 0% -- read "
        "the dollar profit instead."
    )


def test_roi_is_normal_whenever_acquisition_cost_is_positive():
    result = evaluate(Acquisition(50.0, None, 0.0), 100.0, NO_FEES)
    assert result.roi_pct == pytest.approx(100.0)
    assert not any("ROI is undefined" in a for a in result.assumptions)


def test_market_value_equal_to_cost_is_a_loss_after_fees():
    result = evaluate(Acquisition(100.0, None, 0.0), 100.0, SIMPLE_FEES)
    assert result.gross_discount == pytest.approx(0.0)
    assert result.discount_pct == pytest.approx(0.0)
    assert result.expected_profit == pytest.approx(-16.0)


# --- evaluate: the resale haircut -----------------------------------------

def test_default_haircut_is_zero_and_is_still_stated():
    result = evaluate(Acquisition(50.0, None, 0.0), 100.0, SIMPLE_FEES)
    assert result.expected_sale_price == pytest.approx(100.0)
    assert _find(result, "No resale haircut") == (
        "No resale haircut -- the expected sale price is the full estimated market value "
        "of $100.00, which assumes you sell as well as the median comp."
    )


def test_haircut_lowers_the_expected_sale_price_and_is_named():
    result = evaluate(Acquisition(50.0, None, 0.0), 100.0, SIMPLE_FEES, resale_haircut_pct=15.0)
    assert result.expected_sale_price == pytest.approx(85.0)
    assert result.selling_fees == pytest.approx(8.5)
    assert result.expected_net_proceeds == pytest.approx(85.0 - 8.5 - 6.0)
    assert result.expected_profit == pytest.approx(20.5)
    # The market value it is a haircut *from* is unchanged.
    assert result.estimated_market_value == 100.0
    assert result.discount_pct == pytest.approx(50.0)
    assert _find(result, "resale haircut of") == (
        "A resale haircut of 15.00% is applied: expected sale price $85.00 against an "
        "estimated market value of $100.00."
    )


def test_haircut_of_100_percent_means_you_sell_for_nothing():
    result = evaluate(Acquisition(50.0, None, 0.0), 100.0, NO_FEES, resale_haircut_pct=100.0)
    assert result.expected_sale_price == pytest.approx(0.0)
    assert result.expected_profit == pytest.approx(-50.0)


# --- evaluate: the assumptions tuple --------------------------------------

def test_assumptions_are_a_tuple_of_non_empty_sentences():
    result = evaluate(Acquisition(50.0, 5.0, 7.0), 100.0, FeeModel.ebay_default(), resale_haircut_pct=10.0)
    assert isinstance(result.assumptions, tuple)
    assert result.assumptions
    for sentence in result.assumptions:
        assert sentence.strip()
        assert sentence.endswith("."), sentence


def test_fee_and_outbound_assumptions_quote_the_actual_rates():
    fees = FeeModel(
        marketplace_fee_pct=13.25, marketplace_fixed_fee=0.30, payment_fee_pct=2.0,
        outbound_shipping=5.0, supplies=1.0, sales_tax_pct=0.0,
    )
    result = evaluate(Acquisition(50.0, None, 0.0), 100.0, fees)
    assert (
        "Selling fees assumed at 15.25% of the sale price plus $0.30 per order, charged "
        "on the sale price only."
    ) in result.assumptions
    assert "Outbound shipping assumed $5.00 and supplies $1.00 per sale." in result.assumptions


def test_sales_tax_assumption_when_tax_is_charged():
    result = evaluate(Acquisition(100.0, None, 7.5), 200.0, NO_FEES)
    assert result.acquisition_cost == pytest.approx(107.5)
    assert (
        "Sales tax of 7.50% is applied to the item price plus known shipping at purchase."
    ) in result.assumptions


def test_sales_tax_assumption_when_tax_is_not_modelled():
    result = evaluate(Acquisition(100.0, None, 0.0), 200.0, NO_FEES)
    assert (
        "No sales tax is modelled on the purchase -- if your state charges it, your real "
        "cost is higher."
    ) in result.assumptions


def test_shipping_and_tax_assumptions_do_not_contradict_each_other():
    result = evaluate(Acquisition(50.0, 5.0, 7.0), 100.0, NO_FEES)
    assert not any("UNKNOWN" in a for a in result.assumptions)
    assert not any("No sales tax" in a for a in result.assumptions)


# --- max_rational_bid ------------------------------------------------------

def test_max_rational_bid_leaves_exactly_the_required_margin():
    # $100 card, no fees, no tax, want $20 of margin -> bid at most $80.
    bid = max_rational_bid(
        100.0, required_margin_pct=20.0, shipping_in=0.0, fees=NO_FEES,
    )
    assert bid == pytest.approx(80.0)


def test_max_rational_bid_subtracts_fees_and_shipping():
    # net at $100 = 100 - 10 fee - 6 out = 84; minus $20 margin = 64;
    # minus $4 inbound shipping = $60.
    bid = max_rational_bid(
        100.0, required_margin_pct=20.0, shipping_in=4.0, fees=SIMPLE_FEES,
    )
    assert bid == pytest.approx(60.0)


def test_bidding_the_max_actually_produces_the_required_margin():
    # The real contract: buy at the ceiling and the economics agree.
    fees = SIMPLE_FEES
    bid = max_rational_bid(100.0, required_margin_pct=20.0, shipping_in=4.0, fees=fees)
    result = evaluate(Acquisition(bid, 4.0, fees.sales_tax_pct), 100.0, fees)
    assert result.expected_profit == pytest.approx(20.0)


def test_max_rational_bid_accounts_for_sales_tax():
    taxed = FeeModel(
        marketplace_fee_pct=0.0, marketplace_fixed_fee=0.0, payment_fee_pct=0.0,
        outbound_shipping=0.0, supplies=0.0, sales_tax_pct=10.0,
    )
    # Budget of $80 pre-margin-adjusted; tax means the bid itself is 80/1.1.
    bid = max_rational_bid(100.0, required_margin_pct=20.0, shipping_in=0.0, fees=taxed)
    assert bid == pytest.approx(80.0 / 1.1)
    result = evaluate(Acquisition(bid, 0.0, taxed.sales_tax_pct), 100.0, taxed)
    assert result.expected_profit == pytest.approx(20.0)


def test_buyer_premium_lowers_the_bid_ceiling():
    bid = max_rational_bid(
        100.0, required_margin_pct=20.0, shipping_in=0.0, fees=NO_FEES, buyer_premium_pct=20.0,
    )
    assert bid == pytest.approx(80.0 / 1.2)


def test_unknown_inbound_shipping_is_treated_as_zero_and_is_an_upper_bound():
    unknown = max_rational_bid(100.0, required_margin_pct=20.0, shipping_in=None, fees=SIMPLE_FEES)
    known = max_rational_bid(100.0, required_margin_pct=20.0, shipping_in=4.0, fees=SIMPLE_FEES)
    assert unknown == pytest.approx(64.0)
    assert unknown > known


def test_max_rational_bid_clamps_at_zero_when_no_bid_preserves_margin():
    # A $5 card cannot cover $6 of outbound costs, never mind a margin.
    bid = max_rational_bid(5.0, required_margin_pct=20.0, shipping_in=None, fees=SIMPLE_FEES)
    assert bid == 0.0


def test_max_rational_bid_never_returns_a_negative_number():
    for value in (0.0, 1.0, 3.0, 6.5):
        bid = max_rational_bid(value, required_margin_pct=30.0, shipping_in=2.0, fees=SIMPLE_FEES)
        assert bid >= 0.0


def test_zero_margin_still_covers_costs():
    bid = max_rational_bid(100.0, required_margin_pct=0.0, shipping_in=0.0, fees=SIMPLE_FEES)
    assert bid == pytest.approx(84.0)


def test_a_higher_required_margin_never_raises_the_ceiling():
    bids = [
        max_rational_bid(100.0, required_margin_pct=m, shipping_in=0.0, fees=SIMPLE_FEES)
        for m in (0.0, 10.0, 25.0, 50.0, 90.0)
    ]
    assert bids == sorted(bids, reverse=True)


def test_max_rational_bid_on_a_worthless_card_is_zero():
    assert max_rational_bid(0.0, required_margin_pct=10.0, shipping_in=None, fees=NO_FEES) == 0.0


# --- breakeven_grade_probability ------------------------------------------

def test_breakeven_probability_ordinary_case():
    # Cost 30 ($20 raw + $10 grading). Low grade nets $20, high nets $120.
    # Need (30 - 20) / (120 - 20) = 10%.
    p = breakeven_grade_probability(
        raw_cost=20.0, grading_cost=10.0,
        value_if_high_grade=120.0, value_if_low_grade=20.0, fees=NO_FEES,
    )
    assert p == pytest.approx(0.10)


def test_breakeven_probability_accounts_for_selling_fees():
    # net(300) = 300 - 30 - 6 = 264; net(40) = 40 - 4 - 6 = 30.
    # cost = 100; (100 - 30) / (264 - 30).
    p = breakeven_grade_probability(
        raw_cost=75.0, grading_cost=25.0,
        value_if_high_grade=300.0, value_if_low_grade=40.0, fees=SIMPLE_FEES,
    )
    assert p == pytest.approx(70.0 / 234.0)


def test_breakeven_probability_is_none_when_grades_are_worth_the_same():
    assert breakeven_grade_probability(
        raw_cost=20.0, grading_cost=10.0,
        value_if_high_grade=100.0, value_if_low_grade=100.0, fees=NO_FEES,
    ) is None


def test_breakeven_probability_is_none_when_the_high_grade_is_worth_less():
    assert breakeven_grade_probability(
        raw_cost=20.0, grading_cost=10.0,
        value_if_high_grade=50.0, value_if_low_grade=100.0, fees=NO_FEES,
    ) is None


def test_breakeven_probability_is_none_when_the_low_grade_already_profits():
    # You make money even if it grades badly -- there is nothing to believe.
    assert breakeven_grade_probability(
        raw_cost=10.0, grading_cost=5.0,
        value_if_high_grade=500.0, value_if_low_grade=100.0, fees=NO_FEES,
    ) is None


def test_breakeven_probability_is_none_at_exact_breakeven_on_the_low_grade():
    # cost 30, net(low) = 30 -> the low outcome exactly pays for itself.
    assert breakeven_grade_probability(
        raw_cost=20.0, grading_cost=10.0,
        value_if_high_grade=200.0, value_if_low_grade=30.0, fees=NO_FEES,
    ) is None


def test_breakeven_probability_above_one_means_it_never_works():
    # Even a guaranteed high grade doesn't cover the cost -- returned as a
    # number above 1.0 rather than None, because "you'd need 150%" is a
    # real answer and None means "the question doesn't apply".
    p = breakeven_grade_probability(
        raw_cost=100.0, grading_cost=50.0,
        value_if_high_grade=120.0, value_if_low_grade=20.0, fees=NO_FEES,
    )
    assert p is not None
    assert p > 1.0
    assert p == pytest.approx(130.0 / 100.0)


def test_breakeven_probability_of_exactly_one():
    p = breakeven_grade_probability(
        raw_cost=100.0, grading_cost=20.0,
        value_if_high_grade=120.0, value_if_low_grade=20.0, fees=NO_FEES,
    )
    assert p == pytest.approx(1.0)


def test_grading_cost_moves_the_bar():
    cheap = breakeven_grade_probability(
        raw_cost=20.0, grading_cost=10.0,
        value_if_high_grade=120.0, value_if_low_grade=20.0, fees=NO_FEES,
    )
    dear = breakeven_grade_probability(
        raw_cost=20.0, grading_cost=40.0,
        value_if_high_grade=120.0, value_if_low_grade=20.0, fees=NO_FEES,
    )
    assert dear > cheap


# ---------------------------------------------------------------------------
# The max bid has to survive a round trip
#
# The contract is not "returns a plausible number", it is "bidding this
# number produces the margin you asked for". The only way to test that is to
# feed the answer back through evaluate() with the SAME assumptions.
# ---------------------------------------------------------------------------

class TestMaxBidRoundTrip:
    FEES = FeeModel(
        marketplace_fee_pct=13.25, marketplace_fixed_fee=0.30, payment_fee_pct=0.0,
        outbound_shipping=5.0, supplies=1.0, sales_tax_pct=0.0,
    )

    def _round_trip(self, emv, margin, shipping, haircut):
        bid = max_rational_bid(
            emv, required_margin_pct=margin, shipping_in=shipping,
            fees=self.FEES, resale_haircut_pct=haircut,
        )
        result = evaluate(
            Acquisition(bid, shipping, self.FEES.sales_tax_pct),
            emv, self.FEES, resale_haircut_pct=haircut,
        )
        return bid, result

    def test_bidding_the_max_produces_the_margin_at_the_shipped_haircut(self):
        # The haircut was missing here while evaluate() applied it, so the
        # two disagreed about the same card: the ceiling on a $60 card came
        # out $25.76 and actually realised 20.66%, under the words "the most
        # you can pay and still keep your margin. Above this, stop."
        _, result = self._round_trip(emv=60.0, margin=25.0, shipping=4.99, haircut=5.0)
        assert result.expected_profit == pytest.approx(15.0)

    def test_the_overstatement_scaled_with_the_card(self):
        _, result = self._round_trip(emv=600.0, margin=25.0, shipping=4.99, haircut=5.0)
        assert result.expected_profit == pytest.approx(150.0)

    def test_a_haircut_lowers_the_ceiling(self):
        with_haircut = max_rational_bid(
            60.0, required_margin_pct=25.0, shipping_in=4.99, fees=self.FEES,
            resale_haircut_pct=5.0,
        )
        without = max_rational_bid(
            60.0, required_margin_pct=25.0, shipping_in=4.99, fees=self.FEES,
        )
        assert with_haircut < without

    def test_no_haircut_is_still_the_default(self):
        # Every existing caller and test passes nothing, and must keep the
        # behaviour it had.
        _, result = self._round_trip(emv=60.0, margin=25.0, shipping=4.99, haircut=0.0)
        assert result.expected_profit == pytest.approx(15.0)

    def test_the_required_profit_is_not_discounted_too(self):
        # Margin is defined against estimated market value. Discounting the
        # requirement as well as the budget would quietly relax it.
        _, result = self._round_trip(emv=100.0, margin=20.0, shipping=0.0, haircut=10.0)
        assert result.expected_profit == pytest.approx(20.0)
