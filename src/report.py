"""The daily email: a decision-first, sectioned report.

WHY THIS SHAPE
--------------
The old report was one numbered list sorted by dollars saved. That is a
fine *ranking* (see ``rank_deals``) and a bad *document*: it answered
"how much would I save" and nothing else. Reading it, you could not tell
a $200 slab backed by seven grade-matched comps apart from a $1.25 base
card compared against a price bucket it defined itself -- the two looked
identical except for the dollar figure, and the second kind was 76% of
what CardPro produced (docs/CARDPRO_2_AUDIT.md §2.2).

So this module is organised around five questions, in this order:

  1. what deserves attention  -> sections, strongest first
  2. why it is a deal         -> the Market line, always with level, n,
                                 basis, range and recency
  3. how confident we are     -> the Confidence line, which must say WHY
  4. what could invalidate it -> the Risks line
  5. how fast to act          -> ACT NOW vs WATCH, auction time left

WHAT THIS MODULE REFUSES TO DO
------------------------------
* **No blended score.** There is no 0-100 "deal score" and there never
  will be. A single number would hide exactly the disagreement that
  matters ("great discount, terrible comp") behind an average, and the
  whole point of the CardPro 2.0 pass is that a confident-looking number
  can be worthless. Tags, confidence and risks stay separate columns
  that a human combines.
* **No buy instruction.** Nothing here says "buy". Sections say where to
  look first; the Risks line says what would make you wrong. The
  decision is yours and the report's job is to hand you the evidence.
* **No arithmetic the caller did not supply.** Market values, discounts,
  economics and max rational bids arrive already computed on the
  ``Listing``. This module classifies and renders them. The only numbers
  it derives are the Best Offer trio, which are flat percentages off the
  market value and are printed with their arithmetic stated on the line
  below (see ``_offer_block``).
* **No number the data does not support.** No comp means the Market line
  says so in English and the Discount line does not exist. Unknown
  shipping is never $0; it is "shipping unknown -- actual cost may be
  higher".

Purity: no I/O, no clock (``run_date`` is passed in), no randomness, no
mutation of the input list. Same input, byte-identical output, so the
email is diffable day to day.
"""
from __future__ import annotations

import re
import textwrap
from collections import OrderedDict
from datetime import date
from typing import Optional

from src import desirability, focus, reasons
from src.card_identity import NEGATIVE_SIGNAL_LABELS, CardIdentity
from src.models import Listing

#: Where the listing came from, shown next to the link. One source today,
#: but "eBay (saved-search alert)" and a future marketplace must never be
#: indistinguishable in the email -- the discovery path is part of how much
#: the listing's data can be trusted.
SOURCE_LABELS = {"ebay": "eBay", "ebay-alert": "eBay (saved-search alert)"}


#: Comp levels in plain English. The report must never print a level's
#: internal name: "same_set" tells you nothing, "same year/set, parallel
#: unconfirmed" tells you exactly how much of the card was matched, which
#: is the difference between a comp you can act on and one you cannot.
#: Keys match comps.LEVEL_SPECS; unknown levels fall back to their raw name
#: rather than being hidden.
COMP_LEVEL_LABELS = {
    "exact": "exact card + grade",
    "same_card": "same card + grade, card number unconfirmed",
    "same_set": "same year/set, parallel unconfirmed",
    "family": "same card family (context only)",
    "price_tier": "price-bracket estimate (context only)",
}

#: Section keys, in render order. A listing lands in at most one of these,
#: resolved top-down -- except TARGET_HITS, which is allowed to duplicate
#: (see classify_sections).
SECTION_ACT_NOW = "act_now"
SECTION_TOP_OPPORTUNITIES = "top_opportunities"
SECTION_TARGET_HITS = "target_hits"
SECTION_INVESTMENT = "investment_watchlist"
SECTION_AUCTIONS = "auctions_ending_soon"
SECTION_OFFERS = "offer_opportunities"
SECTION_WATCH = "watch"
SECTION_NEEDS_REVIEW = "needs_review"
SECTION_PRICE_DROPS = "price_drops"

SECTION_ORDER = (
    SECTION_ACT_NOW,
    SECTION_TOP_OPPORTUNITIES,
    SECTION_TARGET_HITS,
    SECTION_INVESTMENT,
    SECTION_AUCTIONS,
    SECTION_OFFERS,
    SECTION_WATCH,
    SECTION_NEEDS_REVIEW,
    SECTION_PRICE_DROPS,
)

#: The order sections claim slots from the email's length budget. NOT the
#: same list as SECTION_ORDER, which is render order, and the difference is
#: deliberate: you bid on cheap auctions, so an auction you could still win
#: is worth more of a capped email than a fixed-price near-miss, even
#: though the auctions section prints below TOP OPPORTUNITIES. NEEDS REVIEW
#: goes last because it says of itself that it is not a recommendation --
#: when something has to be cut, the not-a-recommendation section is what
#: gets cut. Every section key must appear here; focus.trim appends any
#: that don't, but silently, and a section that fell off this list would
#: quietly become the lowest priority in the report.
BUDGET_ORDER = (
    SECTION_ACT_NOW,
    SECTION_TOP_OPPORTUNITIES,
    SECTION_TARGET_HITS,
    SECTION_AUCTIONS,
    SECTION_OFFERS,
    SECTION_INVESTMENT,
    SECTION_WATCH,
    SECTION_PRICE_DROPS,
    SECTION_NEEDS_REVIEW,
)

#: Sections that never take a second helping from the length budget.
#: NEEDS REVIEW says of itself that it is not a recommendation, and on a
#: quiet day it is also comfortably the largest thing CardPro produces --
#: on a real August corpus it was 289 listings, every one of them a
#: could-not-value. Letting it absorb the leftover slots turns a
#: 40-listing email into 40 blocks of "we could not value this", which is
#: the long email this cap exists to stop. It keeps its first-pass share
#: and the rest of the day's budget goes unspent rather than to
#: not-recommendations.
BUDGET_NO_LEFTOVERS = (SECTION_NEEDS_REVIEW,)

SECTION_TITLES = {
    SECTION_ACT_NOW: "\U0001f6a8 ACT NOW",
    SECTION_TOP_OPPORTUNITIES: "⭐ TOP OPPORTUNITIES",
    SECTION_TARGET_HITS: "\U0001f3af TARGET CARD HITS",
    SECTION_INVESTMENT: "\U0001f4c8 INVESTMENT WATCHLIST",
    SECTION_AUCTIONS: "\U0001f528 AUCTIONS ENDING SOON",
    SECTION_OFFERS: "\U0001f4ac OFFER OPPORTUNITIES",
    SECTION_WATCH: "\U0001f440 WATCH",
    SECTION_NEEDS_REVIEW: "\U0001f9ea LOW CONFIDENCE / NEEDS REVIEW",
    SECTION_PRICE_DROPS: "\U0001f4c9 PRICE DROPS",
}

#: One line under each heading saying what the section means. These are
#: load-bearing, not decoration: "TARGET CARD HITS" without its subtitle
#: reads as "these are deals", which is precisely the confusion between
#: "the card I asked for" and "this is underpriced" that the section split
#: exists to prevent.
SECTION_SUBTITLES = {
    SECTION_ACT_NOW: (
        "Flag-eligible comps, a discount and a dollar figure big enough that waiting "
        "costs you something. Still not a buy instruction -- read the Risks line."
    ),
    SECTION_TOP_OPPORTUNITIES: (
        "Below market against a comp CardPro is willing to stand behind."
    ),
    SECTION_TARGET_HITS: (
        "Cards from your target list. A target hit means \"the card you asked for, at the "
        "price you set\" -- it is NOT a claim that the card is underpriced. Cards here may "
        "also appear in a section above; that is deliberate, they answer different questions."
    ),
    SECTION_INVESTMENT: (
        "Young-core players you are holding for the long run, where the comp is too weak "
        "to call it an act-now buy but the card is still worth your attention."
    ),
    SECTION_AUCTIONS: (
        "A current bid is NOT a price and nothing in this section is a confirmed deal. "
        "The useful question is not \"is this cheap\" but \"what is the most I can pay\"."
    ),
    SECTION_OFFERS: (
        "Not deals at the asking price -- but they accept offers, and a negotiated price "
        "could get there. Offer figures are flat percentages off market, stated on each card."
    ),
    SECTION_WATCH: (
        "Real, but not strong enough to act on: low confidence, or close to your thresholds "
        "without clearing them."
    ),
    SECTION_NEEDS_REVIEW: (
        "NOT recommendations. Shown so that nothing disappears silently: the comp is "
        "context-only or missing, or the card's identity is not trustworthy enough to value. "
        "Any number below is context, not a valuation."
    ),
    SECTION_PRICE_DROPS: (
        "Seen before, cheaper now. A price drop is a change, not a verdict."
    ),
}

#: Defaults for the immediate-alert tier. Deliberately conservative: an
#: ACT NOW that fires often is an ACT NOW nobody reads.
DEFAULT_IMMEDIATE_MIN_SAVINGS = 150.0
DEFAULT_IMMEDIATE_MIN_DISCOUNT_PCT = 40.0
DEFAULT_ENDING_SOON_HOURS = 24

#: How close to your discount threshold a non-opportunity has to be to earn
#: a WATCH slot. 0.75 means "within a quarter of the way off" -- close
#: enough to be worth an eye, far enough that WATCH doesn't become a dump.
WATCH_CLOSE_FRACTION = 0.75

#: Best Offer suggestions. The ceiling is derived from YOUR discount
#: threshold -- the most you can pay and still clear your own gate -- and
#: the other two step down from it, so aggressive < fair < maximum always.
#: (Anchoring each figure independently to market value produced the
#: incoherent "fair $279 | maximum $244" at a 30% threshold.) Not modelled,
#: not negotiation advice: arithmetic you would do yourself, shown with its
#: basis on the next line.
OFFER_FAIR_BELOW_MAX_PCT = 5.0
OFFER_AGGRESSIVE_BELOW_MAX_PCT = 15.0

#: Sample size below which a comp is called thin in the report. Matches the
#: threshold comps.assess_comp_match uses to downgrade confidence, so the
#: two never disagree in front of the user.
THIN_SAMPLE_N = 5

#: The coverage section is a footnote, not a chapter: 20 players x 7 slices
#: printed ~140 lines and buried the cards. Hard caps, so a growing
#: watchlist can never take the report over again.
SEARCH_COVERAGE_MAX_LINES = 12
SEARCH_COVERAGE_MAX_NAMED_PLAYERS = 6

