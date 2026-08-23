"""Hand-entered SOLD prices -- the only real market data this project has.

Everything else in CardPro is an asking price. No free, automatable,
sanctioned sold-price feed exists: eBay's Marketplace Insights API is
Limited Release and closed to new users, its User Agreement forbids
scraping, Terapeak is free but has no export, and the paid catalogues are
out of scope.

What remains is you looking a card up once and typing what it sold for.
That sounds weak until you notice the shape of the problem: the list of
cards you genuinely care about is short. Twenty entries covering your real
targets buys trustworthy numbers exactly where money gets spent, which no
amount of asking-price statistics can.

HOW THIS INTEGRATES. These sales get no valuation path of their own. They
load as comps.CompEngine observations marked basis="sold", and the engine
does the rest -- it already segments by market (grader + grade +
qualifier), excludes a listing from its own comp, trims outliers, weights
by recency, and reports a bucket's basis as "sold" only when EVERY kept
point is sold. Adding a parallel valuation path here would duplicate all of
that and drift from it.

That single field also lifts a real ceiling. comps.py downgrades confidence
for asking-price data, noting that "medium" is this project's honest
ceiling until real sold data exists. These observations are that data.

Free places to look a card up, none of which we automate:
  eBay -> filter Sold Items           (90 days)
  eBay Seller Hub -> Product Research (3 years, free with a seller account)
  PSA Auction Prices Realized         (free, PSA-graded, incl. auction houses)
  130point.com                        (free, shows accepted Best Offer prices)

Enter them with scripts/import_sold_comps (paste a page) or
scripts/add_sold_comp (one sale, by flags).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.comps import BASIS_SOLD

logger = logging.getLogger(__name__)


def confidence_for(sample_size: int) -> str:
    """Kept for the entry scripts, which report what a sale count buys before
    you type the next one. The real confidence for a comp is decided by
    comps.CompEngine, which also weighs recency, dispersion and outliers."""
    if sample_size >= 5:
        return "high"
    if sample_size >= 3:
        return "medium"
    return "low"


def identity_key(
    player: str,
    year=None,
    set_name=None,
    parallel=None,
    card_number=None,
    grader=None,
    grade=None,
) -> tuple:
    """The card's full identity, grade included. Normalised so "psa"/"PSA"
    and 301/"301" key the same entry, since these are typed by hand and will
    not be consistent. Used by the entry scripts to merge a new sale into the
    right existing card rather than appending a near-duplicate."""
    return (
        player.strip().lower() if player else None,
        int(year) if year is not None else None,
        set_name.strip().lower() if set_name else None,
        parallel.strip().lower() if parallel else None,
        str(card_number).strip().lower() if card_number is not None else None,
        grader.strip().upper() if grader else None,
        str(grade).strip() if grade is not None else None,
    )


def load_observations(path: Path) -> list[dict]:
    """Hand-entered sales as CompEngine observations, each marked basis="sold".

    This is the whole integration. CompEngine already segments by market
    (grader + grade + qualifier), excludes a listing from its own comp,
    trims outliers, weights by recency, and reports `basis` as "sold" only
    when EVERY kept point is sold. So a sold comp does not need its own
    valuation path -- it needs to arrive as an observation that says so, and
    the existing machinery does the rest.

    It also lifts a real ceiling: comps.py downgrades confidence for
    asking-price data, noting that "medium" is this project's honest ceiling
    until real sold data exists. These observations are that data.

    A malformed file raises rather than returning nothing -- these numbers
    decide what gets called a deal, so a typo must not quietly degrade every
    valuation back to asking-price-only with no warning.
    """
    if not path.exists():
        return []
    with open(path) as f:
        raw = json.load(f)

    observations: list[dict] = []
    for entry in raw.get("comps", []):
        grader = entry.get("grader")
        grade = entry.get("grade")
        for index, sale in enumerate(entry.get("sales", [])):
            if "price" not in sale or "date" not in sale:
                logger.warning("Skipping a sold comp with no price/date for %s", entry.get("player"))
                continue
            key = identity_key(
                entry.get("player"), entry.get("year"), entry.get("set_name"),
                entry.get("parallel"), entry.get("card_number"), grader, grade,
            )
            observations.append({
                # A synthetic id that cannot collide with a listing id, so
                # self-exclusion never silently drops a hand-entered sale.
                "id": f"soldcomp:{hash(key) & 0xffffffff:08x}:{index}",
                "player": entry.get("player"),
                "card_type": "graded" if grader else "raw",
                "grader": grader,
                "grade": grade,
                "year": entry.get("year"),
                "set_name": entry.get("set_name"),
                "parallel": entry.get("parallel"),
                "card_number": entry.get("card_number"),
                "price": float(sale["price"]),
                "date": sale["date"],
                "basis": BASIS_SOLD,
            })
    return observations
