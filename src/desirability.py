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


def describe(attributes) -> str:
    """Human-readable list of attributes, for the "why this is interesting"
    line in the report. Empty string when there are none, so callers can omit
    the line entirely rather than printing "attributes: none"."""
    if not attributes:
        return ""
    return ", ".join(ATTRIBUTE_LABELS.get(name, name) for name in attributes)
