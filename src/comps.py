"""Market-value ("comp") calculation. Deliberately dumb: group prices by
(player, raw-vs-graded, price tier), take the median, done. No weighting,
no keyword-based rarity guessing -- if a number looks wrong you can go
look at the exact price list that produced it.

The price-tier split exists because "raw" alone is too coarse: a $1 base
common and a $90 numbered parallel of the same player are both "raw", and
averaging them together makes the $1 card look like a 97%-off steal
against a median it was never really competing with. Splitting by price
tier compares a listing only against others in roughly the same price
class -- accepted tradeoff: a genuinely rare card mistakenly listed cheap
is now compared against cheap-tier comps too, so it's less likely to get
caught as an outlier deal. That's intentional: it trades a small chance of
missing a rare fluke for a large reduction in false-positive noise from
ordinary commons, which is what was actually happening in practice.

Comps are computed once per run from whatever sold data ebay_client could
get (real sold comps via Marketplace Insights when available, otherwise a
fallback list built from active-listing prices -- see
ebay_client.search_sold_items's docstring for why that fallback exists).

HIERARCHICAL COMPS (build_hierarchical_comp_table / lookup_hierarchical_comp)
--------------------------------------------------------------------------
The price-tier bucket above is still the *only* thing that guarantees a
comp exists (it needs nothing but a price), but it's also the crudest --
two completely different cards of the same player can land in the same
tier. Once card_identity.py has extracted year/set/parallel/card_number for
a listing, a much better comp is available: one built from observations of
the *same card*, not just "similar price". This section adds four
progressively broader comp levels, tried in order, first one with enough
samples wins:

  exact:      player + year + set + parallel + card_number + grader + grade
  near_exact: player + year + set + parallel + card_type (raw/graded)
  family:     player + year + set
  price_tier: player + card_type + price_tier   (today's only level, last resort)

A level is only attempted when the listing has every identity field that
level needs -- e.g. a listing with no detected card_number never gets an
"exact" lookup, it just falls through to near_exact/family/price_tier. This
mirrors card_identity.py's own rule: missing means "skip this level",
never a guessed match.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

# (label, min_inclusive, max_exclusive). Last tier's max is unbounded.
PRICE_TIERS = [
    ("under_5", 0, 5),
    ("5_to_25", 5, 25),
    ("25_to_100", 25, 100),
    ("100_plus", 100, float("inf")),
]


def price_tier(price: float) -> str:
    for label, lo, hi in PRICE_TIERS:
        if lo <= price < hi:
            return label
    return PRICE_TIERS[-1][0]


@dataclass
class CompStats:
    median: float
    sample_size: int
    is_fallback: bool  # True if built from active listings, not real sold comps


def compute_median(prices: list[float], is_fallback: bool = False) -> CompStats | None:
    """None if there isn't enough data to trust a median from."""
    if not prices:
        return None
    return CompStats(median=statistics.median(prices), sample_size=len(prices), is_fallback=is_fallback)


def build_comp_table(
    sold_by_bucket: dict[tuple, list[float]],
    min_comps_required: int,
    fallback_by_bucket: dict[tuple, list[float]] | None = None,
) -> dict[tuple, CompStats]:
    """sold_by_bucket / fallback_by_bucket keys are (player, card_type,
    price_tier) -- see price_tier() above for why the tier is included.

    Real sold comps are used whenever there are at least min_comps_required
    of them. Otherwise, if a fallback price list is available for that same
    bucket, that's used instead (flagged as is_fallback=True). Buckets with
    neither get no entry -- callers must skip flagging listings in that
    bucket rather than guessing.
    """
    fallback_by_bucket = fallback_by_bucket or {}
    table: dict[tuple[str, str], CompStats] = {}

    for bucket, prices in sold_by_bucket.items():
        if len(prices) >= min_comps_required:
            table[bucket] = CompStats(median=statistics.median(prices), sample_size=len(prices), is_fallback=False)

    for bucket, prices in fallback_by_bucket.items():
        if bucket in table:
            continue
        if len(prices) >= min_comps_required:
            table[bucket] = CompStats(median=statistics.median(prices), sample_size=len(prices), is_fallback=True)

    return table