#: Assumption sentences that carry one card's dollar figures. Every one of
#: them is true, but each belongs to a single listing, and a footer holding
#: four different "Inbound shipping of $X" lines and two resale-haircut
#: lines with different dollar amounts reads as self-contradiction. Matched
#: by prefix against economics.py's wording; if that wording ever changes,
#: the failure mode is a per-card sentence reappearing in the footer, and
#: only when it is identical across every card -- visible, and harmless.
PER_CARD_ASSUMPTION_PREFIXES = (
    "Inbound shipping",
    "A resale haircut",
    "No resale haircut",
    "Estimated market value is",
    "Acquisition cost is",
)

#: Assumptions footer cap. The assumption sentences are near-identical
#: across listings, so the deduped list is normally 5-6 lines; the cap only
#: bites on a pathological run and keeps the email skimmable.
MAX_ASSUMPTION_LINES = 10

#: Desirability attributes the tag row does not print. ``graded`` is
#: already stated in full two fields to the left of the tags ("-- PSA 9"),
#: and the same fact twice on one line makes the row harder to scan, not
#: more informative. Everything else desirability establishes gets a tag.
TAG_ROW_OMITTED_ATTRIBUTES = (desirability.GRADED,)

#: Fixed width for the rule under a section heading.
SECTION_RULE_WIDTH = 60

_INDENT = "   "
_LABEL_WIDTH = 12
_WRAP_WIDTH = 92


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------


def _money(value) -> str:
    """Money, or the word "unknown". Never "$0.00" for a missing number --
    that is the shipping bug that started all of this."""
    if value is None:
        return "unknown"
    return "${:,.2f}".format(value)


def _pct(value) -> str:
    if value is None:
        return "unknown"
    return "{:.0f}%".format(value)


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    """"1 auction" / "2 auctions". Trivial, but the counts line is the first
    thing read every morning and "1 opportunit(y/ies)" is the kind of detail
    that makes a tool feel unfinished."""
    if count == 1:
        return "{} {}".format(count, singular)
    return "{} {}".format(count, plural if plural is not None else singular + "s")


def _pct1(value) -> str:
    """Discount percentages, to one decimal.

    Not cosmetic. At zero decimals a listing 29.8% under market printed
    "(30%)" directly above "close to your 30% threshold without clearing
    it", and a report that appears to contradict itself costs more trust
    than the extra character costs space. Rates that are configuration
    rather than measurement (ROI, fee percentages) keep ``_pct``.
    """
    if value is None:
        return "unknown"
    return "{:.1f}%".format(value)


def _days(age) -> str:
    if age is None:
        return "unknown age"
    if age == 0:
        return "today"
    if age == 1:
        return "1 day old"
    return "{} days old".format(age)


