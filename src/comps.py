"""Market-value ("comp") calculation. Deliberately dumb: group prices by
(player, raw-vs-graded), take the median, done. No weighting, no outlier
trimming, no per-grade sub-buckets -- if a number looks wrong you can go
look at the exact price list that produced it.

Comps are computed once per run from whatever sold data ebay_client could
get (real sold comps via Marketplace Insights when available, otherwise a
fallback list built from active-listing prices -- see
ebay_client.search_sold_items's docstring for why that fallback exists).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


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
    sold_by_bucket: dict[tuple[str, str], list[float]],
    min_comps_required: int,
    fallback_by_bucket: dict[tuple[str, str], list[float]] | None = None,
) -> dict[tuple[str, str], CompStats]:
    """sold_by_bucket / fallback_by_bucket keys are (player, card_type).

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