COMP_LEVELS = ("exact", "near_exact", "family", "price_tier")
CONFIDENCE_BY_LEVEL = {"exact": "high", "near_exact": "medium", "family": "low", "price_tier": "low"}


def _exact_key(player, year, set_name, parallel, card_number, grader, grade):
    if None in (year, set_name, card_number, grader, grade):
        return None
    return (player, year, set_name, parallel, card_number, grader, grade)


def _near_exact_key(player, year, set_name, parallel, card_type):
    if None in (year, set_name, card_type):
        return None
    return (player, year, set_name, parallel, card_type)


def _family_key(player, year, set_name):
    if None in (year, set_name):
        return None
    return (player, year, set_name)


def _price_tier_key(player, card_type, price):
    return (player, card_type, price_tier(price))


def build_hierarchical_comp_table(
    observations: list[dict], min_comps_required: int
) -> dict[str, dict[tuple, CompStats]]:
    """Builds all four comp levels from a flat list of observation dicts
    (each needs at least "player", "card_type", "price"; "year", "set_name",
    "parallel", "card_number", "grader", "grade" are used when present --
    see price_history.deduped_observations, which produces exactly this
    shape). Every comp built this way is asking-price-based, so is_fallback
    is always True here -- there's no "real sold data" version of this
    table (that would require eBay API access this project doesn't have).

    A level's bucket only gets an entry once it clears min_comps_required,
    same rule as build_comp_table -- callers must treat a missing level/key
    as "not enough data at that level", not "market value unknown".
    """
    buckets_by_level: dict[str, dict[tuple, list[float]]] = {level: {} for level in COMP_LEVELS}
    for obs in observations:
        price = obs["price"]
        player = obs["player"]
        card_type = obs["card_type"]

        key = _exact_key(player, obs.get("year"), obs.get("set_name"), obs.get("parallel"), obs.get("card_number"), obs.get("grader"), obs.get("grade"))
        if key:
            buckets_by_level["exact"].setdefault(key, []).append(price)

        key = _near_exact_key(player, obs.get("year"), obs.get("set_name"), obs.get("parallel"), card_type)
        if key:
            buckets_by_level["near_exact"].setdefault(key, []).append(price)

        key = _family_key(player, obs.get("year"), obs.get("set_name"))
        if key:
            buckets_by_level["family"].setdefault(key, []).append(price)

        buckets_by_level["price_tier"].setdefault(_price_tier_key(player, card_type, price), []).append(price)

    table: dict[str, dict[tuple, CompStats]] = {level: {} for level in COMP_LEVELS}
    for level, buckets in buckets_by_level.items():
        for key, prices in buckets.items():
            if len(prices) >= min_comps_required:
                table[level][key] = CompStats(median=statistics.median(prices), sample_size=len(prices), is_fallback=True)
    return table


def lookup_hierarchical_comp(
    hier_table: dict[str, dict[tuple, CompStats]],
    player: str,
    card_type: str,
    price: float,
    grader: Optional[str] = None,
    grade: Optional[str] = None,
    year: Optional[int] = None,
    set_name: Optional[str] = None,
    parallel: Optional[str] = None,
    card_number: Optional[str] = None,
) -> Optional[tuple[CompStats, str]]:
    """Tries exact -> near_exact -> family -> price_tier in order, returns
    (CompStats, level_matched) for the first level that both applies (the
    listing has the identity fields that level needs) and has enough
    samples. None if nothing matched at any level -- callers must skip
    flagging, never guess.
    """
    key = _exact_key(player, year, set_name, parallel, card_number, grader, grade)
    if key and key in hier_table["exact"]:
        return hier_table["exact"][key], "exact"

    key = _near_exact_key(player, year, set_name, parallel, card_type)
    if key and key in hier_table["near_exact"]:
        return hier_table["near_exact"][key], "near_exact"

    key = _family_key(player, year, set_name)
    if key and key in hier_table["family"]:
        return hier_table["family"][key], "family"

    key = _price_tier_key(player, card_type, price)
    if key in hier_table["price_tier"]:
        return hier_table["price_tier"][key], "price_tier"

    return None
