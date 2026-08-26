"""Explicit acquisition targets: specific cards you have told CardPro to
find below a price you set yourself.

This is deliberately separate from the player watchlist and from
fair-market valuation, because they answer different questions:

  player watchlist  -> "show me anything interesting about this player"
  comp engine       -> "what is this card actually worth"
  acquisition target -> "I want THIS card at or below THIS price"

A target hit is not a claim that a card is underpriced. A card can be a
target hit and a bad deal at the same time (you're paying up for something
you specifically want), or a great deal and not a target at all. The report
keeps them in separate sections for exactly that reason -- collapsing them
would hide which one is actually true.

Targets live in config/watchlist.json under "target_cards" and need no code
changes to add, remove, or reprice. Every field except `player` and the
price thresholds is optional; a target only matches on the fields it
actually specifies, so you can write a broad target ("any Caleb Williams
Prizm Silver PSA 10") or a precise one (down to the card number).

Matching is strict-but-optional: a field you specify MUST match, and a
field the listing hasn't confidently identified does NOT match. Unknown
never satisfies a target -- the same "missing means unknown, never a guess"
rule the rest of the project follows. Being told "your target card showed
up" and finding it's a different parallel is exactly the failure this
avoids.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Ordered best -> worst. A listing is assigned the strongest band whose
# threshold its total cost clears.
BAND_IMMEDIATE = "immediate_alert"
BAND_GREAT = "great_buy"
BAND_BUY_ZONE = "buy_zone"
BAND_ORDER = (BAND_IMMEDIATE, BAND_GREAT, BAND_BUY_ZONE)

BAND_LABELS = {
    BAND_IMMEDIATE: "IMMEDIATE",
    BAND_GREAT: "GREAT BUY",
    BAND_BUY_ZONE: "BUY ZONE",
}


@dataclass(frozen=True)
class TargetCard:
    """One acquisition target. `label` is what shows up in the report, so
    write it the way you'd describe the card out loud."""

    label: str
    player: str
    year: Optional[int] = None
    set_name: Optional[str] = None
    parallel: Optional[str] = None
    card_number: Optional[str] = None
    grader: Optional[str] = None
    grade: Optional[str] = None
    card_type: Optional[str] = None  # "raw" | "graded"
    buy_zone: Optional[float] = None
    great_buy: Optional[float] = None
    immediate_alert: Optional[float] = None

    def thresholds(self) -> dict:
        return {
            BAND_IMMEDIATE: self.immediate_alert,
            BAND_GREAT: self.great_buy,
            BAND_BUY_ZONE: self.buy_zone,
        }


@dataclass(frozen=True)
class TargetHit:
    target: TargetCard
    band: Optional[str]  # None when it matched the card but is above every price band
    threshold: Optional[float]
    # False when the listing had no readable price. Without this, "we do not
    # know what it costs" and "it costs more than every band you set" were
    # the same value, and the report printed the second sentence for the
    # first situation -- a price claim manufactured out of an unknown.
    price_known: bool = True

    @property
    def in_buy_zone(self) -> bool:
        return self.band is not None

    @property
    def label(self) -> str:
        if not self.price_known:
            return "PRICE UNKNOWN"
        return BAND_LABELS.get(self.band, "ABOVE BUY ZONE")


def load_targets(raw_targets: list) -> list:
    """Builds TargetCards from the raw JSON list in config/watchlist.json.

    Entries missing a `player` are skipped rather than raising: a typo in a
    personal config file shouldn't take down the daily scan, and the entry
    is visible in the config for you to notice and fix.
    """
    targets = []
    for entry in raw_targets or []:
        player = entry.get("player")
        if not player:
            continue
        targets.append(
            TargetCard(
                label=entry.get("label") or player,
                player=player,
                year=entry.get("year"),
                set_name=entry.get("set_name"),
                parallel=entry.get("parallel"),
                card_number=entry.get("card_number"),
                grader=entry.get("grader"),
                grade=entry.get("grade"),
                card_type=entry.get("card_type"),
                buy_zone=_as_float(entry.get("buy_zone")),
                great_buy=_as_float(entry.get("great_buy")),
                immediate_alert=_as_float(entry.get("immediate_alert")),
            )
        )
    return targets


def _as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _matches_field(specified, actual) -> bool:
    """A target field the listing doesn't know about is NOT a match. Unknown
    never satisfies a target -- see the module docstring."""
    if specified is None:
        return True  # target didn't ask about this field
    if actual is None:
        return False  # target asked, listing doesn't know -> not a confirmed match
    return str(specified).strip().lower() == str(actual).strip().lower()


def match_target(
    target: TargetCard,
    *,
    player: str,
    total_cost: Optional[float],
    year=None,
    set_name=None,
    parallel=None,
    card_number=None,
    grader=None,
    grade=None,
    card_type=None,
) -> Optional[TargetHit]:
    """Returns a TargetHit if this listing is the card the target describes,
    else None. A hit is returned even when the price is above every band
    (band=None) so the report can show "your target card is listed, but
    above your buy zone" -- that is useful information, not noise.

    A listing with no readable price is a hit too, with ``price_known=False``.
    That is a different answer from "above every band" and has to stay
    different: they were the same value, and the report printed the
    above-every-band sentence for a card whose price it did not know.
    """
    if player.strip().lower() != target.player.strip().lower():
        return None
    fields = (
        (target.year, year),
        (target.set_name, set_name),
        (target.parallel, parallel),
        (target.card_number, card_number),
        (target.grader, grader),
        (target.grade, grade),
        (target.card_type, card_type),
    )
    if not all(_matches_field(specified, actual) for specified, actual in fields):
        return None

    if total_cost is None:
        return TargetHit(target=target, band=None, threshold=None, price_known=False)

    for band in BAND_ORDER:
        threshold = target.thresholds()[band]
        if threshold is not None and total_cost <= threshold:
            return TargetHit(target=target, band=band, threshold=threshold)
    return TargetHit(target=target, band=None, threshold=None)


def best_hit(targets: list, **listing_fields) -> Optional[TargetHit]:
    """The strongest hit across all configured targets, or None. "Strongest"
    means the best price band; ties keep the first target in config order so
    the result is stable and explainable.
    """
    hits = [hit for hit in (match_target(target, **listing_fields) for target in targets) if hit]
    if not hits:
        return None

    def rank(hit):
        return BAND_ORDER.index(hit.band) if hit.band in BAND_ORDER else len(BAND_ORDER)

    return sorted(hits, key=rank)[0]
