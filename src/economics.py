"""Explicit, auditable deal economics: what a card actually costs you,
what you would actually net reselling it, and the most you could bid.

Why this module exists: "saved $203" is not a number you can spend. The
report's savings figure today is ``comp_median - price`` -- it ignores
the ~13.5% eBay takes, the $5 it costs to mail a card with tracking, the
sleeve and toploader, and the sales tax on the way in. On a $30 card
those costs are most of the "profit". A tool that tells you to spend real
money owes you the arithmetic in full.

Two rules shape everything below:

1. **Nothing is a black box.** Every ``DealEconomics`` carries an
   ``assumptions`` tuple of plain sentences naming every rate and every
   guess that went into its numbers, because the report prints them and
   you should be able to disagree with one and re-run.
2. **Unknown never means zero.** Unknown inbound shipping does not become
   $0; it becomes ``shipping_known == False`` plus an assumption saying
   the real cost may be higher. Same discipline as
   ``Listing.total_cost``.

Deliberately *not* here: any prediction. This module never guesses what
a card will sell for, never guesses a grade, and never guesses whether
shipping is free. It converts inputs you supply into consequences, and
names every input it used.

Rounding: **none**, anywhere. Intermediate values stay full-precision
floats and the outputs are unrounded too; formatting money to two
decimals is the report's job. Rounding here would silently change a
comparison ("profit >= $10") depending on where in the chain it happened.
The one place a number is rounded is inside the human-readable assumption
*strings*, which are display text and never fed back into arithmetic.

Pure stdlib, no I/O, no clock, no randomness -- every input is explicit
so the tests are deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class FeeModel:
    """What it costs you to *sell* a card, plus the tax you pay to buy it.

    Every field is a number you should tune. ``ebay_default()`` gives
    documented starting points, not facts -- see its docstring for where
    each one comes from and how wrong it can be.

    Fee base, stated once so it is not a mystery: marketplace and payment
    percentages are applied to the **expected sale price only**. That is
    exact for the normal case of a free-shipping listing (the buyer pays
    one number; you pay postage out of it). If you instead charge the
    buyer for shipping, eBay charges its final value fee on that shipping
    too, so this model will *understate* your fees -- fold the difference
    into ``marketplace_fee_pct`` if you sell that way.
    """

    marketplace_fee_pct: float  # % of sale price, e.g. 13.25 for eBay trading cards
    marketplace_fixed_fee: float  # flat per-order fee, e.g. 0.30
    payment_fee_pct: float  # 0.0 when payment processing is bundled into the above
    outbound_shipping: float  # what it costs YOU to ship it out
    supplies: float  # sleeve + toploader + team bag + mailer
    sales_tax_pct: float  # tax paid on acquisition; 0.0 if unknown or exempt

    @classmethod
    def ebay_default(cls) -> "FeeModel":
        """Starting points for selling a raw or slabbed card on eBay.

        **These are ASSUMPTIONS, not facts.** They are the numbers a
        casual (non-Store) US seller mailing one card at a time would
        typically see as of this writing. Your real numbers depend on
        your store subscription, your seller level, your state, your
        shipping method and your buyer's location. Tune them; they are
        constructor arguments precisely so you can.

        * ``marketplace_fee_pct = 13.25`` -- eBay's final value fee for
          the Trading Cards categories for a seller without a Store
          subscription. Store subscribers and Top Rated sellers pay less;
          some subcategories differ. If you are unsure, leaving it high
          is the safe error: it understates profit rather than
          overstating it.
        * ``marketplace_fixed_fee = 0.30`` -- eBay's per-order fixed fee.
        * ``payment_fee_pct = 0.0`` -- eBay's managed payments bundles
          processing into the final value fee, so charging it again would
          double-count. Set it if you sell somewhere that bills it
          separately.
        * ``outbound_shipping = 5.00`` -- a plain bubble mailer with
          USPS Ground Advantage and tracking, which is what you want on
          anything above pocket-change value. A raw common in an eBay
          Standard Envelope is closer to $1.00 but has no real tracking;
          if that is how you ship, lower this.
        * ``supplies = 1.00`` -- penny sleeve, toploader, team bag and
          bubble mailer, bought in bulk. Cheap, but not free, and it is
          per-card every single time. (This matches the ``supplies``
          default in config/settings.json, so a run that never touches
          settings and a run that reads the shipped defaults produce the
          same numbers.)
        * ``sales_tax_pct = 0.0`` -- deliberately zero, because it is
          state-dependent and this tool does not know where you are.
          Zero here means "not modelled", and ``evaluate`` says so out
          loud in its assumptions rather than letting you assume it was
          measured. Set your own rate (e.g. 7.0) to include it.

        The honest summary: on a $30 sale these defaults cost you about
        $10.28 -- roughly a third of the sale. That is the money the old
        ``comp_median - price`` savings figure was quietly leaving out.
        """
        return cls(
            marketplace_fee_pct=13.25,
            marketplace_fixed_fee=0.30,
            payment_fee_pct=0.0,
            outbound_shipping=5.00,
            supplies=1.00,
            sales_tax_pct=0.0,
        )

    @property
    def total_pct(self) -> float:
        """Combined percentage taken off the top of a sale."""
        return self.marketplace_fee_pct + self.payment_fee_pct

    def selling_fees_on(self, sale_price: float) -> float:
        """Percentage fees plus the flat fee, for a given sale price.

        Note the flat fee applies even to a $0 sale price; that is how a
        per-order fee works, and pretending otherwise would make small
        sales look better than they are.
        """
        return sale_price * (self.total_pct / 100.0) + self.marketplace_fixed_fee

    @property
    def outbound_costs(self) -> float:
        """Postage plus packaging -- what leaves your pocket to ship it."""
        return self.outbound_shipping + self.supplies

    def net_proceeds_on(self, sale_price: float) -> float:
        """What actually lands in your account after selling at this price.

        Can go negative on a cheap card: a $1 sale with the eBay
        defaults nets about -$5.43. That is real, and it is not clamped.
        """
        return sale_price - self.selling_fees_on(sale_price) - self.outbound_costs


@dataclass(frozen=True)
class Acquisition:
    """What buying the card costs you, including the parts you don't know.

    ``shipping=None`` means **unknown**, never free. ``total_cost`` then
    falls back to the price alone -- the same convention as
    ``Listing.total_cost`` -- and ``shipping_known`` is False so callers
    can print "actual cost may be higher" instead of quietly presenting
    an understated number as if it were complete.

    Sales tax is applied to price plus known shipping, which is how most
    US marketplaces charge it.
    """

    price: float
    shipping: Optional[float]  # None means UNKNOWN, never 0
    sales_tax_pct: float

    @property
    def shipping_known(self) -> bool:
        return self.shipping is not None

    @property
    def taxable_base(self) -> float:
        """Price plus known shipping -- what tax is charged on."""
        return self.price + (self.shipping if self.shipping is not None else 0.0)

    @property
    def total_cost(self) -> float:
        """Price + shipping (when known) + tax on both.

        When shipping is unknown this is a **lower bound**, not the
        answer. Check ``shipping_known`` before presenting it as final.
        """
        return self.taxable_base * (1.0 + self.sales_tax_pct / 100.0)


@dataclass(frozen=True)
class DealEconomics:
    """The full, traceable arithmetic for one candidate purchase.

    Nothing here is clamped to look good. If the fees exceed the spread,
    ``expected_profit`` is negative and ``roi_pct`` is negative, and that
    is reported as-is -- a deal that loses money is exactly the thing you
    hired this tool to tell you about.

    ``assumptions`` is not decoration. It is the audit trail: every rate,
    every fallback and every unknown that moved a number in this object
    appears there as a sentence, and the report prints them next to the
    figures they produced.
    """

    acquisition_cost: float
    estimated_market_value: float
    gross_discount: float  # market - acquisition, before any selling costs
    discount_pct: float  # gross_discount as % of market value
    expected_sale_price: float  # what you'd realistically SELL at, not the optimistic ask
    selling_fees: float
    outbound_costs: float
    expected_net_proceeds: float
    expected_profit: float
    roi_pct: float  # profit / acquisition_cost * 100
    shipping_known: bool
    assumptions: Tuple[str, ...]

    @property
    def is_profitable(self) -> bool:
        """Strictly positive expected profit. Breakeven is not profitable."""
        return self.expected_profit > 0.0


def evaluate(
    acquisition: Acquisition,
    estimated_market_value: float,
    fees: FeeModel,
    *,
    resale_haircut_pct: float = 0.0,
) -> DealEconomics:
    """Work out what this purchase is really worth, and show your working.

    ``estimated_market_value`` is whatever the comp engine handed you.
    This function does not second-guess it, does not know how it was
    derived, and does not know how much to trust it -- confidence is the
    comp engine's story to tell.

    ``resale_haircut_pct`` is the gap between "the median comp" and "what
    I would actually realise selling this reasonably quickly": you are
    not the median seller, you want your money back this month, and Best
    Offer exists. Default 0.0 means ``expected_sale_price ==
    estimated_market_value`` -- an optimistic assumption, which is why
    the zero case is *also* stated in ``assumptions`` rather than passing
    silently. A haircut of 10-15% is a reasonable thing to try; this
    module has no opinion on the right value because it has no data on
    your selling behaviour.

    Degenerate inputs are handled explicitly rather than by exception,
    because a pipeline scanning hundreds of listings should not die on
    one weird row:

    * ``estimated_market_value <= 0`` -- ``discount_pct`` is reported as
      0.0 (a percentage of zero is not a thing) and an assumption says
      so. The dollar figures remain correct and will be very negative.
    * ``acquisition_cost == 0`` -- ``roi_pct`` is reported as 0.0, not
      infinity, and an assumption says ROI is undefined on a free card.
      The dollar profit is still exact, and it is the number that matters.
    """
    acquisition_cost = acquisition.total_cost
    gross_discount = estimated_market_value - acquisition_cost

    market_value_is_usable = estimated_market_value > 0.0
    discount_pct = (gross_discount / estimated_market_value * 100.0) if market_value_is_usable else 0.0

    expected_sale_price = estimated_market_value * (1.0 - resale_haircut_pct / 100.0)
    selling_fees = fees.selling_fees_on(expected_sale_price)
    outbound_costs = fees.outbound_costs
    expected_net_proceeds = expected_sale_price - selling_fees - outbound_costs
    expected_profit = expected_net_proceeds - acquisition_cost

    roi_is_defined = acquisition_cost > 0.0
    roi_pct = (expected_profit / acquisition_cost * 100.0) if roi_is_defined else 0.0

    assumptions = []

    # -- acquisition side --------------------------------------------------
    if acquisition.shipping_known:
        assumptions.append(
            "Inbound shipping of ${:.2f} is included in the acquisition cost.".format(acquisition.shipping)
        )
    else:
        assumptions.append(
            "Inbound shipping is UNKNOWN and is not included -- the actual cost may be "
            "higher than ${:.2f}.".format(acquisition_cost)
        )
    if acquisition.sales_tax_pct > 0.0:
        assumptions.append(
            "Sales tax of {:.2f}% is applied to the item price plus known shipping at "
            "purchase.".format(acquisition.sales_tax_pct)
        )
    else:
        assumptions.append(
            "No sales tax is modelled on the purchase -- if your state charges it, your real "
            "cost is higher."
        )

    # -- resale side -------------------------------------------------------
    if resale_haircut_pct != 0.0:
        assumptions.append(
            "A resale haircut of {:.2f}% is applied: expected sale price ${:.2f} against an "
            "estimated market value of ${:.2f}.".format(
                resale_haircut_pct, expected_sale_price, estimated_market_value
            )
        )
    else:
        assumptions.append(
            "No resale haircut -- the expected sale price is the full estimated market value of "
            "${:.2f}, which assumes you sell as well as the median comp.".format(estimated_market_value)
        )
    assumptions.append(
        "Selling fees assumed at {:.2f}% of the sale price plus ${:.2f} per order, charged on the "
        "sale price only.".format(fees.total_pct, fees.marketplace_fixed_fee)
    )
    assumptions.append(
        "Outbound shipping assumed ${:.2f} and supplies ${:.2f} per sale.".format(
            fees.outbound_shipping, fees.supplies
        )
    )

    # -- degenerate inputs, said out loud ----------------------------------
    if not market_value_is_usable:
        assumptions.append(
            "Estimated market value is ${:.2f}, so the discount percentage is not meaningful and "
            "is reported as 0%.".format(estimated_market_value)
        )
    if not roi_is_defined:
        assumptions.append(
            "Acquisition cost is $0.00, so ROI is undefined and is reported as 0% -- read the "
            "dollar profit instead."
        )

    return DealEconomics(
        acquisition_cost=acquisition_cost,
        estimated_market_value=estimated_market_value,
        gross_discount=gross_discount,
        discount_pct=discount_pct,
        expected_sale_price=expected_sale_price,
        selling_fees=selling_fees,
        outbound_costs=outbound_costs,
        expected_net_proceeds=expected_net_proceeds,
        expected_profit=expected_profit,
        roi_pct=roi_pct,
        shipping_known=acquisition.shipping_known,
        assumptions=tuple(assumptions),
    )


def max_rational_bid(
    estimated_market_value: float,
    *,
    required_margin_pct: float,
    shipping_in: Optional[float],
    fees: FeeModel,
    buyer_premium_pct: float = 0.0,
    resale_haircut_pct: float = 0.0,
) -> float:
    """The highest bid that still leaves you ``required_margin_pct`` of margin.

    This is the number that makes auctions safe to act on. An auction's
    current bid is not a price (see ``reasons.AUCTION_CURRENT_BID_NOT_A_PRICE``)
    and there is no way to know what it closes at -- so the only useful
    question is not "is this cheap?" but "what is the most I can pay and
    still be right?". Answer that once, before the last thirty seconds,
    and the auction stops being a decision made under time pressure.

    Margin is defined as a percentage **of estimated market value**, not
    of the bid: 20% margin on a $100 card means you want $20 of profit,
    whatever you end up bidding. Defining it against the bid would make
    the required profit shrink as the price you pay falls, which is
    backwards.

    ``resale_haircut_pct`` is the same gap between "the median comp" and
    "what you will actually get" that ``evaluate`` applies, and it has to be
    applied here too or the two disagree about the same card. It was missing,
    and the effect was not small: at the shipped 5% haircut and a 25% margin,
    the returned ceiling on a $60 card was $25.76, which actually realises
    20.66% -- and the report prints that number under the words "the most you
    can pay and still keep your margin. Above this, stop." The overstatement
    scales with the card: $26 on a $600 one.

    The haircut applies to the SALE side only. ``required_profit`` stays a
    percentage of estimated market value, because that is what the margin was
    defined against and discounting it too would quietly relax the
    requirement at the same time as tightening the budget.

    ``buyer_premium_pct`` covers auction houses that add a premium on top
    of the hammer price (eBay does not; Goldin and Heritage do). It is
    applied to the bid before tax.

    ``shipping_in=None`` means unknown. There is no honest way to solve
    for a bid ceiling while an input is unknown, so it is treated as
    $0.00 -- which makes the returned number an **upper bound that is too
    high by exactly the unknown shipping**. Tell the user that; do not
    present the figure as final. (Passing a pessimistic guess instead is
    the safer habit.)

    Returns **0.0 when no bid can preserve your margin** -- the card is
    not worth enough to cover the fees, the shipping and the margin you
    asked for, so the correct bid is not to bid. 0.0 is never returned as
    "bid a penny"; it means "walk away". The result is never negative.
    """
    required_profit = estimated_market_value * (required_margin_pct / 100.0)
    expected_sale_price = estimated_market_value * (1.0 - resale_haircut_pct / 100.0)
    net_proceeds = fees.net_proceeds_on(expected_sale_price)

    # The most the whole acquisition (bid + premium + shipping + tax) may cost.
    max_total_acquisition_cost = net_proceeds - required_profit

    shipping = shipping_in if shipping_in is not None else 0.0
    pre_tax_budget = max_total_acquisition_cost / (1.0 + fees.sales_tax_pct / 100.0)
    bid = (pre_tax_budget - shipping) / (1.0 + buyer_premium_pct / 100.0)

    return bid if bid > 0.0 else 0.0


def breakeven_grade_probability(
    raw_cost: float,
    grading_cost: float,
    value_if_high_grade: float,
    value_if_low_grade: float,
    fees: FeeModel,
) -> Optional[float]:
    """How often you'd need to hit the high grade for grading to break even.

    **This does not predict a grade and must never be used as if it did.**
    Nothing in this repo looks at an image, and a grade prediction from a
    title is a fantasy. The only question this answers is the one you can
    actually reason about yourself: *"what would I have to believe?"* If
    it comes back 0.62, the decision is whether you genuinely think this
    specific card gems more than three times in five. That is a judgment
    you make holding the card under a lamp, not one this module makes.

    The model is a two-outcome coin flip -- high grade or low grade --
    which is a simplification: real grading has a distribution across
    PSA 8, 9 and 10, and returns take months during which the market
    moves. Treat the answer as a sanity check, not a valuation. Both
    outcomes are run through the same selling fees and shipping as any
    other sale; grading fees and shipping to the grader belong in
    ``grading_cost``.

    Returns **None** when the question is degenerate, meaning one of:

    * the two outcomes net the same or the "high" grade nets *less* than
      the low one -- there is no probability that makes the gamble
      matter, because grading changes nothing (or hurts);
    * the low-grade outcome already covers the cost -- you profit even if
      it grades badly, so there is no breakeven probability to compute;
      the answer is "you don't need to believe anything".

    None therefore means "the question does not apply", never "zero" and
    never "unknown". A returned value **above 1.0** is not degenerate and
    is returned as-is: it means no probability is high enough, i.e. even
    a guaranteed high grade loses money, which is a real and useful
    answer.
    """
    total_cost = raw_cost + grading_cost
    net_high = fees.net_proceeds_on(value_if_high_grade)
    net_low = fees.net_proceeds_on(value_if_low_grade)

    spread = net_high - net_low
    if spread <= 0.0:
        return None

    shortfall = total_cost - net_low
    if shortfall <= 0.0:
        return None

    return shortfall / spread