def _field_line(label: str, text: str) -> str:
    """One "Label      value" line, wrapped so long values stay in their
    column instead of running off the right of a phone screen."""
    return textwrap.fill(
        text,
        width=_WRAP_WIDTH,
        initial_indent=_INDENT + label.ljust(_LABEL_WIDTH),
        subsequent_indent=_INDENT + " " * _LABEL_WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _raw_line(label: str, text: str) -> str:
    """Unwrapped variant, for URLs -- wrapping a link breaks it."""
    return _INDENT + label.ljust(_LABEL_WIDTH) + text


def _link_line(deal) -> str:
    """The link, plus which source it came from. One source today, but the
    discovery path is part of how much a listing's data can be trusted, so
    it is never left implicit."""
    return _raw_line("Link", "{}  ({})".format(deal.url, SOURCE_LABELS.get(deal.source, deal.source)))


def _short_title(title: str, limit: int = 76) -> str:
    if title is None:
        return "(no title)"
    if len(title) <= limit:
        return title
    return title[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# reading a Listing
# ---------------------------------------------------------------------------


def _stats(deal: Listing):
    match = deal.comp_match
    return getattr(match, "stats", None) if match is not None else None


def _confidence(deal: Listing) -> Optional[str]:
    """Confidence comes from the comp match and nowhere else. There is no
    separate report-level judgment call -- inventing one here would be the
    black-box score by another name."""
    match = deal.comp_match
    if match is None:
        return None
    return getattr(match, "confidence", None)


def _has_market(deal: Listing) -> bool:
    return deal.market_value is not None


def _grade_text(deal: Listing) -> str:
    if deal.card_type != "graded":
        return "raw/ungraded"
    if deal.grader and deal.grade:
        return "{} {}".format(deal.grader, deal.grade)
    return "graded (grader/grade unknown)"


def _identity_description(identity: Optional[CardIdentity]) -> Optional[str]:
    """"2024 Panini Prizm Silver #301" from whatever was confidently
    extracted, or None if nothing was. Never a guess: every field here is
    either a confident extraction or absent (see card_identity.py)."""
    if identity is None:
        return None
    parts = []
    if identity.year.value is not None:
        parts.append(str(identity.year.value))
    manufacturer = identity.manufacturer.value
    set_name = identity.set_name.value
    # "Topps" + "Topps Chrome" is "2024 Topps Topps Chrome". The set name
    # already carries the brand in most modern products, so the separate
    # manufacturer field only earns its place when it adds a word.
    if manufacturer is not None and not (
        set_name is not None and str(set_name).strip().lower().startswith(str(manufacturer).strip().lower())
    ):
        parts.append(str(manufacturer))
    if set_name is not None:
        parts.append(str(set_name))
    if identity.parallel.value is not None:
        parts.append(str(identity.parallel.value))
    if identity.card_number.value is not None:
        parts.append("#{}".format(identity.card_number.value))
    if identity.serial_number.value is not None:
        parts.append("({})".format(identity.serial_number.value))
    return " ".join(parts) if parts else None


def _print_run(deal: Listing) -> Optional[int]:
    identity = deal.card_identity
    field = getattr(identity, "print_run", None) if identity is not None else None
    return field.value if field is not None else None


def _tags(deal: Listing) -> list:
    """Attribute tags. Deliberately never combined with each other or with
    a number -- see the module docstring on the forbidden blended score.

    The attributes themselves come from ``desirability`` and are not
    re-derived here. The report used to read the identity fields directly,
    which meant "what makes this card interesting" had two definitions --
    one for the gate that decides a cheap card is worth surfacing, one for
    the row of tags that is supposed to say why. Two definitions of the
    same idea drift, and the drift shows up as a card in the report with
    no tag explaining what it is doing there.
    """
    tags = []
    # Not a card attribute: this is about the player, so it comes from the
    # tier and stays out of desirability (which is deliberately opinion-free).
    if deal.player_tier == "young_core":
        tags.append("YOUNG CORE")
    print_run = _print_run(deal)
    for attribute in deal.desirable_attributes or ():
        if attribute in TAG_ROW_OMITTED_ATTRIBUTES:
            continue
        tag = desirability.ATTRIBUTE_TAGS.get(attribute, str(attribute).upper())
        # "#'d /99" beats "#'d": the print run is the whole point of the
        # tag, and it is the one number a collector scans the row for.
        if attribute == desirability.SERIAL_NUMBERED and print_run is not None:
            tag += " /{}".format(print_run)
        tags.append(tag)
    return tags


def _title_line(deal: Listing) -> Optional[str]:
    """The raw seller title, but only when the headline did not already show
    it in full. The title is the primary evidence -- every parsed field in
    this report is an inference from it -- so it earns a line whenever the
    headline is showing a parsed description or a shortened version."""
    if _identity_description(deal.card_identity) is None and deal.title == _short_title(deal.title):
        return None
    return _field_line("Title", deal.title)


def _headline(index: int, deal: Listing) -> str:
    """"1. Player -- card -- grade   [TAGS]  [TARGET: ...]"."""
    description = _identity_description(deal.card_identity) or _short_title(deal.title)
    parts = ["{}. {}".format(index, deal.player), description, _grade_text(deal)]
    line = " -- ".join(parts)
    tags = _tags(deal)
    if tags:
        line += "   [{}]".format(" + ".join(tags))
    if deal.target_hit is not None:
        line += "  [TARGET: {}]".format(getattr(deal.target_hit, "label", "MATCH"))
    return line


# ---------------------------------------------------------------------------
# the lines every block is made of
# ---------------------------------------------------------------------------


def _cost_text(deal: Listing) -> str:
    if deal.price is None:
        return "price could not be read from the listing"
    if deal.shipping_price is None:
        return "{} + shipping unknown -- actual cost may be higher".format(_money(deal.price))
    if deal.shipping_price == 0:
        return "{} + free shipping = {} total".format(_money(deal.price), _money(deal.total_cost))
    return "{} + {} shipping = {} total".format(
        _money(deal.price), _money(deal.shipping_price), _money(deal.total_cost)
    )


def _market_text(deal: Listing) -> str:
    """The Market line always discloses HOW the number was reached: level in
    English, sample size, asking-vs-sold basis, range, and recency. A bare
    "market $348" is the shape of claim this project no longer makes."""
    stats = _stats(deal)
    if deal.market_value is None or stats is None:
        return (
            "no comparable listing was found for this exact card and grade, so CardPro "
            "has no value estimate -- there is no discount to report"
        )
    level = getattr(deal.comp_match, "level", None)
    level_label = COMP_LEVEL_LABELS.get(level, level or "unknown comp level")
    basis = "sold comps" if stats.basis == "sold" else "asking comps"
    text = "{}  -- {}, {} {}, range {}-{}, newest {}, oldest {}".format(
        _money(deal.market_value),
        level_label,
        stats.sample_size,
        basis,
        _money(stats.minimum),
        _money(stats.maximum),
        _days(stats.age_days_newest),
        _days(stats.age_days_oldest),
    )
    if stats.trimmed_count:
        text += ", {} trimmed".format(_plural(stats.trimmed_count, "outlier"))
    return text


def _is_real_comp(deal: Listing) -> bool:
    """A comp solid enough to base a number on. Deliberately stricter than
    "has a market value": suggesting an offer price off a context-only comp
    would be the circular price-tier valuation wearing a negotiation hat --
    "offer 30% below a bucket defined by price" is not advice, it is noise.
    Listings whose only comp is context-only belong in NEEDS REVIEW."""
    match = deal.comp_match
    return match is not None and getattr(match, "flag_eligible", False) and _has_market(deal)


#: What replaces the Discount and Economics lines when the only comp is
#: context-only. Not a hedge on a number -- the number does not get printed.
CONTEXT_ONLY_TEXT = (
    "the only comp is a context-only level, which cannot be evidence about price -- a "
    "price-bracket bucket is defined BY price, so the cheap end of it always looks cheap. "
    "CardPro has no valuation for this card and there is no discount to report."
)


def _discount_text(deal: Listing) -> Optional[str]:
    """None when there is nothing honest to say. No comp means no discount
    line at all -- not "0%", not "n/a"."""
    if not _has_market(deal) or deal.dollar_savings is None:
        return None
    if not _is_real_comp(deal):
        # A discount computed off a context-only comp is the audit's failure
        # mode #1 verbatim: an $18 card inside a bucket defined as "$25-$100"
        # reported as 51% off. It reaches here because the pipeline fills in
        # market_value and dollar_savings before it checks flag-eligibility,
        # and a target hit gets a headline section regardless of claims. The
        # parenthetical on the Market line is not enough -- a number that
        # cannot be stood behind must not be printed at all.
        return None
    if deal.dollar_savings < 0:
        return "{} ABOVE market ({} over)".format(
            _money(abs(deal.dollar_savings)), _pct1(abs(deal.pct_under_market or 0.0))
        )
    if deal.pct_under_market is None:
        text = "{} below market".format(_money(deal.dollar_savings))
    else:
        text = "{} below market ({})".format(
            _money(deal.dollar_savings), _pct1(deal.pct_under_market)
        )
    if deal.shipping_price is None:
        # total_cost falls back to price alone when shipping is unknown, so
        # this figure is computed against a cost the Cost line has just
        # declined to state. Naming it to the cent two lines later, with no
        # caveat, is the report contradicting itself.
        text += " -- before shipping, which is unknown, so this is a ceiling"
    return text


#: What the Economics line says about a card that cannot be flipped.
#: Printing "-10% ROI" instead would be arithmetic presented as a warning:
#: at $4-$8 the postage and fees exceed the entire spread, so the negative
#: figure says nothing about the card -- it says the card is cheap, which
#: the Cost line already said. The reader's question at that price is not
#: "how much would I make", it is "is this worth owning".
COLLECTOR_BUY_TEXT = (
    "collector buy -- at this price postage and fees exceed the spread, so there is no "
    "flip here"
)


def _economics_text(deal: Listing) -> Optional[str]:
    """Omitted entirely when the pipeline computed no economics. Present, it
    always ends in a pointer to the assumptions footer, so the profit figure
    is never a bare unexplained number.

    A listing the pipeline marked ``resale_uneconomic`` gets the honest
    one-liner instead of a negative-ROI figure. The *discount* is untouched
    either way: the card really is below market, and that is exactly why it
    is being shown.

    A listing whose only comp is context-only gets no line at all, for the
    same reason it gets no discount line -- see CONTEXT_ONLY_TEXT.
    """
    if not _is_real_comp(deal):
        # Same reason as _discount_text: profit and ROI are computed off the
        # market value, so a context-only comp makes them a restatement of
        # the price wearing an accountant's hat.
        return None
    if deal.resale_uneconomic:
        return COLLECTOR_BUY_TEXT
    econ = deal.economics
    if econ is None:
        return None
    tail = "[assumptions in footer]"
    if not getattr(econ, "shipping_known", True):
        # economics.evaluate emits this caveat as an assumption, but the
        # footer filters per-card assumptions out, so the one assumption
        # that can flip the sign of the profit was the one the reader never
        # saw. On a $22 card with a $3.44 expected profit, $5 of postage
        # makes it a $1.56 loss.
        tail = "before unknown inbound shipping, which could make it a loss " + tail
    return (
        "sell ~{} -> fees {} + ship/supplies {} -> net {} -> profit {} ({} ROI) "
        "{}".format(
            _money(econ.expected_sale_price),
            _money(econ.selling_fees),
            _money(econ.outbound_costs),
            _money(econ.expected_net_proceeds),
            _money(econ.expected_profit),
            _pct(econ.roi_pct),
            tail,
        )
    )


#: Attributes whose label reads as an adjective ("serial numbered"), which
#: must not take "a" in front of it. Purely an English rule about the labels
#: desirability publishes -- it adds no attribute and hides none, and a name
#: that is not listed simply gets the article.
ADJECTIVAL_ATTRIBUTES = (desirability.SERIAL_NUMBERED, desirability.GRADED)


def _article_for(lead_attribute, described: str) -> str:
    """"a " / "an " / "" for the front of a described attribute list."""
    if lead_attribute in ADJECTIVAL_ATTRIBUTES:
        return ""
    return "an " if described[:1].lower() in "aeiou" else "a "


def _cheap_find_text(deal: Listing) -> Optional[str]:
    """One sentence saying why a $4 line item is not noise.

    Only for listings below the cheap-card ceiling, where the price alone
    tells the reader nothing: the cheap end of the hobby is mostly commodity,
    so a card at that price has to name what makes a copy of it wanted.
    Above the ceiling the line is omitted -- the price is its own argument.

    Omitted too when desirability established nothing, because there is then
    no honest answer to "why this is worth $4" (and such a listing is
    rejected upstream as a common card anyway).
    """
    if not deal.is_cheap or deal.price is None:
        return None
    attributes = deal.desirable_attributes or ()
    described = desirability.describe(attributes)
    if not described:
        return None
    return "{} card, but it is {}{} -- not a base common".format(
        _money(deal.price), _article_for(attributes[0], described), described
    )


def _confidence_text(deal: Listing) -> str:
    """Confidence must state WHY, never just the word.

    The asking-vs-sold caveat leads whenever it applies, because it is the
    single most important thing to know about every number in this report:
    100% of CardPro's comp corpus is what sellers are *asking*, not what
    buyers *paid*.
    """
    stats = _stats(deal)
    confidence = _confidence(deal)
    if stats is None or confidence is None:
        return "NONE -- no comp backs this listing, so there is no confidence to report"

    why = []
    if stats.basis == "sold":
        why.append("based on confirmed sold prices")
    else:
        why.append(
            "asking-price comps only (what sellers want, not what buyers paid), no "
            "confirmed sold data on this path"
        )
    level = getattr(deal.comp_match, "level", None)
    why.append(COMP_LEVEL_LABELS.get(level, level or "unknown comp level"))
    why.append("n={}".format(stats.sample_size))
    if stats.sample_size < THIN_SAMPLE_N:
        why.append("thin sample")
    if stats.is_stale:
        why.append("newest comp is {}".format(_days(stats.age_days_newest)))
    if stats.is_dispersed:
        why.append("comps disagree widely (dispersion {:.2f})".format(stats.dispersion))
    if stats.is_concentrated:
        # The most common downgrade by far and the only one that had no
        # sentence: it fires on 866 of 907 production listings, so an exact
        # grade-matched comp with n=9 was printing "LOW" with nothing in the
        # line to account for the fall. A confidence ladder nobody can follow
        # reads as a black box, which is what this line exists to prevent.
        why.append(
            "all {} comps landed on {} date(s) spanning {} day(s) -- one snapshot, "
            "not independent readings".format(
                stats.sample_size, stats.distinct_dates, stats.span_days
            )
        )
    return "{} -- {}".format(confidence.upper(), "; ".join(why))


def _risks(deal: Listing) -> list:
    """Everything true about this listing that could make the numbers above
    wrong, worst first.

    Assembled from facts on the Listing, never from suspicion -- and the line
    is omitted upstream when the list comes back empty, because a "Risks:
    none" line trains you to stop reading the ones that matter.

    ORDER IS THE POINT. This used to be an append-as-you-go list, so on a
    card with six risks "shipping unknown" and "this may be a reprint" got
    the same weight in the same run-on sentence, and the reader had to rank
    them. They are not the same kind of statement, so they go in tiers:

      1. It may not be the card. A reprint, a custom, a lot, a truncated
         title, two players in the name -- if any of these is true the rest
         of the block is about a different object than the one you would
         receive. Nothing else can outrank that.
      2. The price may not be the price. An unknown listing type means the
         number could be a current bid; unknown shipping means the total is
         a floor.
      3. The comp may not be the market. Thin, stale, dispersed,
         time-concentrated. Real caveats, but caveats about how well we
         measured a real thing, not about whether the thing is real.
    """
    stats = _stats(deal)
    covered = set()
    identity_risks = []
    price_risks = []
    comp_risks = []

    for signal in deal.negative_signals or ():
        identity_risks.append(NEGATIVE_SIGNAL_LABELS.get(signal, signal))
    if deal.title_truncated:
        identity_risks.append(
            "eBay truncated the title, so the grade shown may not be the real grade"
        )
    if len(deal.matched_players or ()) > 1:
        identity_risks.append(
            "{} watchlist players in the title -- may be a multi-player card with no single "
            "market".format(len(deal.matched_players))
        )

    if deal.listing_type == "unknown":
        price_risks.append(
            "listing type unknown -- if this is an auction the price shown is a current bid, "
            "not an asking price"
        )
    if deal.shipping_price is None:
        price_risks.append("shipping unknown -- actual cost may be higher")

    if stats is not None:
        if stats.sample_size < THIN_SAMPLE_N:
            comp_risks.append("thin sample (n={})".format(stats.sample_size))
            covered.add(reasons.Reason.THIN_SAMPLE)
        if stats.is_stale:
            comp_risks.append("stale comps -- newest is {}".format(_days(stats.age_days_newest)))
            covered.add(reasons.Reason.STALE_COMPS)
        if stats.is_dispersed:
            comp_risks.append(
                "comps disagree with each other (dispersion {:.2f}), so one median hides a "
                "wide spread".format(stats.dispersion)
            )
            covered.add(reasons.Reason.DISPERSED_COMPS)

    for blocked in getattr(deal.comp_match, "blocked_reasons", ()) or ():
        if blocked in covered:
            continue
        try:
            comp_risks.append(reasons.label(blocked))
        except reasons.UnknownReasonError:
            comp_risks.append(str(blocked))

    return identity_risks + price_risks + comp_risks


# ---------------------------------------------------------------------------
# the blocks
# ---------------------------------------------------------------------------


def _thesis_block(index: int, deal: Listing, ending_soon_hours: float = DEFAULT_ENDING_SOON_HOURS) -> str:
    """The full per-card thesis, for the sections where a card is being put
    forward as worth your attention. Auctions never use this block -- see
    ``_auction_block`` -- because a current bid must never be laid out in
    the same shape as a price."""
    if deal.is_auction:
        return _auction_block(index, deal, ending_soon_hours)

    lines = [_headline(index, deal), _field_line("Cost", _cost_text(deal))]
    lines.append(_field_line("Market", _market_text(deal)))
    if _has_market(deal) and not _is_real_comp(deal):
        lines.append(_field_line("Context", CONTEXT_ONLY_TEXT))
    discount = _discount_text(deal)
    if discount:
        lines.append(_field_line("Discount", discount))
    econ = _economics_text(deal)
    if econ:
        lines.append(_field_line("Economics", econ))
    cheap = _cheap_find_text(deal)
    if cheap:
        lines.append(_field_line("Cheap find", cheap))
    lines.append(_field_line("Confidence", _confidence_text(deal)))
    risks = _risks(deal)
    if risks:
        lines.append(_field_line("Risks", "; ".join(risks)))
    if deal.target_hit is not None:
        lines.append(_field_line("Target", _target_text(deal)))
    title_line = _title_line(deal)
    if title_line:
        lines.append(title_line)
    lines.append(_link_line(deal))
    return "\n".join(lines)


def _target_text(deal: Listing) -> str:
    hit = deal.target_hit
    label = getattr(hit, "label", "MATCH")
    target = getattr(hit, "target", None)
    name = getattr(target, "label", None) or "your target list"
    threshold = getattr(hit, "threshold", None)
    if not getattr(hit, "price_known", True):
        # "We do not know what it costs" is not "it costs more than every
        # band you set". Saying the second for the first is a price claim
        # made out of an unknown.
        return '"{}" -- the card you asked for is listed, but its price could not be read'.format(name)
    if getattr(hit, "in_buy_zone", False) and threshold is not None:
        text = '"{}" -- {}, at or below your {} threshold of {}'.format(
            name, label, label.lower(), _money(threshold)
        )
        if deal.shipping_price is None:
            # The band was matched against price alone, because that is what
            # total_cost falls back to when shipping is unknown. Saying it
            # clears your threshold without saying that is how a $149 card
            # with $10 postage reports as inside a $150 buy zone.
            text += "; shipping unknown, so the real cost may put it above the band"
        return text
    return '"{}" -- the card you asked for is listed, but above every price band you set'.format(name)


def _auction_block(index: int, deal: Listing, ending_soon_hours: float = DEFAULT_ENDING_SOON_HOURS) -> str:
    """Auctions get their own shape on purpose.

    The number on an auction is a *current bid*: it is not what the card
    costs, it is the floor under what the card will cost. Rendering it in
    the same "Cost / Discount" frame as a Buy It Now is how the old
    pipeline turned a $40 opening bid into a reported 60%-off deal
    (audit failure mode #7). So the label says CURRENT BID, the discount is
    explicitly scoped to "at this bid", and the max rational bid -- the one
    number that makes an auction safe to act on -- gets its own line.
    """
    lines = [_headline(index, deal)]
    bid_text = "{} -- this is a CURRENT BID, not an asking price".format(_money(deal.price))
    if deal.shipping_price is None:
        bid_text += "; shipping unknown -- actual cost may be higher"
    elif deal.shipping_price:
        bid_text += "; plus {} shipping".format(_money(deal.shipping_price))
    lines.append(_field_line("Current bid", bid_text))

    bids = _plural(deal.bid_count, "bid") if deal.bid_count is not None else "bid count unknown"
    time_left = deal.time_left_text or "time left unknown"
    bidding = "{}, {} remaining".format(bids, time_left)
    hours_left = _parse_time_left_hours(deal.time_left_text)
    if hours_left is not None and hours_left <= ending_soon_hours:
        bidding += " -- inside your {:.0f}h ending-soon window".format(ending_soon_hours)
    lines.append(_field_line("Bidding", bidding))
    lines.append(_field_line("Market", _market_text(deal)))
    if _has_market(deal) and not _is_real_comp(deal):
        lines.append(_field_line("Context", CONTEXT_ONLY_TEXT))

    if deal.max_rational_bid is not None:
        max_bid_text = "{} -- the most you can pay and still keep your margin. Above this, stop.".format(
            _money(deal.max_rational_bid)
        )
        if not getattr(deal, "max_rational_bid_shipping_known", True):
            # Computed with shipping treated as $0 because it is unknown, so
            # it is an upper bound that is too high by exactly the unknown
            # shipping -- see economics.max_rational_bid. Printing it as a
            # figure is how you overbid.
            max_bid_text += (
                " Shipping is unknown, so this assumes $0 postage and your real"
                " ceiling is lower by whatever it turns out to be."
            )
        lines.append(_field_line("Max bid", max_bid_text))
    else:
        lines.append(
            _field_line(
                "Max bid",
                "not computed -- without a market value there is no rational ceiling to name",
            )
        )

    discount = _discount_text(deal)
    if discount:
        lines.append(
            _field_line(
                "At this bid",
                "{} -- true only at the current bid, which will very likely rise before "
                "close. This is not a confirmed deal.".format(discount),
            )
        )
    lines.append(_field_line("Confidence", _confidence_text(deal)))
    risks = _risks(deal)
    if risks:
        lines.append(_field_line("Risks", "; ".join(risks)))
    title_line = _title_line(deal)
    if title_line:
        lines.append(title_line)
    lines.append(_link_line(deal))
    return "\n".join(lines)


def offer_trio(market_value: float, threshold_pct: float) -> tuple:
    """(aggressive, fair, maximum) offer prices for a Best Offer listing.

    ``maximum`` is the anchor: the highest price that still clears the
    discount threshold you configured, so an accepted offer at that number
    is, by your own definition, still a deal. ``fair`` and ``aggressive``
    step down from it by fixed percentages.

    Deriving all three from the ceiling is what makes the trio monotonic by
    construction -- aggressive < fair < maximum at every threshold. Pegging
    each one independently to market value did not: at a 30% threshold it
    produced a "fair" offer higher than the "maximum", which is nonsense
    dressed up as arithmetic.

    Nothing here is modelled or learned. It is the sum you would do
    yourself, done once, with the basis printed next to it.
    """
    maximum = market_value * (1.0 - threshold_pct / 100.0)
    fair = maximum * (1.0 - OFFER_FAIR_BELOW_MAX_PCT / 100.0)
    aggressive = maximum * (1.0 - OFFER_AGGRESSIVE_BELOW_MAX_PCT / 100.0)
    return aggressive, fair, maximum


def _offer_block(index: int, deal: Listing, threshold_pct: float) -> str:
    if deal.is_auction:
        # An auction's number is a CURRENT BID. Rendering it under "Asking"
        # or "Cost" is how a $40 opening bid became a reported 60%-off deal
        # (audit failure mode #7). _thesis_block and _compact_block have
        # always had this guard; these two did not, and were safe only
        # because classify_sections happens to claim auctions before it
        # reaches their loops. Reorder those loops -- a plausible edit -- and
        # the defect _auction_block exists to prevent comes straight back.
        return _auction_block(index, deal)
    lines = [_headline(index, deal)]
    lines.append(_field_line("Asking", _cost_text(deal)))
    lines.append(_field_line("Market", _market_text(deal)))
    if _has_market(deal) and not _is_real_comp(deal):
        lines.append(_field_line("Context", CONTEXT_ONLY_TEXT))
    discount = _discount_text(deal)
    if discount:
        lines.append(_field_line("Discount", "{} at the asking price".format(discount)))

    aggressive, fair, maximum = offer_trio(deal.market_value, threshold_pct)
    lines.append(
        _field_line(
            "Offer",
            "aggressive {} | fair {} | maximum {}".format(
                _money(aggressive), _money(fair), _money(maximum)
            ),
        )
    )
    basis = (
        "maximum is the highest price that still clears your {:.0f}% discount threshold "
        "against a market estimate of {}; fair is {:.0f}% below it and aggressive {:.0f}% "
        "below it. These are total-cost figures".format(
            threshold_pct,
            _money(deal.market_value),
            OFFER_FAIR_BELOW_MAX_PCT,
            OFFER_AGGRESSIVE_BELOW_MAX_PCT,
        )
    )
    if deal.shipping_price:
        basis += "; subtract the {} known shipping from what you actually offer".format(
            _money(deal.shipping_price)
        )
    elif deal.shipping_price is None:
        basis += "; shipping is unknown, so treat them as ceilings"
    lines.append(_field_line("Basis", basis))
    lines.append(_field_line("Confidence", _confidence_text(deal)))
    risks = _risks(deal)
    if risks:
        lines.append(_field_line("Risks", "; ".join(risks)))
    title_line = _title_line(deal)
    if title_line:
        lines.append(title_line)
    lines.append(_link_line(deal))
    return "\n".join(lines)


def _compact_block(index: int, deal: Listing, *, why: Optional[str] = None,
                   ending_soon_hours: float = DEFAULT_ENDING_SOON_HOURS) -> str:
    """Shorter block for the sections that are explicitly not
    recommendations. Still honest -- cost, whatever the comp says, risks --
    just not laid out as a thesis, because there is no thesis."""
    if deal.is_auction:
        return _auction_block(index, deal, ending_soon_hours)
    lines = [_headline(index, deal), _field_line("Cost", _cost_text(deal))]
    lines.append(_field_line("Market", _market_text(deal)))
    if _has_market(deal) and not _is_real_comp(deal):
        lines.append(_field_line("Context", CONTEXT_ONLY_TEXT))
    discount = _discount_text(deal)
    if discount:
        lines.append(_field_line("Discount", discount))
    if why:
        lines.append(_field_line("Why here", why))
    lines.append(_field_line("Confidence", _confidence_text(deal)))
    risks = _risks(deal)
    if risks:
        lines.append(_field_line("Risks", "; ".join(risks)))
    title_line = _title_line(deal)
    if title_line:
        lines.append(title_line)
    lines.append(_link_line(deal))
    return "\n".join(lines)


def _price_drop_block(index: int, deal: Listing) -> str:
    if deal.is_auction:
        # An auction's number is a CURRENT BID. Rendering it under "Asking"
        # or "Cost" is how a $40 opening bid became a reported 60%-off deal
        # (audit failure mode #7). _thesis_block and _compact_block have
        # always had this guard; these two did not, and were safe only
        # because classify_sections happens to claim auctions before it
        # reaches their loops. Reorder those loops -- a plausible edit -- and
        # the defect _auction_block exists to prevent comes straight back.
        return _auction_block(index, deal)
    lines = [_headline(index, deal)]
    if deal.previous_price is not None and deal.price is not None:
        lines.append(
            _field_line(
                "Price drop",
                "was {} -> now {} ({} lower)".format(
                    _money(deal.previous_price),
                    _money(deal.price),
                    _money(deal.previous_price - deal.price),
                ),
            )
        )
    lines.append(_field_line("Cost", _cost_text(deal)))
    lines.append(_field_line("Market", _market_text(deal)))
    if _has_market(deal) and not _is_real_comp(deal):
        lines.append(_field_line("Context", CONTEXT_ONLY_TEXT))
    discount = _discount_text(deal)
    if discount:
        lines.append(_field_line("Discount", discount))
    # Every other block carries this. Its absence here was a copy-paste
    # omission, not a decision: a market value and a discount with nothing
    # saying how far to trust them is the one thing this report is not
    # allowed to print.
    lines.append(_field_line("Confidence", _confidence_text(deal)))
    risks = _risks(deal)
    if risks:
        lines.append(_field_line("Risks", "; ".join(risks)))
    title_line = _title_line(deal)
    if title_line:
        lines.append(title_line)
    lines.append(_link_line(deal))
    return "\n".join(lines)


def _needs_review_why(deal: Listing) -> str:
    match = deal.comp_match
    if match is None or deal.market_value is None:
        return (
            "no comp at any level -- CardPro cannot say what this is worth, and is showing it "
            "rather than dropping it silently"
        )
    if not getattr(match, "flag_eligible", False):
        level = getattr(match, "level", None)
        return (
            "the only comp available is {} -- context, not a valuation, and never allowed to "
            "declare a deal".format(COMP_LEVEL_LABELS.get(level, level or "an unknown level"))
        )
    return "the card's identity is not trustworthy enough to stand behind a valuation"


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def _parse_time_left_hours(text) -> Optional[float]:
    """Hours from eBay's countdown text ("2d 04h", "45m"), or None if it
    cannot be read. None is a real answer and sorts last -- guessing "0
    hours" would put an unknown auction at the top of a section ordered by
    urgency, which is the wrong kind of wrong."""
    if not text:
        return None
    hours = 0.0
    found = False
    for value, unit in re.findall(r"(\d+)\s*([dhm])", str(text).lower()):
        found = True
        number = float(value)
        if unit == "d":
            hours += number * 24.0
        elif unit == "h":
            hours += number
        else:
            hours += number / 60.0
    return hours if found else None


def _auction_sort_key(deal: Listing):
    """Auctions sort by urgency: a known time left first, soonest first,
    then by discount at the current bid. An unreadable countdown sorts last
    rather than being guessed at zero, which would put the listing we know
    least about at the top of the list."""
    hours = _parse_time_left_hours(deal.time_left_text)
    return (
        deal.time_left_text is None,
        hours if hours is not None else float("inf"),
        -(deal.pct_under_market or 0.0),
        deal.id,
    )


def _savings(deal: Listing) -> float:
    return deal.dollar_savings or 0.0


def _clears_immediate(deal: Listing, min_savings: float, min_discount_pct: float) -> bool:
    if deal.dollar_savings is None or deal.pct_under_market is None:
        return False
    return deal.dollar_savings >= min_savings and deal.pct_under_market >= min_discount_pct


def rank_deals(deals: list) -> list:
    """Rank by dollars saved, descending.

    Still the right primary key, and unchanged from CardPro 1.0: percent
    alone lets trivia through (50% off a $5 common is $2.50), so the dollar
    figure is the better "is this worth your time" signal. Percent is shown
    everywhere for context but never sorts. Python's sort is stable, so
    equal savings keep their input order and the email stays diffable.
    """
    return sorted(deals, key=lambda d: d.dollar_savings or 0, reverse=True)


def classify_sections(
    deals: list,
    *,
    threshold_pct: float = 0.0,
    immediate_min_savings: float = DEFAULT_IMMEDIATE_MIN_SAVINGS,
    immediate_min_discount_pct: float = DEFAULT_IMMEDIATE_MIN_DISCOUNT_PCT,
    ending_soon_hours: float = DEFAULT_ENDING_SOON_HOURS,
) -> "OrderedDict":
    """Sort listings into report sections. Separate from rendering because
    this is the part most likely to be wrong, and a classification bug is
    invisible in a diff of formatted text.

    Returns an ``OrderedDict`` with **every** section key present, in
    ``SECTION_ORDER``, mapping to a (possibly empty) list. Always-present
    keys make callers and tests simpler; the renderer omits empty sections
    from the email.

    The rules, and the tradeoffs in them:

    * A listing is claimed by the FIRST section it qualifies for, walking
      ``SECTION_ORDER`` top-down. ``target_hits`` is the one exception: it
      neither claims nor is blocked, because "the card I asked for" and
      "this is underpriced" are different questions and a card can be the
      answer to both.
    * ``top_opportunities`` deliberately excludes low-confidence
      opportunities so that ``watch`` can catch them; without that
      exclusion WATCH could never fire, since every opportunity would be
      claimed higher up.
    * Consequently ``investment_watchlist`` holds young-core opportunities
      that did NOT make ACT NOW or TOP -- i.e. the low-confidence ones,
      which are exactly the long-horizon "worth knowing about, not worth
      acting on today" cases. A young-core card with a strong comp still
      belongs in TOP; splitting strong opportunities by player tier would
      bury the best card of the day under a tier label.
    * ``needs_review`` catches context-only comps AND no-comp-at-all, which
      is the 21% of listings that used to vanish without a trace.

    ``min_savings_dollars`` is deliberately NOT a parameter here: a listing
    that clears the discount threshold but misses the dollar minimum is
    already caught by the percentage rule, so accepting the argument would
    only imply an influence it does not have.
    """
    # Dedupe by LISTING id, not object identity: the same eBay item arriving
    # twice in one run (two saved searches, one card) must not be reported
    # twice in two different sections.
    claimed = set()
    sections = OrderedDict((key, []) for key in SECTION_ORDER)
    ranked = rank_deals(deals)

    def take(key, deal):
        sections[key].append(deal)
        claimed.add(deal.id)

    for deal in ranked:
        if deal.is_opportunity and _confidence(deal) != "low" and _clears_immediate(
            deal, immediate_min_savings, immediate_min_discount_pct
        ):
            take(SECTION_ACT_NOW, deal)

    for deal in ranked:
        if deal.id in claimed:
            continue
        if deal.is_opportunity and _confidence(deal) != "low":
            take(SECTION_TOP_OPPORTUNITIES, deal)

    # Target hits: allowed to duplicate, so no claim is taken or checked.
    target_hits = [deal for deal in ranked if deal.target_hit is not None]
    sections[SECTION_TARGET_HITS] = sorted(
        target_hits,
        key=lambda d: (not getattr(d.target_hit, "in_buy_zone", False), -_savings(d), d.id),
    )

    for deal in ranked:
        if deal.id in claimed:
            continue
        if deal.player_tier == "young_core" and deal.is_opportunity:
            take(SECTION_INVESTMENT, deal)

    auctions = []
    for deal in ranked:
        if deal.id in claimed:
            continue
        if deal.is_auction:
            auctions.append(deal)
            claimed.add(deal.id)
    sections[SECTION_AUCTIONS] = sorted(auctions, key=_auction_sort_key)

    for deal in ranked:
        if deal.id in claimed:
            continue
        if deal.has_best_offer and not deal.is_opportunity and _is_real_comp(deal):
            take(SECTION_OFFERS, deal)

    for deal in ranked:
        if deal.id in claimed:
            continue
        if deal.is_opportunity:  # low confidence, by elimination
            take(SECTION_WATCH, deal)
        elif _is_close_but_under(deal, threshold_pct, immediate_min_savings):
            take(SECTION_WATCH, deal)

    for deal in ranked:
        if deal.id in claimed:
            continue
        if _needs_review(deal):
            take(SECTION_NEEDS_REVIEW, deal)

    for deal in ranked:
        if deal.id in claimed:
            continue
        if deal.is_price_drop:
            take(SECTION_PRICE_DROPS, deal)

    return sections


def _is_close_but_under(deal: Listing, threshold_pct: float, big_dollar_savings: float) -> bool:
    """A real, flag-eligible comp that just missed the bar. Not an
    opportunity, but a near-miss worth an eye rather than a silent drop.

    Two ways to be close, because "close" means different things at
    different price points:

    * within ``WATCH_CLOSE_FRACTION`` of your discount percentage -- the
      ordinary near-miss; and
    * a large amount of money in absolute terms on a modest percentage --
      $500 off a $5,000 slab is 10% and is obviously worth a look, and a
      percentage-only rule would throw it away.

    "Large" is measured against the ACT NOW dollar threshold, not the
    ordinary minimum-savings setting. That distinction matters: an ordinary
    minimum is often $10-25, and at that level the dollar rule swallowed
    every mildly-discounted listing in the run and turned WATCH into a
    dumping ground -- price drops included, which then never reached their
    own section.
    """
    match = deal.comp_match
    if match is None or not getattr(match, "flag_eligible", False):
        return False
    if deal.pct_under_market is None or deal.pct_under_market <= 0:
        return False
    if deal.pct_under_market >= threshold_pct * WATCH_CLOSE_FRACTION:
        return True
    return (deal.dollar_savings or 0.0) >= big_dollar_savings


def _identity_untrustworthy(deal: Listing) -> bool:
    return bool(
        deal.title_truncated
        or (deal.negative_signals or ())
        or len(deal.matched_players or ()) > 1
    )


def _needs_review(deal: Listing) -> bool:
    match = deal.comp_match
    if match is not None and not getattr(match, "flag_eligible", False):
        return True
    if match is None or deal.market_value is None:
        return True
    return _identity_untrustworthy(deal)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


#: Sections that point at an earlier full block instead of repeating it.
#:
#: Only the explicitly-not-a-recommendation ones. They exist so that nothing
#: disappears silently, which is a claim about the card being LISTED, not
#: about it being described twice -- and a card that reached one of these
#: after a headline section has already had every one of its lines printed
#: above. The headline sections never cross-reference: whichever of them
#: prints first is the full block, and they do not overlap each other.
CROSS_REFERENCE_SECTIONS = (SECTION_WATCH, SECTION_NEEDS_REVIEW, SECTION_PRICE_DROPS)


def _why_for(key: str, deal: Listing, threshold_pct: float, min_savings_dollars: float):
    """The one line a cross-referenced section still has to say for itself."""
    if key == SECTION_NEEDS_REVIEW:
        return _needs_review_why(deal)
    if key == SECTION_WATCH:
        return _watch_why(deal, threshold_pct, min_savings_dollars)
    if key == SECTION_PRICE_DROPS and deal.previous_price is not None and deal.price is not None:
        return "was {} -> now {} ({} lower)".format(
            _money(deal.previous_price), _money(deal.price),
            _money(deal.previous_price - deal.price),
        )
    return None


def _plain_title(title: str) -> str:
    """A section title without its emoji, for referring to it mid-sentence."""
    return "".join(ch for ch in title if ch.isascii()).strip()


def _already_shown_block(index: int, deal: Listing, why: Optional[str], seen_in: str) -> str:
    """A card whose full block is already printed above.

    Sections answer different questions, so a card legitimately appears in
    more than one -- a target hit whose only comp is context-only belongs in
    both TARGET CARD HITS and NEEDS REVIEW. What it does not need is the
    same nine lines twice: eight of them were byte-identical and only the
    "why" differed, in an email whose length is a known problem. Print the
    line that is actually new, and point at the rest.
    """
    lines = [_headline(index, deal)]
    if why:
        lines.append(_field_line("Why here", why))
    lines.append(_field_line("Full detail", "shown above under {}".format(seen_in)))
    return "\n".join(lines)


def _render_section(key: str, deals: list, threshold_pct: float, ending_soon_hours: float,
                    min_savings_dollars: float = 0.0, shown_above=None) -> str:
    title = SECTION_TITLES[key]
    # A fixed-width rule rather than one measured from the title: an emoji's
    # rendered width varies by client, so a "measured" underline is ragged
    # in exactly the places it is supposed to be tidy.
    lines = [title, "-" * SECTION_RULE_WIDTH]
    subtitle = SECTION_SUBTITLES.get(key)
    if subtitle:
        lines.append(textwrap.fill(subtitle, width=_WRAP_WIDTH))
    lines.append("")

    for index, deal in enumerate(deals, start=1):
        seen_in = (shown_above or {}).get(deal.id) if key in CROSS_REFERENCE_SECTIONS else None
        if seen_in:
            lines.append(_already_shown_block(index, deal, _why_for(key, deal, threshold_pct,
                                                                   min_savings_dollars), seen_in))
            lines.append("")
            continue
        if key in (SECTION_ACT_NOW, SECTION_TOP_OPPORTUNITIES, SECTION_TARGET_HITS, SECTION_INVESTMENT):
            block = _thesis_block(index, deal, ending_soon_hours)
        elif key == SECTION_AUCTIONS:
            block = _auction_block(index, deal, ending_soon_hours)
        elif key == SECTION_OFFERS:
            block = _offer_block(index, deal, threshold_pct)
        elif key == SECTION_NEEDS_REVIEW:
            block = _compact_block(
                index, deal, why=_needs_review_why(deal), ending_soon_hours=ending_soon_hours
            )
        elif key == SECTION_PRICE_DROPS:
            block = _price_drop_block(index, deal)
        else:
            block = _compact_block(
                index,
                deal,
                why=_watch_why(deal, threshold_pct, min_savings_dollars),
                ending_soon_hours=ending_soon_hours,
            )
        lines.append(block)
        lines.append("")
    return "\n".join(lines)


def _watch_why(deal: Listing, threshold_pct: float, min_savings_dollars: float = 0.0) -> str:
    """Why THIS card is in WATCH rather than acted on.

    Says the actual reason with the actual numbers. An earlier version
    hedged -- "close to your threshold, or clearing it on percent but not on
    dollars" -- which forced you to work out which of the two applied, and
    read as nonsense next to a 5% discount that was really parked here for
    low confidence.
    """
    if deal.is_opportunity and _confidence(deal) == "low":
        return "below market, but on a low-confidence comp -- worth watching, not acting on"

    reason = deal.rejection_reason
    if reason == reasons.Reason.BELOW_DISCOUNT_THRESHOLD and deal.pct_under_market is not None:
        return "{} under market, short of your {:.0f}% threshold".format(
            _pct1(deal.pct_under_market), threshold_pct
        )
    if reason == reasons.Reason.BELOW_MIN_SAVINGS and deal.dollar_savings is not None:
        return "clears {:.0f}% but saves only {}, under your {} minimum".format(
            threshold_pct, _money(deal.dollar_savings), _money(min_savings_dollars)
        )
    if _confidence(deal) == "low":
        return "the comp behind this is low confidence -- watch it, don't act on it"
    return "close to your thresholds without clearing them"


def _summary_line(sections) -> str:
    """One line of counts, so the top of the email says what kind of day it
    was before you read a single card."""
    labels = [
        (SECTION_ACT_NOW, "act now", "act now"),
        (SECTION_TOP_OPPORTUNITIES, "opportunity", "opportunities"),
        (SECTION_TARGET_HITS, "target hit", "target hits"),
        (SECTION_INVESTMENT, "investment watch", "investment watches"),
        (SECTION_AUCTIONS, "auction", "auctions"),
        (SECTION_OFFERS, "offer candidate", "offer candidates"),
        (SECTION_WATCH, "watch", "watch"),
        (SECTION_NEEDS_REVIEW, "needs review", "needs review"),
        (SECTION_PRICE_DROPS, "price drop", "price drops"),
    ]
    parts = [
        _plural(len(sections[key]), singular, plural)
        for key, singular, plural in labels
        if sections[key]
    ]
    if not parts:
        return "nothing in any section today"
    return " | ".join(parts)


def _subject(sections, date_short: str, stats=None) -> str:
    """Lead with the strongest thing that happened, short enough for a lock
    screen. Never leads with a dollar figure -- a big number off a weak comp
    is exactly the false urgency this pass exists to remove.

    A run that raised a warning and found nothing leads with the warning. It
    used to be indistinguishable from a quiet day: the day eBay changes its
    email template, every count is zero, the subject says "No opportunities
    today", and the alarm that says the zeroes are fabricated is the last
    line of the footer. A quiet day and a broken scan are the two states this
    subject line most needs to keep apart.
    """
    act_now = len(sections[SECTION_ACT_NOW])
    opportunities = len(sections[SECTION_TOP_OPPORTUNITIES])
    targets = len(sections[SECTION_TARGET_HITS])
    auctions = len(sections[SECTION_AUCTIONS])

    if act_now:
        subject = "{} ACT NOW".format(act_now)
        if opportunities:
            subject += " + {} more".format(opportunities)
        return "{} ({})".format(subject, date_short)
    if opportunities:
        return "{} ({})".format(_plural(opportunities, "opportunity", "opportunities"), date_short)
    if targets:
        return "{} ({})".format(_plural(targets, "target card hit"), date_short)
    if auctions:
        return "{} to review ({})".format(_plural(auctions, "auction"), date_short)
    if getattr(stats, "breakage_warnings", None):
        return "CHECK THIS -- today's scan may be broken ({})".format(date_short)
    return "No opportunities today ({})".format(date_short)


def _empty_state_block(threshold_pct: float, min_savings_dollars: float, stats,
                       has_other_content: bool, focused: bool = False) -> str:
    """The empty state is the most important state in this report.

    After the CardPro 2.0 comp fixes, zero-opportunity days are expected
    and correct -- the old engine's 15 lifetime "deals" were 15 artifacts
    of a circular price-tier comparison. So this block has one job: make a
    quiet day read as the system working, with the receipts to prove it
    looked, rather than as silence or breakage.
    """
    warnings = list(getattr(stats, "breakage_warnings", None) or [])
    if warnings:
        # Never assert that a quiet day is normal while something is warning
        # that the quiet is manufactured. This block used to open with "that
        # is a normal outcome, not a failure" unconditionally, six lines
        # above a footer saying 14 alert emails yielded zero listings.
        lines = ["SOMETHING IS WRONG WITH TODAY'S SCAN.", ""]
        for warning in warnings:
            lines.append(textwrap.fill(warning, width=_WRAP_WIDTH,
                                       initial_indent="!! ", subsequent_indent="   "))
        lines.append("")
        lines.append(
            textwrap.fill(
                "Everything below is what CardPro would say about an empty day. Do not read "
                "the zeroes as a quiet market until you have checked the above.",
                width=_WRAP_WIDTH,
            )
        )
    else:
        lines = [
            "NOTHING CLEARED THE BAR TODAY.",
            "",
            textwrap.fill(
                "No listing had both a comp CardPro is willing to stand behind and a discount of "
                "{:.0f}%+ with at least {} saved. That is a normal outcome, not a failure: since "
                "the comp rules were fixed, a card only qualifies when its value is backed by "
                "grade-matched, identity-matched comps. Days with nothing are the honest price of "
                "not being told that a $1.25 base card is 95% under market.".format(
                    threshold_pct, _money(min_savings_dollars)
                ),
                width=_WRAP_WIDTH,
            ),
        ]
    if stats is not None:
        summary = stats.rejections.summary_lines()[:5]
        if summary:
            lines.append("")
            lines.append("Top reasons listings did not qualify:")
            for line in summary:
                lines.append("  * {}".format(line))
        unvalued = getattr(stats, "unvalued", 0)
        matched = getattr(stats, "listings_matched_to_watchlist", 0)
        if matched:
            lines.append("")
            lines.append(
                "Looked at {} matched to your watchlist; {} had no comp at any level.".format(
                    _plural(matched, "listing"), unvalued
                )
            )
    lines.append("")
    # With focus on, this is a sample and must not be described as the
    # whole: "everything CardPro did see" above a capped list would be the
    # report claiming completeness it deliberately gave up.
    if has_other_content and focused:
        lines.append(
            textwrap.fill(
                "The best of what CardPro did see is still below -- auctions, listings it "
                "could not value, and price drops, as far as your focus limits allow. The "
                "footer counts what did not fit and names the setting that would show it.",
                width=_WRAP_WIDTH,
            )
        )
    elif has_other_content:
        lines.append(
            textwrap.fill(
                "Everything CardPro did see is still below -- auctions, listings it could not "
                "value, and price drops are shown so that nothing disappears without being "
                "counted.",
                width=_WRAP_WIDTH,
            )
        )
    elif focused:
        lines.append(
            textwrap.fill(
                "Nothing inside your focus reached the report either -- no auctions, no "
                "unvalued listings, no price drops. Anything outside it is counted in the "
                "footer, and the counts in SYSTEM HEALTH below are the proof it looked.",
                width=_WRAP_WIDTH,
            )
        )
    else:
        lines.append(
            textwrap.fill(
                "Nothing else reached the report either -- no auctions, no unvalued listings, "
                "no price drops. The counts in SYSTEM HEALTH below are the proof it looked.",
                width=_WRAP_WIDTH,
            )
        )
    return "\n".join(lines)


def _economics_in_report(sections) -> list:
    """Every DealEconomics whose figures are actually PRINTED, in render order.

    A card marked ``resale_uneconomic`` shows the collector-buy line instead
    of its numbers, so its assumptions back nothing the reader can see. On a
    day of only cheap finds that used to leave a footer announcing "every
    profit figure above rests on these" above zero profit figures -- an
    explanation of arithmetic the report deliberately did not do.
    """
    found = []
    for key in SECTION_ORDER:
        for deal in sections[key]:
            if deal.economics is not None and not getattr(deal, "resale_uneconomic", False):
                found.append(deal.economics)
    return found


def _shared_haircut_line(all_economics) -> Optional[str]:
    """The resale haircut as a percentage, when every card shares one.

    economics.py states the haircut in a sentence that also carries that
    card's dollar figures, so the sentence itself is per-card and gets
    filtered out of the footer. The *percentage* is a global assumption and
    losing it would leave "sell ~$330" against a "$348" market looking like
    an unexplained discrepancy -- so it is restated here, derived from the
    two numbers economics.py already published.
    """
    percentages = set()
    for econ in all_economics:
        market = getattr(econ, "estimated_market_value", 0.0)
        if not market:
            return None
        percentages.add(round((1.0 - econ.expected_sale_price / market) * 100.0, 2))
    if len(percentages) != 1:
        return None
    haircut = percentages.pop()
    if haircut == 0.0:
        return (
            "No resale haircut: the expected sale price is the full estimated market value, "
            "which assumes you sell as well as the median comp."
        )
    return (
        "A resale haircut of {:.2f}% is applied to every expected sale price -- you are not "
        "the median seller and you want your money back this month.".format(haircut)
    )


def _assumptions_footer(sections) -> str:
    """The assumptions behind every profit figure, stated once.

    Two filters, both there for the same reason -- this footer has to be
    text you can read once, disagree with, and then trust for weeks:

    * only assumptions shared by EVERY card with economics, so the footer
      describes the model rather than one listing; and
    * none of the per-card sentences (see ``PER_CARD_ASSUMPTION_PREFIXES``),
      because four different "Inbound shipping of $X is included" lines
      side by side read as a contradiction rather than as four facts.

    The per-card figures are not lost -- they are on each card's own Cost
    and Economics lines, which is where they belong. Printed nowhere,
    ``profit $40.98`` would be a bare unexplained number, which is exactly
    what economics.py built its ``assumptions`` tuple to prevent.
    """
    all_economics = _economics_in_report(sections)
    if not all_economics:
        return ""

    shared = OrderedDict()
    for assumption in getattr(all_economics[0], "assumptions", ()) or ():
        if assumption.startswith(PER_CARD_ASSUMPTION_PREFIXES):
            continue
        if all(assumption in (getattr(e, "assumptions", ()) or ()) for e in all_economics):
            shared.setdefault(assumption, None)

    body = list(shared)
    haircut_line = _shared_haircut_line(all_economics)
    if haircut_line:
        body.append(haircut_line)
    if not body:
        return ""

    lines = ["ECONOMICS ASSUMPTIONS (every profit figure above rests on these):"]
    for assumption in body[:MAX_ASSUMPTION_LINES]:
        lines.append(
            textwrap.fill(assumption, width=_WRAP_WIDTH, initial_indent="  * ", subsequent_indent="    ")
        )
    remaining = len(body) - MAX_ASSUMPTION_LINES
    if remaining > 0:
        lines.append("  * (+{} more)".format(_plural(remaining, "assumption")))
    lines.append(
        textwrap.fill(
            "Per-card figures -- what inbound shipping was known to be, what each card's "
            "expected sale price works out to -- are on that card's own Cost and Economics "
            "lines.",
            width=_WRAP_WIDTH,
            initial_indent="  ",
            subsequent_indent="  ",
        )
    )
    return "\n".join(lines)


def _health_footer(stats) -> str:
    if stats is None:
        return ""
    lines = ["SYSTEM HEALTH"]
    for line in stats.health_lines():
        # Wrapped, like every other block in this module. Unwrapped, the
        # "Top reasons" line came out at 404 characters and a mail client
        # soft-wrapped it wherever it liked, which is how the one part of
        # the footer worth reading became the one part nobody could.
        lines.append(
            textwrap.fill(line, width=_WRAP_WIDTH, initial_indent="  ", subsequent_indent="    ")
        )
    return "\n".join(lines)


def _craigslist_section(craigslist_links) -> str:
    """Unchanged in spirit from CardPro 1.0: Craigslist is never scraped
    (it actively blocks automation), so the report links you straight into
    a live search and you eyeball it yourself."""
    if not craigslist_links:
        return ""
    lines = ["Craigslist quick check (not auto-scanned -- eyeball these yourself):"]
    for player, url in craigslist_links.items():
        lines.append("  {}: {}".format(player, url))
    return "\n".join(lines)


def _wrap_footer_line(text: str) -> str:
    """Footer lines wrap like every other block. The leading spaces in the
    caller's string are the indent, and continuations get two more."""
    stripped = text.lstrip(" ")
    indent = " " * (len(text) - len(stripped))
    return textwrap.fill(
        stripped, width=_WRAP_WIDTH, initial_indent=indent, subsequent_indent=indent + "  "
    )


def _sold_comp_requests_section(requests, unidentified: int = 0) -> str:
    """Where ten minutes of your time would buy the most valuation.

    Every price in the corpus is an asking price, which is why no comp in
    this report reaches "high" confidence. The only fix available without
    paid data or scraping is you looking a card up on 130point and typing
    the number in. That does not scale, so this picks the handful of lookups
    with the best return: the identities the most listings are currently
    waiting on -- see src/comp_requests.py.

    Prints the unidentified count alongside, because the two numbers say
    different things about what to do next. If the suggestions are thin and
    the unidentified count is large, data entry is not the bottleneck --
    title parsing is, and no amount of typing will fix it.
    """
    if not requests and not unidentified:
        return ""

    lines = ["Sold comps worth adding (130point.com -> python -m scripts.add_sold_comp):"]
    # Same wrapping rule as everywhere else -- a suggestion line with a long
    # set name and a long player name ran past 130 characters.
    for request in requests:
        lines.append(
            _wrap_footer_line(
                "  {} ({} {}) -- {} waiting, {} still needed".format(
                    request.player,
                    request.card_label,
                    request.market_label,
                    _plural(request.listings_waiting, "listing"),
                    _plural(request.still_needed, "sale"),
                )
            )
        )
        lines.append('      search: "{}"'.format(request.search_query))
        if request.example_url:
            lines.append("      example: {}".format(request.example_url))
    if unidentified:
        lines.append(
            _wrap_footer_line(
                "  ({} listing(s) could not be identified precisely enough for any sold "
                "comp to match -- those need better title parsing, not more data.)".format(
                    unidentified
                )
            )
        )
    return "\n".join(lines)


def _search_slice(player: str, query: str) -> str:
    """The distinguishing part of a suggested query -- "Caleb Williams PSA 10"
    against player "Caleb Williams" is the slice "PSA 10". That is what makes
    identical gap sets across players recognisably identical."""
    text = str(query).strip()
    prefix = str(player).strip()
    if prefix and text.lower().startswith(prefix.lower()):
        remainder = text[len(prefix):].strip()
        if remainder:
            return remainder
    return text


def _named_players(players: list) -> str:
    shown = players[:SEARCH_COVERAGE_MAX_NAMED_PLAYERS]
    text = ", ".join(shown)
    hidden = len(players) - len(shown)
    if hidden:
        text += ", and {} more".format(hidden)
    return text


def _search_coverage_section(search_suggestions) -> str:
    """Searches you do not appear to have -- collapsed to the common case.

    Suggestive, never assertive: eBay's alert emails don't say which saved
    search produced a listing, so this can only report an absence of
    evidence (see search_terms.py).

    Collapsed because of what it actually looks like in production: 20
    watchlist players x 7 slices printed ~140 identical-in-substance lines,
    longer than the rest of the report combined. A section that buries the
    cards defeats the point of a decision-first report, so identical gap
    sets are stated once for the group, only genuinely different ones get
    their own lines, and the whole section is capped at
    ``SEARCH_COVERAGE_MAX_LINES`` regardless of watchlist size.
    """
    if not search_suggestions:
        return ""

    groups = OrderedDict()  # tuple of slices -> [player, ...]
    for player, suggestions in search_suggestions.items():
        if not suggestions:
            continue
        key = tuple(_search_slice(player, s.query) for s in suggestions)
        groups.setdefault(key, []).append(player)
    if not groups:
        return ""

    total_players = sum(len(players) for players in groups.values())
    lines = [
        "\U0001f50d SEARCH COVERAGE -- no evidence of these saved searches for {}".format(
            _plural(total_players, "player")
        ),
        textwrap.fill(
            "One query per player is why 99% of what CardPro sees is cheap raw filler. Each "
            "slice below pulls part of the market the broad query structurally misses; adding "
            "one is a copy/paste on eBay's own site.",
            width=_WRAP_WIDTH,
        ),
    ]

    # Biggest group first: the collapsed line covering 20 players is worth
    # more than one player's detail, and the cap may cut the tail off.
    ordered = sorted(
        enumerate(groups.items()), key=lambda pair: (-len(pair[1][1]), pair[0])
    )

    def rendered_line_count(chunks):
        # Chunks are already wrapped, so a chunk can be several lines. The
        # cap is on what the reader sees, not on how many strings we hold.
        return sum(chunk.count("\n") + 1 for chunk in chunks)

    unlisted = 0
    for _, (slices, players) in ordered:
        if len(players) == 1:
            player = players[0]
            entry = ['  {}: {}'.format(player, ", ".join('"{}"'.format(part) for part in slices))]
            rationales = search_suggestions[player]
            if len(rationales) == 1:
                entry = ["  {}: {}".format(player, rationales[0].as_line())]
        else:
            entry = [
                textwrap.fill(
                    "For each of these {} players, add: {}".format(
                        len(players), ", ".join('"{}"'.format(part) for part in slices)
                    ),
                    width=_WRAP_WIDTH,
                    initial_indent="  ",
                    subsequent_indent="    ",
                ),
                textwrap.fill(
                    "Players: {}.".format(_named_players(players)),
                    width=_WRAP_WIDTH,
                    initial_indent="  ",
                    subsequent_indent="    ",
                ),
            ]
        if rendered_line_count(lines) + rendered_line_count(entry) > SEARCH_COVERAGE_MAX_LINES:
            unlisted += len(players)
            continue
        lines.extend(entry)

    if unlisted:
        lines.append("  (+{} with different gaps, not listed)".format(_plural(unlisted, "player")))
    return "\n".join(lines)


def _focus_line(rules) -> str:
    """The one line under the counts that says why the email is short.

    Without it a focused report is indistinguishable from a quiet market,
    and "CardPro found nothing" and "CardPro found plenty and showed you
    the cheap end of it" are very different mornings.
    """
    if not rules.enabled:
        return ""
    parts = ["Focus: cards at or under {}".format(_money(rules.price_ceiling))]
    if rules.require_auction_bidding_room:
        parts.append("auctions still under your max bid")
    if rules.max_listings > 0:
        parts.append("top {} listings".format(rules.max_listings))
    line = ", ".join(parts)
    line += ". Anything dearer needed {:.0f}%+ off and {}+ saved to get in.".format(
        rules.exceptional_min_discount_pct, _money(rules.exceptional_min_savings_dollars)
    )
    return textwrap.fill(line, width=_WRAP_WIDTH)


def _focus_footer(selection, trimmed: int, rules) -> str:
    """What focus removed, in plain numbers, with the setting that would
    bring it back. A cap you cannot see the effect of is a cap you stop
    trusting -- and the fix for "I think it hid something good" has to be a
    number you can change, not a rebuild.
    """
    if not rules.enabled:
        return ""
    parts = []
    above = selection.omitted.get(focus.ABOVE_CEILING, 0)
    if above:
        parts.append(
            " {} above your {} focus ceiling left out -- none was {:.0f}%+ off with {}+ saved "
            "off a comp CardPro will stand behind. Raise focus.price_ceiling to see them.".format(
                _plural(above, "listing", "listings"),
                _money(rules.price_ceiling),
                rules.exceptional_min_discount_pct,
                _money(rules.exceptional_min_savings_dollars),
            )
        )
    no_room = selection.omitted.get(focus.NO_BIDDING_ROOM, 0)
    if no_room:
        parts.append(
            " {} left out: the current bid is already above the most you could pay and keep "
            "your margin, so there is no bid left to make.".format(
                _plural(no_room, "auction was", "auctions were")
            )
        )
    unknown = selection.omitted.get(focus.PRICE_UNKNOWN, 0)
    if unknown:
        parts.append(
            " {} left out with no readable price -- nothing could be said about cost or "
            "value.".format(_plural(unknown, "listing was", "listings were"))
        )
    if trimmed:
        # Deliberately does not say "trimmed to N listings": the email holds
        # at most max_listings, but a day whose only content is
        # not-recommendations stops at their per-section share and spends
        # nothing like the full budget. Quoting the cap as if it were the
        # count would be a number the reader can see is wrong.
        sentence = " {} matched your focus but {} trimmed for length -- at most {} print".format(
            _plural(trimmed, "listing", "listings"),
            "was" if trimmed == 1 else "were",
            _plural(rules.max_listings, "listing"),
        )
        if rules.max_per_section > 0:
            sentence += ", and at most {} from any one section".format(
                _plural(rules.max_per_section, "listing")
            )
        sentence += (
            ". The cut comes off the bottom of a section, so nothing above it moved; raise "
            "focus.max_listings or focus.max_per_section to see more."
        )
        parts.append(sentence)
    return "".join(parts)


def _join_blocks(blocks) -> str:
    return "\n\n".join(block for block in blocks if block)


# ---------------------------------------------------------------------------
# the entry point
# ---------------------------------------------------------------------------


def build_report(
    deals: list,
    threshold_pct: float,
    run_date: date,
    craigslist_links: Optional[dict] = None,
    ebay_enabled: bool = True,
    min_savings_dollars: float = 0,
    *,
    stats=None,
    search_suggestions=None,
    immediate_min_savings: float = DEFAULT_IMMEDIATE_MIN_SAVINGS,
    immediate_min_discount_pct: float = DEFAULT_IMMEDIATE_MIN_DISCOUNT_PCT,
    ending_soon_hours: float = DEFAULT_ENDING_SOON_HOURS,
    focus_rules: Optional[focus.FocusRules] = None,
    comp_requests_list=(),
    unidentified_listings: int = 0,
) -> tuple:
    """Returns ``(subject, body)`` -- a plain-text email, no HTML.

    Every argument after ``min_savings_dollars`` is keyword-only with a
    default, so an older caller that passes the original six positionals
    still gets a valid report (it just loses the health footer and the
    coverage section, which are additions, not requirements).

    ``stats`` is an ``observability.RunStats``; ``search_suggestions`` is
    ``{player: [search_terms.SuggestedSearch, ...]}``.

    ``comp_requests_list`` (``comp_requests.CompRequest``) and
    ``unidentified_listings`` drive the "sold comps worth adding" footer --
    where a few minutes on 130point would buy the most valuation. Both
    default to nothing, so a caller that doesn't compute them just gets no
    such section.

    ``focus_rules`` (a ``focus.FocusRules``) decides what is email material
    and how long the email may be -- the price ceiling, the exception for a
    genuinely exceptional expensive card, and the cap on how many listings
    print. It defaults to ``focus.OFF``, which shows everything, so the
    length cap is something a caller opts into rather than something that
    silently starts hiding cards.
    """
    focus_rules = focus_rules or focus.OFF
    date_long = "{} {}, {}".format(run_date.strftime("%B"), run_date.day, run_date.year)
    date_full = "{}, {}".format(run_date.strftime("%A"), date_long)
    date_short = "{} {}".format(run_date.strftime("%b"), run_date.day)

    footer = _join_blocks(
        [
            _health_footer(stats),
            _sold_comp_requests_section(comp_requests_list, unidentified_listings),
            _craigslist_section(craigslist_links),
            _search_coverage_section(search_suggestions),
        ]
    )
    footer = ("\n\n---\n" + footer) if footer else ""

    if not ebay_enabled:
        subject = "eBay not configured ({})".format(date_long)
        body = textwrap.fill(
            "Card deal scan for {}: eBay wasn't scanned today because neither "
            "EBAY_CLIENT_ID/EBAY_CLIENT_SECRET nor ebay_alerts.enabled are set up in "
            ".env / config/settings.json. This is expected, not an error -- once either "
            "eBay path is configured, deals will resume showing up here "
            "automatically.".format(date_long),
            width=_WRAP_WIDTH,
        )
        return subject, "CARDPRO DAILY -- {}\n\n".format(date_full) + body + footer

    selection = focus.select(deals, focus_rules)
    sections = classify_sections(
        selection.kept,
        threshold_pct=threshold_pct,
        immediate_min_savings=immediate_min_savings,
        immediate_min_discount_pct=immediate_min_discount_pct,
        ending_soon_hours=ending_soon_hours,
    )
    sections, trimmed = focus.trim(
        sections, BUDGET_ORDER, focus_rules, no_leftovers=BUDGET_NO_LEFTOVERS
    )

    header = "CARDPRO DAILY -- {}\n{}".format(date_full, _summary_line(sections))
    focus_line = _focus_line(focus_rules)
    if focus_line:
        header += "\n" + focus_line

    decision_sections = (
        SECTION_ACT_NOW,
        SECTION_TOP_OPPORTUNITIES,
        SECTION_TARGET_HITS,
        SECTION_INVESTMENT,
    )
    has_decision_content = any(sections[key] for key in decision_sections)

    blocks = [header]
    if not has_decision_content:
        has_other_content = any(sections[key] for key in SECTION_ORDER)
        blocks.append(
            _empty_state_block(
                threshold_pct, min_savings_dollars, stats, has_other_content,
                focused=focus_rules.enabled,
            )
        )
    # Which section printed each card's full block, so a later section can
    # point at it rather than repeat it. Built as we go, in render order.
    shown_above: dict = {}
    for key in SECTION_ORDER:
        if sections[key]:
            blocks.append(
                _render_section(
                    key, sections[key], threshold_pct, ending_soon_hours, min_savings_dollars,
                    shown_above=shown_above,
                )
            )
            for deal in sections[key]:
                shown_above.setdefault(deal.id, _plain_title(SECTION_TITLES[key]))

    shown = {deal.id for key in SECTION_ORDER for deal in sections[key]}
    # Everything CardPro saw is accounted for in exactly one of these
    # buckets: printed, left out by focus, trimmed for length, or reviewed
    # and simply not close. A shorter email is only worth having if you can
    # tell which of those happened.
    not_shown = (
        len({deal.id for deal in deals}) - len(shown) - selection.omitted_total - trimmed
    )
    thresholds = (
        "Thresholds: an opportunity needs {:.0f}%+ under market AND at least {} saved; "
        "ACT NOW additionally needs {} saved and {:.0f}%+ off.".format(
            threshold_pct,
            _money(min_savings_dollars),
            _money(immediate_min_savings),
            immediate_min_discount_pct,
        )
    )
    # Cheap cards clear a HIGHER percentage bar, so a footer quoting only the
    # ordinary threshold understates what a cheap find had to do to get here.
    # Only said when a cheap card is actually on the page -- an unconditional
    # sentence about rules nothing hit is noise.
    if any(getattr(deal, "is_cheap", False) for key in SECTION_ORDER for deal in sections[key]):
        thresholds += (
            " Cheap cards clear a higher bar, not a lower one: they need a bigger percentage "
            "discount AND something that makes a copy scarce -- a rookie, auto, patch, serial "
            "number, parallel or grade."
        )
    if not_shown > 0:
        thresholds += (
            " {} reviewed and fitted no section -- they had a usable comp and were simply not "
            "close.".format(_plural(not_shown, "other listing was", "other listings were"))
        )
    thresholds += _focus_footer(selection, trimmed, focus_rules)
    blocks.append(textwrap.fill(thresholds, width=_WRAP_WIDTH))
    blocks.append(_assumptions_footer(sections))

    body = _join_blocks(blocks) + footer
    return _subject(sections, date_short, stats), body
