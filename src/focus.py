"""What reaches the email: price focus and length.

Everything upstream of this module answers one question -- "is this listing
underpriced, and by how much". This module answers a different one:
"should it be in your morning email at all". They are not the same
question, and conflating them is how a run that reviewed three hundred
listings ends up printing three hundred listings and getting skimmed
instead of read.

Three rules, all yours, all in ``config/settings.json`` -> ``focus``:

* **A price ceiling.** You buy at the cheap end and bid to win, so the
  email is built around cards at or under ``price_ceiling``. A card above
  it is not being called a bad card -- it is being called not-what-you-shop-
  for, and it has to be exceptional (a big percentage AND a big dollar
  figure, off a comp CardPro is willing to stand behind) to take a slot
  away from the cheap ones. A listing whose price cannot be read fails
  this rule too, under its own reason: there is no cost to hold against
  the ceiling and no valuation behind it either.
* **Bidding room on auctions.** An auction whose current bid already sits
  above your max rational bid is not a card you can win at a price that
  works. It is a finished story, and it costs the same eight lines to
  print as a live one.
* **A hard cap on length.** At most ``max_listings`` distinct listings,
  handed out section by section (see ``trim``) so the cut always falls on
  the tail rather than on the best card of the day.

Three things this module deliberately does NOT do:

* It never promotes. Nothing enters the email here that the sections had
  not already earned -- focus only removes.
* It never changes a valuation, a confidence, or a rejection reason. A
  $900 card left out for being above the ceiling was still fetched, still
  valued and still counted in the run stats; it is one config number away
  from being printed again.
* It never drops anything silently. Every call returns counts of what it
  removed and why, and the report prints them in its thresholds footer.
  The whole point of a shorter email is that you trust what is missing
  from it.

Targets are exempt from both price rules above, not just the ceiling (the
length cap still applies to them, because that one is about how long the
email is and not about the card). A target card is one you asked for by
name at a price you set yourself (see src/targets.py), and both price rules
here are CardPro's own opinion about price: the ceiling is the cheap end
you usually shop at, and the max rational bid is a comp median less fees
less the margin in your config. Your own price test has already been
applied to a target -- the hit carries the band it landed in, or says it is
above every band you set -- so a second opinion from this module would only
overrule you with your own settings. The bidding-room rule is where that
bites hardest: on a target with a $400 buy zone the rational ceiling sits
nearer $235, because it is a resale-margin figure and a target is
explicitly allowed to be a bad flip ("you're paying up for something you
specifically want"), so applying it here would hide the card you named
across most of the range you said you would pay for it. The unreadable
price goes the same way for a weaker but sufficient reason: "a copy of the
card you asked for is listed right now" is worth eight lines on its own,
and the report says exactly that rather than inventing a cost for it.

None of that is a promotion. A target hit still had to be produced
upstream, focus only declines to remove it, and the report still prints the
max bid and the band side by side so both numbers are in front of you.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Optional

#: Why a listing did not make the email. Distinct strings rather than one
#: "filtered" bucket because the report says which happened, and "12 above
#: your ceiling" and "12 auctions already bid past your maximum" call for
#: opposite reactions from you.
ABOVE_CEILING = "above_ceiling"
NO_BIDDING_ROOM = "no_bidding_room"
PRICE_UNKNOWN = "price_unknown"


@dataclass(frozen=True)
class FocusRules:
    """The editorial rules for one report. Frozen: a report must not be
    able to talk itself into a longer email halfway through rendering.

    Defaults match ``config/settings.json``; ``config.load_config`` builds
    the real one. ``OFF`` below is what every caller that does not pass
    rules gets, so an older caller keeps the pre-focus behaviour exactly.
    """

    enabled: bool = True
    price_ceiling: float = 40.0
    exceptional_min_discount_pct: float = 50.0
    exceptional_min_savings_dollars: float = 100.0
    require_auction_bidding_room: bool = True
    max_listings: int = 40
    max_per_section: int = 10


#: Focus disabled: every listing kept, no cap. The default for
#: ``report.build_report`` so that callers written before focus existed --
#: including most of the test suite -- get the report they always got.
OFF = FocusRules(enabled=False, max_listings=0, max_per_section=0)


@dataclass
class Selection:
    """``kept`` is what the report may classify; ``omitted`` counts what was
    left out, by reason, in distinct listing ids."""

    kept: list
    omitted: Counter = field(default_factory=Counter)

    @property
    def omitted_total(self) -> int:
        return sum(self.omitted.values())


def _price(listing) -> Optional[float]:
    """What this listing would cost you right now, shipping included when
    known. For an auction that is the current bid -- which is exactly the
    number you would have to beat to be in it, so it is the right one to
    measure a bidding budget against, even though it is emphatically not
    the price the card will sell for.
    """
    total = getattr(listing, "total_cost", None)
    if total is not None:
        return total
    return getattr(listing, "price", None)


def is_exceptional(listing, rules: FocusRules) -> bool:
    """The escape hatch for expensive cards: a big percentage AND a big
    dollar figure, off a comp CardPro will stand behind.

    All three conditions, because each alone has a known failure mode. A
    percentage alone flags a $900 card at 55% off a comp built from a
    context-only bucket. A dollar figure alone flags every expensive card
    with a mild discount, which is precisely the flood this ceiling exists
    to stop. And without ``flag_eligible`` the whole thing runs off comps
    the engine itself refuses to declare deals from.
    """
    match = getattr(listing, "comp_match", None)
    if match is None or not getattr(match, "flag_eligible", False):
        return False
    pct = getattr(listing, "pct_under_market", None)
    savings = getattr(listing, "dollar_savings", None)
    if pct is None or savings is None:
        return False
    return (
        pct >= rules.exceptional_min_discount_pct
        and savings >= rules.exceptional_min_savings_dollars
    )


def _has_bidding_room(listing, rules: FocusRules) -> bool:
    """False only when we KNOW the bidding is already past your ceiling.

    An auction with no market value has no max rational bid, so there is no
    ceiling to be past and nothing to conclude -- it stays, and the report
    says the ceiling could not be computed. Silence about a card we could
    not judge would be indistinguishable from silence about a card we
    judged and rejected.
    """
    if not rules.require_auction_bidding_room:
        return True
    if not getattr(listing, "is_auction", False):
        return True
    ceiling = getattr(listing, "max_rational_bid", None)
    if ceiling is None:
        return True
    # The BID, not the total cost. economics.max_rational_bid has already
    # subtracted inbound shipping to arrive at its ceiling, so comparing a
    # shipping-inclusive total against it charges shipping twice and drops
    # auctions you could still win -- on a $60 card with $4.99 shipping, a
    # $24 bid with $1.76 of room left was being reported as having none.
    bid = getattr(listing, "price", None)
    if bid is None:
        return True
    return bid <= ceiling


def omission_reason(listing, rules: FocusRules) -> Optional[str]:
    """Why this listing is not email material, or None if it is."""
    if not rules.enabled:
        return None
    if getattr(listing, "target_hit", None) is not None:
        # You asked for this card by name at your own price, and
        # src/targets.py has already applied that price test -- every rule
        # below is CardPro's price opinion. See the module docstring.
        return None
    if not _has_bidding_room(listing, rules):
        return NO_BIDDING_ROOM
    price = _price(listing)
    if price is None:
        # No price means no way to tell whether it is the kind of card you
        # shop for, and no valuation behind it either. It is counted, not
        # printed.
        return PRICE_UNKNOWN
    if price <= rules.price_ceiling:
        return None
    return None if is_exceptional(listing, rules) else ABOVE_CEILING


def select(deals, rules: FocusRules) -> Selection:
    """Split listings into what the email may show and a tally of the rest.

    Input order is preserved -- ranking belongs to the report, not here.
    """
    kept = []
    kept_ids = set()
    reason_by_id = {}
    for listing in deals:
        reason = omission_reason(listing, rules)
        if reason is None:
            kept.append(listing)
            kept_ids.add(listing.id)
            continue
        # First reason wins. The same eBay item can arrive twice in one run
        # (two saved searches, one card) with different data each time, and
        # the two copies can be omitted for DIFFERENT reasons -- one over the
        # price ceiling, one an auction past its max bid. Counting both makes
        # the footer claim two cards were left out and print two sentences
        # about one.
        reason_by_id.setdefault(listing.id, reason)
    # A card that is being shown must not also be counted as left out: the
    # footer's buckets have to add up to what CardPro saw.
    omitted = Counter(
        reason for listing_id, reason in reason_by_id.items() if listing_id not in kept_ids
    )
    return Selection(kept=kept, omitted=omitted)


def trim(sections, order, rules: FocusRules, no_leftovers=()) -> tuple:
    """Cut the classified sections down to ``max_listings`` distinct
    listings. Returns ``(sections, trimmed)``.

    Slots are handed out in two passes over ``order`` -- which is the
    caller's priority order, NOT necessarily render order:

    1. every section takes up to ``max_per_section``;
    2. then, with whatever budget is left, every section except those named
       in ``no_leftovers`` takes the rest.

    ``no_leftovers`` exists for sections that are worth showing a few of and
    never worth showing forty of -- a section that says of itself that it is
    not a recommendation should not be able to become the email just by
    being the largest thing CardPro produced that morning. Those sections
    keep their first-pass share and the leftover slots go unspent.

    The first pass is what stops one crowded section from eating the email.
    Forty listings allocated purely top-down means a 40-opportunity morning
    prints zero auctions, and "the day was so good you saw none of the
    thing you actually bid on" is a bug wearing a ranking's clothes. The
    second pass is what stops the cap from being wasted on a quiet morning.

    A listing appearing in two sections (a target hit that is also an
    opportunity) costs budget once, at the first section that takes it, but
    still occupies a slot in each -- because the budget is really about how
    long the email is, and it is printed twice.

    Sections keep their input order throughout, so the ranking each section
    did for itself survives: trimming takes from the bottom of a section,
    never from the middle.
    """
    kept = OrderedDict((key, []) for key in sections)
    if not rules.enabled or rules.max_listings <= 0:
        for key, deals in sections.items():
            kept[key] = list(deals)
        return kept, 0

    keys = [key for key in order if key in sections]
    keys += [key for key in sections if key not in order]

    kept_ids = set()
    positions = {key: 0 for key in keys}

    def fill(key, limit):
        deals = sections[key]
        index = positions[key]
        while index < len(deals) and len(kept[key]) < limit:
            deal = deals[index]
            if deal.id not in kept_ids:
                if len(kept_ids) >= rules.max_listings:
                    break
                kept_ids.add(deal.id)
            kept[key].append(deal)
            index += 1
        positions[key] = index

    per_section = rules.max_per_section if rules.max_per_section > 0 else rules.max_listings
    for key in keys:
        fill(key, per_section)
    for key in keys:
        if key in no_leftovers:
            continue
        fill(key, len(sections[key]))

    all_ids = {deal.id for deals in sections.values() for deal in deals}
    return kept, len(all_ids) - len(kept_ids)
