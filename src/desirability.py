"""What makes a card worth owning, separately from what it's worth.

This is the "collectible opportunity" half of the product. Market value
(src/comps.py) answers *what is this worth*. Desirability answers *is this
the kind of card I want at all*. They are kept apart on purpose: a card can
be genuinely underpriced and still be something you'd never want, and a card
you badly want can be a terrible price. Blending them into one number would
hide which of the two is actually true -- the project forbids that.

Where this earns its keep is cheap cards. Once the deal gate lets sub-$10
listings through, "underpriced" stops being a sufficient filter, because the
cheap end of the hobby is mostly commodity: base veterans, junk-wax commons,
team cards. There are thousands of them, they are cheap because they are
common, and a 60%-off base common is still a base common. What separates a
$4 card worth surfacing from a $4 card that is noise is not its price or
even its discount -- it is whether the card has any attribute that makes a
specific copy scarce or wanted.

Deliberately NOT in here: player conviction, favourite teams, or anything
else about your personal taste. Those belong in config (player_tiers,
target_cards) because they change with your opinion. What's here is about
the card itself and is the same for everybody.
"""
from __future__ import annotations

from typing import Optional

# Canonical attribute names. Order is the order they're shown in.
ROOKIE = "rookie"
AUTOGRAPH = "autograph"
PATCH = "patch"
MEMORABILIA = "memorabilia"
SERIAL_NUMBERED = "serial_numbered"
SHORT_PRINT = "short_print"
PARALLEL = "parallel"
GRADED = "graded"

ATTRIBUTE_LABELS = {
    ROOKIE: "rookie card",
    AUTOGRAPH: "autograph",
    PATCH: "patch",
    MEMORABILIA: "memorabilia/relic",
    SERIAL_NUMBERED: "serial numbered",
    SHORT_PRINT: "short print",
    PARALLEL: "non-base parallel",
    GRADED: "professionally graded",
}

#: Short tags for the report. Kept separate from the labels so the email can
#: stay scannable without the prose getting cryptic.
ATTRIBUTE_TAGS = {
    ROOKIE: "ROOKIE",
    AUTOGRAPH: "AUTO",
    PATCH: "PATCH",
    MEMORABILIA: "MEM",
    SERIAL_NUMBERED: "#'d",
    SHORT_PRINT: "SP",
    PARALLEL: "PARALLEL",
    GRADED: "GRADED",
}

#: A print run at or below this is scarce enough to matter on its own.
#: Above it, being numbered is still an attribute, just a weaker one -- the
#: distinction isn't used to gate anything today, only to explain.
SCARCE_PRINT_RUN = 500


def attributes_of(listing) -> tuple:
    """Every desirability attribute this listing demonstrably has.

    Reads only what identity extraction was CONFIDENT about. An attribute
    that couldn't be established is absent, never assumed -- same rule as
    everywhere else. That means this under-reports rather than over-reports,
    which is the safe direction: the cost of missing an attribute is that a
    good cheap card doesn't surface, and the cost of inventing one is that a
    commodity does.
    """
    found = []
    identity = getattr(listing, "card_identity", None)

    if getattr(listing, "is_rookie_card", False):
        found.append(ROOKIE)

    if identity is not None:
        if identity.is_autograph.value:
            found.append(AUTOGRAPH)
        if getattr(identity, "is_patch", None) is not None and identity.is_patch.value:
            found.append(PATCH)
        elif identity.is_memorabilia.value:
            found.append(MEMORABILIA)
        if identity.serial_number.value is not None or _print_run(identity) is not None:
            found.append(SERIAL_NUMBERED)
        if identity.parallel.value is not None:
            found.append(PARALLEL)

    if getattr(listing, "card_type", None) == "graded":
        found.append(GRADED)

    return tuple(found)


def _print_run(identity) -> Optional[int]:
    field = getattr(identity, "print_run", None)
    return field.value if field is not None else None


def is_commodity(listing, price_ceiling: float) -> bool:
    """True when this is a cheap card with nothing that makes a copy special.

    Only applies below `price_ceiling`. Above it the market has already voted
    that the card is not commodity -- a $200 base card is expensive for some
    reason, and it isn't CardPro's place to overrule that with a keyword list.

    A listing with no price is not treated as commodity: unknown is not a
    verdict, and it will be rejected upstream for having no price anyway.
    """
    price = getattr(listing, "price", None)
    if price is None or price >= price_ceiling:
        return False
    return not attributes_of(listing)


#: How much each attribute counts toward "is this card interesting".
#:
#: THIS IS NOT A VALUATION AND MUST NEVER BE USED AS ONE. It answers a
#: different question -- "would I want to look at this card" -- and it exists
#: because that question is one CardPro can actually answer from a title,
#: on a day when it cannot answer "is this underpriced" for anything at all.
#: The weights are ordinary hobby knowledge: a signature or a patch is on
#: the card itself and is why the card exists; a serial number caps how many
#: copies there are; a parallel or a rookie designation is real but common;
#: a grade says a third party looked at it, which matters least of the six
#: because it says nothing about what the card IS.
#:
#: They are only ever compared against each other to sort a list. No number
#: derived from them is ever printed as a price, a discount, or a score.
INTEREST_WEIGHTS = {
    AUTOGRAPH: 5,
    PATCH: 4,
    SERIAL_NUMBERED: 3,
    MEMORABILIA: 2,
    ROOKIE: 2,
    SHORT_PRINT: 2,
    PARALLEL: 1,
    GRADED: 1,
}

#: An attribute at or above this weight makes a card worth showing on its
#: own. Below it, a card needs more than one -- a bare parallel or a bare
#: grade is not, by itself, an interesting card.
STANDOUT_WEIGHT = 3


def interest_score(listing) -> int:
    """How interesting this card is, for ordering a list. Never a price.

    A serial-numbered rookie autograph outranks a graded base parallel, and
    that is the whole claim being made. Scarcity compounds, so a low print
    run adds on top: at or below SCARCE_PRINT_RUN it is worth another point,
    and a one-of-one is worth several, because "there is exactly one" is the
    strongest thing a title can say about a card.
    """
    attributes = attributes_of(listing)
    score = sum(INTEREST_WEIGHTS.get(name, 0) for name in attributes)
    identity = getattr(listing, "card_identity", None)
    print_run = _print_run(identity) if identity is not None else None
    if print_run is not None:
        if print_run == 1:
            score += 5
        elif print_run <= 25:
            score += 3
        elif print_run <= SCARCE_PRINT_RUN:
            score += 1
    return score


def is_standout(listing) -> bool:
    """Whether this card is interesting enough to show for its own sake.

    One strong attribute is enough -- an autograph is an autograph whatever
    else is true of the listing. Otherwise it takes at least two, because a
    single common attribute describes most of the hobby and a section that
    prints most of the hobby is the pile the reader is already drowning in.
    """
    attributes = attributes_of(listing)
    if any(INTEREST_WEIGHTS.get(name, 0) >= STANDOUT_WEIGHT for name in attributes):
        return True
    return len(attributes) >= 2


def describe(attributes) -> str:
    """Human-readable list of attributes, for the "why this is interesting"
    line in the report. Empty string when there are none, so callers can omit
    the line entirely rather than printing "attributes: none"."""
    if not attributes:
        return ""
    return ", ".join(ATTRIBUTE_LABELS.get(name, name) for name in attributes)
