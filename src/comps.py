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
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

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
