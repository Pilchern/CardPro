"""Market-value ("comp") calculation.

This module contains TWO engines:

* **CompEngine (current)** -- the identity- and market-segmented engine at the
  bottom of this file. Use this for anything that can lead to spending money.
* **Everything above it (LEGACY / DEPRECATED)** -- ``price_tier``,
  ``PRICE_TIERS``, ``CompStats``, ``compute_median``, ``build_comp_table``,
  ``COMP_LEVELS``, ``CONFIDENCE_BY_LEVEL``, ``build_hierarchical_comp_table``
  and ``lookup_hierarchical_comp``. Kept only because the dormant eBay-API
  path still calls them and their tests still pass. **They must not be used to
  declare a deal.** Measured against the live corpus they produced 15 flagged
  "deals", 15 of which were artifacts (see docs/CARDPRO_2_AUDIT.md 2.2): their
  dominant level buckets by *price*, so being cheap inside your own bucket is
  itself the "discount"; they pool PSA 10 with PSA 8; they treat unknown
  parallel as matching unknown parallel; and they include a listing in the
  median used to judge it. CompEngine exists because none of that is fixable
  in place without breaking the callers/tests that still depend on the exact
  old behaviour.

LEGACY ENGINE (below, unchanged)
--------------------------------
Deliberately dumb: group prices by
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

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# (label, min_inclusive, max_exclusive). Last tier's max is unbounded.
PRICE_TIERS = [
    ("under_5", 0, 5),
    ("5_to_25", 5, 25),
    ("25_to_100", 25, 100),
    ("100_plus", 100, float("inf")),
]


def price_tier(price: float) -> str:
    """Which price bracket a price falls in.

    Still used by CompEngine's context-only ``price_tier`` level, but never
    again as grounds for a deal: a bucket defined by price cannot tell you
    anything about price (audit failure mode #1).
    """
    for label, lo, hi in PRICE_TIERS:
        if lo <= price < hi:
            return label
    return PRICE_TIERS[-1][0]


@dataclass
class CompStats:
    """LEGACY. Superseded by CompStatsV2 -- no outlier trim, no recency
    weighting, no dispersion or staleness signal."""

    median: float
    sample_size: int
    is_fallback: bool  # True if built from active listings, not real sold comps


def compute_median(prices: list[float], is_fallback: bool = False) -> CompStats | None:
    """LEGACY. None if there isn't enough data to trust a median from.

    Superseded by CompEngine, which trims outliers and weights by recency.
    """
    if not prices:
        return None
    return CompStats(median=statistics.median(prices), sample_size=len(prices), is_fallback=is_fallback)


def build_comp_table(
    sold_by_bucket: dict[tuple, list[float]],
    min_comps_required: int,
    fallback_by_bucket: dict[tuple, list[float]] | None = None,
) -> dict[tuple, CompStats]:
    """LEGACY -- do not flag deals from this table. Kept for the dormant
    eBay-API path (main.flag_deals) and its tests.

    sold_by_bucket / fallback_by_bucket keys are (player, card_type,
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


# LEGACY level names/confidences. The current engine's levels are in
# LEVEL_SPECS at the bottom of this file and are NOT the same set.
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
    """LEGACY -- superseded by CompEngine; do not flag deals from this table.

    It pools grades under one "graded" card_type, treats unknown parallel as
    a matching parallel, cannot exclude a listing from its own bucket, and
    its last level is circular. See the module docstring.

    Builds all four comp levels from a flat list of observation dicts
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
    """LEGACY -- superseded by CompEngine.lookup(); never flag from its result.

    Tries exact -> near_exact -> family -> price_tier in order, returns
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


# ---------------------------------------------------------------------------
# CURRENT ENGINE: CompEngine
# ---------------------------------------------------------------------------
#
# WHY a second engine instead of fixing the one above: the legacy hierarchy has
# a live (dormant) caller and a test suite that pins its exact behaviour, and
# every fix below changes bucket keys, statistics and return types at once.
# Two engines side by side for one migration is cheaper than a half-migrated
# one. The legacy engine is frozen; all new work goes here.
#
# What this engine changes, and why (docs/CARDPRO_2_AUDIT.md 1.2, 2.2, 3):
#
#  1. Only levels that identify an actual card can declare a deal. The
#     price-tier level survives ONLY as context, because a bucket defined by
#     price says nothing about whether a price is low: the median of everything
#     in $25-$100 is ~$44, so every $25-$31 card is "30% under market" by
#     construction. That single rung supplied 76% of all production
#     valuations and all 15 of the flagged "deals".
#  2. Every card-level bucket is segmented by MARKET, not by "raw vs graded".
#     A PSA 10, a PSA 9 and a PSA 9 (OC) are three different markets that
#     happen to share a piece of cardboard. Pooling them is what makes a PSA 8
#     look like a steal against a PSA-10-inflated median.
#  3. Unknown never matches unknown. Two listings whose parallel we failed to
#     parse are not "the same parallel"; with parallel fill rate at 3% that
#     mistake is the common case, not the edge case. Such listings fall to
#     same_set, which is deliberately context-only.
#  4. A listing is excluded from the comp that judges it (exclude_id). At
#     n=3 a listing was supplying a third of its own median.
#  5. Real statistics: MAD outlier trim, time-decay weighted median, plus
#     staleness, dispersion and sample-span gates that can block a flag and
#     say why.
#  6. A bucket must be spread over calendar time, not just deep. The daily
#     scan records every listing it sees each morning, so six asking prices
#     captured in one run are one snapshot six listings deep, not six
#     independent readings of a market. Measured: the live corpus holds 563
#     observations on exactly 2 calendar dates. The staleness gate cannot
#     catch this -- it only asks whether the NEWEST point is recent, so a
#     bucket where everything landed this morning is maximally fresh and
#     maximally un-independent. See the sample-span gate below.
#
# Deliberate non-goals here: no price prediction, no grade modelling, no
# blended score, nothing the report cannot explain in one line. Every
# downgrade this engine applies is a named reason a human can check.

#: Confidence ladder, best first. "high" is reserved for sold data.
CONFIDENCE_LADDER = ("high", "medium", "low")

#: Basis values an observation may carry. "asking" is a seller's opinion,
#: "sold" is the market's. Today 100% of this project's corpus is "asking".
BASIS_ASKING = "asking"
BASIS_SOLD = "sold"


def _downgrade(confidence: str, steps: int = 1) -> str:
    """One step down the ladder per reason, floored at "low"."""
    idx = CONFIDENCE_LADDER.index(confidence)
    return CONFIDENCE_LADDER[min(idx + steps, len(CONFIDENCE_LADDER) - 1)]


def _clean(value) -> Optional[str]:
    """Normalise a free-text identity field: None/blank -> None, else a
    stripped string. Blank strings must become None, otherwise "" would act
    as a known-and-matching value and re-introduce unknown-matches-unknown.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_upper(value) -> Optional[str]:
    """Same as _clean but case-folded, for fields with a small controlled
    vocabulary where case is noise rather than meaning (grader, qualifier).
    Set/parallel names are deliberately NOT folded -- normalising those is
    card_identity.py's job, not the comp engine's.
    """
    text = _clean(value)
    return text.upper() if text is not None else None


def _clean_year(value) -> Optional[int]:
    """Years arrive as ints from card_identity but as strings from hand-made
    JSON; 2024 and "2024" are the same year and must land in the same bucket.
    Anything that isn't a plain year is treated as unknown rather than
    coerced -- a wrong year is a wrong card.
    """
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def market_key(
    card_type: Optional[str],
    grader: Optional[str] = None,
    grade: Optional[str] = None,
    qualifier: Optional[str] = None,
) -> Optional[tuple]:
    """The market a card trades in -- the thing every card-level comp bucket
    must be segmented by.

    ("raw",) for a raw card; ("graded", grader, grade, qualifier) for a slab.
    Grader, grade and qualifier are all part of the identity of the market:
    PSA 9 and BGS 9 are different markets, PSA 9 and PSA 10 are different
    markets, and PSA 9 with an "OC" (off-centre) qualifier is a third.

    Returns None for a graded card whose grader or grade is unknown. That is
    the point: a slab we cannot identify has no known market, and must be
    excluded from grade-segmented levels rather than quietly pooled with
    every other slab (audit failure mode #3). Callers treat None as "this
    level does not apply".
    """
    kind = _clean(card_type)
    if kind is None:
        return None
    if kind.lower() != "graded":
        # raw (and any future non-graded type) is its own single market.
        return (kind.lower(),)
    grader_n = _clean_upper(grader)
    grade_n = _clean(grade)
    if grader_n is None or grade_n is None:
        return None
    return ("graded", grader_n, grade_n, _clean_upper(qualifier))


@dataclass(frozen=True)
class LevelSpec:
    """One comp level: how its bucket key is built, and whether a match at
    this level is allowed to declare a deal.

    flag_eligible=False means CONTEXT ONLY: the level may be returned and
    displayed ("here is roughly what this player's 2024 Prizms go for"), but
    the caller must never turn it into a deal. That is the whole fix for
    audit failure mode #1 -- the circular level is still useful as colour and
    is structurally incapable of triggering a purchase.
    """

    name: str
    flag_eligible: bool
    base_confidence: str
    key_fn: Callable[[dict], Optional[tuple]]


def _key_exact(f: dict) -> Optional[tuple]:
    if None in (f["year"], f["set_name"], f["parallel"], f["card_number"], f["market"]):
        return None
    return (f["player"], f["year"], f["set_name"], f["parallel"], f["card_number"], f["market"])


def _key_same_card(f: dict) -> Optional[tuple]:
    if None in (f["year"], f["set_name"], f["parallel"], f["market"]):
        return None
    return (f["player"], f["year"], f["set_name"], f["parallel"], f["market"])


def _key_same_set(f: dict) -> Optional[tuple]:
    if None in (f["year"], f["set_name"], f["market"]):
        return None
    return (f["player"], f["year"], f["set_name"], f["market"])


# There is deliberately NO grade-blind "family" level in this engine.
#
# An earlier design had one, keyed (player, year, set_name) and later
# (player, year, set_name, card_type). Both were wrong for the same reason:
# `same_set` above already covers "same year and set, parallel unknown" while
# still segmenting by the full market key, so the only thing a family level
# added was the ability to pool different grades. That is precisely the
# defect this rebuild exists to remove -- it is how a PSA 9 got shown the
# PSA 10 median as its "market value" (docs/CARDPRO_2_AUDIT.md failure mode
# #3). Labelling such a number "context only" does not make it safe; being
# wrong by 4x is worse than saying nothing, so the level is gone rather than
# demoted.


def _key_price_tier(f: dict) -> Optional[tuple]:
    """Last-resort context: same player, same market, similar price bracket.

    This level is circular by construction -- the bucket is defined by price,
    so the cheap end of every bucket automatically looks "under market". It
    supplied 76% of v1's valuations and every false positive in production
    (docs/CARDPRO_2_AUDIT.md failure mode #1), which is why it can never flag
    a deal. It survives only as context: "cards of this player in this grade
    around this price".

    It keys on the full market, not just card_type, for the same reason the
    grade-blind family level was deleted: a PSA 9 must never be shown the
    PSA 10 median, not even as context.
    """
    if f["price"] is None or f["card_type"] is None:
        return None
    market = market_key(f["card_type"], f.get("grader"), f.get("grade"), f.get("qualifier"))
    if market is None:
        return None
    return (f["player"], market, price_tier(f["price"]))


#: Levels in lookup order, narrowest first. Only the top two can flag.
#:
#: exact / same_card both require a KNOWN parallel, so unknown parallel never
#: matches unknown parallel. same_set is where unknown-parallel listings land;
#: it mixes base cards with refractors and numbered parallels, which is
#: exactly why it is context-only (that mix produced the "$1.25 card is 95%
#: under market" false positive).
LEVEL_SPECS = (
    LevelSpec("exact", True, "high", _key_exact),
    LevelSpec("same_card", True, "medium", _key_same_card),
    LevelSpec("same_set", False, "low", _key_same_set),
    LevelSpec("price_tier", False, "low", _key_price_tier),
)

LEVEL_SPEC_BY_NAME = {spec.name: spec for spec in LEVEL_SPECS}
LEVEL_NAMES = tuple(spec.name for spec in LEVEL_SPECS)
FLAG_ELIGIBLE_LEVELS = tuple(spec.name for spec in LEVEL_SPECS if spec.flag_eligible)


@dataclass
class CompStatsV2:
    """What a comp bucket actually looks like, in enough detail that a human
    can decide whether to believe it. Every field here is meant to be
    printable in the report -- the product forbids a black box, so "median
    $44" alone is not an acceptable output.
    """

    median: float  # time-decay weighted median of the kept points
    mean: float  # plain unweighted mean, shown only as a sanity check vs median
    sample_size: int  # kept points, after self-exclusion and outlier trim
    trimmed_count: int  # points dropped as outliers
    minimum: float
    maximum: float
    newest_date: str
    oldest_date: str
    age_days_newest: int
    age_days_oldest: int
    dispersion: float  # MAD / median; 0.0 when median is 0
    basis: str  # "sold" only if EVERY kept point is sold, else "asking"
    is_stale: bool
    is_dispersed: bool
    # -- sample span -------------------------------------------------------
    # How much CALENDAR TIME the kept points cover, which is a different
    # question from how many of them there are and from how fresh the newest
    # one is. These three trail the rest of the dataclass and carry neutral
    # defaults so that hand-built stats (the report's fixtures, a debugging
    # snippet) keep working; compute_comp_stats always sets all three.
    distinct_dates: int = 1  # number of distinct calendar dates among the kept points
    span_days: int = 1  # oldest..newest inclusive of both ends -- one day is 1, not 0
    is_concentrated: bool = False  # too few dates / too short a span to be independent


@dataclass
class CompMatch:
    """The result of a lookup: the numbers, where they came from, and whether
    they are allowed to move money."""

    stats: CompStatsV2
    level: str
    flag_eligible: bool  # level is flag-eligible AND every quality gate passed
    confidence: str  # "high" | "medium" | "low"
    blocked_reasons: Tuple[str, ...]  # why flag_eligible was downgraded

    @property
    def is_context_only(self) -> bool:
        return not self.flag_eligible


def _parse_date(value) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    """Weighted median: sort by value, walk the sorted values accumulating
    weight, return the value at which cumulative weight crosses half the
    total.

    WHY median and not a weighted mean: even after the MAD trim, one survivor
    at 10x the going rate would drag a mean somewhere no card actually
    trades. The median only cares about ordering, so a surviving outlier
    moves the answer by one position instead of by its magnitude. The cost is
    granularity -- the result is always a price something was actually listed
    at, never an interpolated midpoint. For deciding "is this cheap" that is
    a feature: the comp is a real observation you can go look at.
    """
    if not values:
        raise ValueError("weighted_median() of empty sequence")
    pairs = sorted(zip(values, weights))
    total = math.fsum(w for _, w in pairs)
    if total <= 0:
        # All weights underflowed to zero (every point ancient). Fall back to
        # the unweighted median rather than reporting nothing.
        return float(statistics.median([v for v, _ in pairs]))
    half = total / 2.0
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= half:
            return float(value)
    return float(pairs[-1][0])  # unreachable except for float rounding


def _mad(values: Sequence[float], center: float) -> float:
    """Median absolute deviation about `center`."""
    return float(statistics.median([abs(v - center) for v in values]))


def trim_outliers(
    points: List[dict], mad_threshold: float = 3.5, min_points_to_trim: int = 5
) -> Tuple[List[dict], int]:
    """Drop points more than `mad_threshold` scaled MADs from the median.

    The 0.6745 factor makes one MAD comparable to one standard deviation for
    normal-ish data, so the 3.5 default is the usual "modified z-score"
    cutoff. Its job here is the $2,499 listing sitting in a bucket of $100
    cards -- one of those in a bucket of three is the difference between a
    real comp and a fantasy.

    Two guards, both deliberate:
      * fewer than `min_points_to_trim` points -> trim nothing. With 3 or 4
        observations "outlier" is indistinguishable from "the market", and
        trimming would mostly delete the very cheap listing we are trying to
        evaluate against.
      * MAD == 0 (more than half the points identical) -> trim nothing,
        because every non-identical point would score as infinitely far out.
    """
    if len(points) < min_points_to_trim:
        return points, 0
    prices = [p["price"] for p in points]
    center = float(statistics.median(prices))
    mad = _mad(prices, center)
    if mad <= 0:
        return points, 0
    kept = [p for p in points if abs(0.6745 * (p["price"] - center) / mad) <= mad_threshold]
    if not kept:  # defensive: the median point always survives, so this can't happen
        return points, 0
    return kept, len(points) - len(kept)


def _is_concentrated(
    *,
    basis: str,
    distinct_dates: int,
    span_days: int,
    min_distinct_comp_dates: int,
    min_comp_span_days: int,
) -> bool:
    """Is this bucket too concentrated in time to be independent evidence?

    THE DEFECT. ``min_comps_required`` counts observations, not independent
    readings. The daily scan records every listing it sees each morning, so a
    bucket of six asking prices captured in one run is one snapshot six
    listings deep. The engine used to hand that the same confidence as six
    prices spread over six weeks. The staleness gate does not help: it asks
    only whether the NEWEST point is recent, so an all-this-morning bucket is
    maximally fresh and maximally un-independent. (The related problem of the
    same listing recurring across overlapping alert-lookback windows is
    already solved upstream -- price_history.deduped_observations collapses
    repeat sightings by listing id. What is left is purely calendar spread.)

    THE RULE is a conjunction of two different measures, because each one
    plugs the other's hole:

      * ``distinct_dates >= min_distinct_comp_dates`` -- how many separate
        mornings contributed. This is the unit of independence: one scan is
        one draw from the market, however many listings it returned.
      * ``span_days >= min_comp_span_days`` -- how much calendar time those
        mornings cover.

    Neither alone is enough. A span rule alone passes a bucket of twenty
    prices from one morning plus a single straggler a week later: span 8,
    twenty-one points, still essentially one snapshot -- and it perversely
    rewards keeping one old point around. A distinct-dates rule alone passes
    three consecutive mornings, which is three draws from a market that has
    not had time to move. Requiring both says what we actually mean: several
    separate readings, taken far enough apart to be readings of a market
    rather than of a moment.

    On the case the two rules disagree about -- two observations a month
    apart -- the distinct-dates rule is the one telling the truth. That is a
    well-spread sample of two, and "two" is the problem; a span rule would
    call it healthy. It is already caught by ``thin_sample`` at the current
    minimum of 3, but only by luck, and this gate should not depend on that.

    SOLD COMPS ARE EXEMPT, deliberately, and this is not a loophole.

    Three sales on the same day are three completed transactions: three
    separate buyers each agreed a price with a seller. They are independent
    draws from the market in exactly the sense this gate is trying to
    measure, and the fact that they happen to share a date says nothing about
    them. Three asking prices on the same day are not: they may be one
    seller's optimism listed three times, or one batch dumped by one
    consignor into one morning's alert digest, and their shared date is the
    whole reason to distrust them. A time-concentration rule designed for
    asking prices would, applied to sold data, suppress the only real market
    evidence the system has -- hand-entered sold comps are scarce by
    construction (see src/sold_comps.py: twenty is a realistic ceiling), so a
    gate that demands they arrive on three separate weeks would mean they
    never count at all. That is the opposite of the intended effect.

    Sold comps keep every other gate. Staleness still applies (a sale from
    six months ago is old news), dispersion still applies, and thinness still
    applies. Only the calendar-spread requirement is lifted.

    ``basis`` is "sold" only when EVERY kept point is sold, which is the
    existing all-or-nothing convention on CompStatsV2. A mixed bucket reads
    as "asking" and is therefore gated -- correct, because the asking points
    in it carry exactly the correlation this gate exists to catch.
    """
    if basis == BASIS_SOLD:
        return False
    return distinct_dates < min_distinct_comp_dates or span_days < min_comp_span_days


def compute_comp_stats(
    points: Sequence[dict],
    *,
    today: datetime,
    exclude_id: Optional[str] = None,
    half_life_days: float = 30.0,
    stale_after_days: int = 45,
    max_dispersion: float = 0.5,
    mad_threshold: float = 3.5,
    min_distinct_comp_dates: int = 3,
    min_comp_span_days: int = 7,
) -> Optional[CompStatsV2]:
    """Turn a bucket of observations into CompStatsV2, or None if nothing
    usable is left. Order matters and is the order of the audit's complaints:
    self-exclusion, then outlier trim, then recency weighting, then the
    quality signals computed over exactly the points that survived.
    """
    # Accepts either CompEngine-prepared points (which carry a parsed
    # "_date") or plain observation dicts, so this can be used standalone --
    # e.g. to re-stat one bucket while debugging a comp in the report.
    usable = []
    for point in points:
        if point.get("_date") is None:
            parsed = _parse_date(point.get("date"))
            if parsed is None:
                continue  # no date -> no age -> no honest recency weighting
            point = dict(point, _date=parsed)
        usable.append(point)
    if exclude_id is not None:
        # Failure mode #4: a listing must not be in the median that judges it.
        usable = [p for p in usable if p.get("id") != exclude_id]
    if not usable:
        return None

    kept, trimmed_count = trim_outliers(usable, mad_threshold=mad_threshold)
    prices = [p["price"] for p in kept]

    ages = [max(0, (today.date() - p["_date"].date()).days) for p in kept]
    # Half-life weighting: a 30-day-old asking price counts half as much as
    # today's. Ages are floored at 0 so a clock skew / future-dated
    # observation cannot earn extra weight.
    weights = [0.5 ** (age / float(half_life_days)) for age in ages]
    median = weighted_median(prices, weights)

    # MAD is taken about the reported (weighted) median so dispersion
    # describes the spread around the number we actually publish.
    dispersion = (_mad(prices, median) / median) if median else 0.0
    age_newest, age_oldest = min(ages), max(ages)
    basis = BASIS_SOLD if all(p.get("basis", BASIS_ASKING) == BASIS_SOLD for p in kept) else BASIS_ASKING

    # -- sample span ---------------------------------------------------------
    # Both measures are always reported; only the gate is conditional. Counted
    # over `kept`, i.e. after self-exclusion and the outlier trim, because the
    # question is how spread out the points that produced THIS median are.
    distinct_dates = len({p["_date"].date() for p in kept})
    span_days = age_oldest - age_newest + 1  # inclusive of both ends
    is_concentrated = _is_concentrated(
        basis=basis,
        distinct_dates=distinct_dates,
        span_days=span_days,
        min_distinct_comp_dates=min_distinct_comp_dates,
        min_comp_span_days=min_comp_span_days,
    )

    return CompStatsV2(
        median=median,
        mean=float(statistics.fmean(prices)),
        sample_size=len(kept),
        trimmed_count=trimmed_count,
        minimum=float(min(prices)),
        maximum=float(max(prices)),
        newest_date=max(kept, key=lambda p: p["_date"])["date"],
        oldest_date=min(kept, key=lambda p: p["_date"])["date"],
        age_days_newest=age_newest,
        age_days_oldest=age_oldest,
        dispersion=dispersion,
        basis=basis,
        is_stale=age_newest > stale_after_days,
        is_dispersed=dispersion > max_dispersion,
        distinct_dates=distinct_dates,
        span_days=span_days,
        is_concentrated=is_concentrated,
    )


def assess_comp_match(stats: CompStatsV2, level: str, min_comps_required: int) -> CompMatch:
    """Apply the confidence ladder and the quality gates to a bucket's stats.

    Confidence is a checklist, not a model -- every step is a sentence the
    report can print:
      * start at the level's own confidence (how much of the card we matched)
      * asking-price data can never be "high"; it is what sellers hope for,
        not what buyers paid. 100% of today's corpus is asking, so "medium"
        is this project's honest ceiling until real sold data exists.
      * thin (<5), stale, dispersed, or concentrated in time each cost one
        more step.

    The gates are separate from confidence on purpose: confidence says how
    much to trust the number, the gates say whether it may trigger a
    purchase. A "low" comp is still worth printing.
    """
    spec = LEVEL_SPEC_BY_NAME[level]
    confidence = spec.base_confidence
    if stats.basis != BASIS_SOLD:
        confidence = _downgrade(confidence)
    if stats.sample_size < 5:
        confidence = _downgrade(confidence)
    if stats.is_stale:
        confidence = _downgrade(confidence)
    if stats.is_dispersed:
        confidence = _downgrade(confidence)
    if stats.is_concentrated:
        confidence = _downgrade(confidence)

    reasons = []
    if not spec.flag_eligible:
        reasons.append("context_only_level")
    if stats.sample_size < min_comps_required:
        reasons.append("thin_sample")
    if stats.is_stale:
        reasons.append("stale_comps")
    if stats.is_dispersed:
        reasons.append("dispersed_comps")
    if stats.is_concentrated:
        # Listed last so it never displaces a more specific reason at the
        # head of the tuple for callers that report only the first one.
        reasons.append("concentrated_sample")

    return CompMatch(
        stats=stats,
        level=level,
        flag_eligible=not reasons,
        confidence=confidence,
        blocked_reasons=tuple(reasons),
    )


class CompEngine:
    """Identity- and market-segmented comps with real statistics.

    Build it once per run from the whole observation corpus
    (price_history.deduped_observations() produces exactly the right shape),
    then call lookup() per listing, passing that listing's own id as
    exclude_id.

    An observation is a dict:
        price, date ("YYYY-MM-DD"), id, player, card_type ("raw"|"graded"),
        year, set_name, parallel, card_number, grader, grade
    plus three optional keys absent from older data:
        qualifier (grade qualifier such as "OC" -- a different market),
        basis ("asking" | "sold", default "asking"),
        print_run (carried through untouched; kept in the observation so a
            future level can segment on it without another migration).

    `today` is injected rather than read from the clock so that every run --
    and every test -- is reproducible, and so a backtest can ask "what did
    this comp look like on that date" without lying about ages.
    """

    def __init__(
        self,
        observations: Sequence[dict],
        *,
        min_comps_required: int,
        today: datetime,
        half_life_days: float = 30.0,
        stale_after_days: int = 45,
        max_dispersion: float = 0.5,
        mad_threshold: float = 3.5,
        min_distinct_comp_dates: int = 3,
        min_comp_span_days: int = 7,
    ) -> None:
        self.min_comps_required = min_comps_required
        self.today = today
        self.half_life_days = half_life_days
        self.stale_after_days = stale_after_days
        self.max_dispersion = max_dispersion
        self.mad_threshold = mad_threshold
        # Sample-span gate; see _is_concentrated for the rule and the sold
        # exemption. Defaults: at least 3 separate observation days covering
        # at least a week. Three because two is what a scraper that has been
        # running since yesterday produces -- the live corpus is 563
        # observations on exactly 2 dates -- and because three is the
        # smallest number of readings that can show a level rather than a
        # single line between two points. A week because that is roughly one
        # turnover of eBay's fixed-price/auction listing cycle, so a
        # week-plus span means the listing pool refreshed at least once
        # instead of one weekend's dump being counted several times. Both are
        # cheap to satisfy for any card that genuinely trades, and impossible
        # to satisfy by scanning harder on one morning, which is the point.
        self.min_distinct_comp_dates = min_distinct_comp_dates
        self.min_comp_span_days = min_comp_span_days

        self._buckets: Dict[str, Dict[tuple, List[dict]]] = {spec.name: {} for spec in LEVEL_SPECS}
        self._stats_cache: Dict[tuple, Optional[CompStatsV2]] = {}
        self._skipped = 0

        for obs in observations:
            prepared = self._prepare(obs)
            if prepared is None:
                self._skipped += 1
                continue
            for spec in LEVEL_SPECS:
                key = spec.key_fn(prepared["_fields"])
                if key is not None:
                    self._buckets[spec.name].setdefault(key, []).append(prepared)

    # -- construction helpers -------------------------------------------------

    def _prepare(self, obs: dict) -> Optional[dict]:
        """Normalise one observation and pre-compute its level keys' inputs.

        Returns None for anything unusable: no player, no numeric price, or
        an unparseable date. A dateless observation is dropped rather than
        assigned a guessed age -- recency weighting and the staleness gate
        both depend on the date being real, and price_history has always
        written one.
        """
        player = _clean(obs.get("player"))
        if player is None:
            return None
        try:
            price = float(obs["price"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        parsed_date = _parse_date(obs.get("date"))
        if parsed_date is None:
            return None

        card_type = _clean(obs.get("card_type"))
        fields = {
            "player": player,
            "card_type": card_type.lower() if card_type else None,
            "price": price,
            "year": _clean_year(obs.get("year")),
            "set_name": _clean(obs.get("set_name")),
            "parallel": _clean(obs.get("parallel")),
            "card_number": _clean(obs.get("card_number")),
            "market": market_key(card_type, obs.get("grader"), obs.get("grade"), obs.get("qualifier")),
        }
        return {
            "price": price,
            "date": str(obs.get("date")),
            "_date": parsed_date,
            "id": obs.get("id"),
            "basis": obs.get("basis", BASIS_ASKING),
            "print_run": obs.get("print_run"),
            "_fields": fields,
        }

    # -- public API -----------------------------------------------------------

    def lookup(
        self,
        *,
        player: str,
        card_type: str,
        price: float,
        grader: Optional[str] = None,
        grade: Optional[str] = None,
        qualifier: Optional[str] = None,
        year: Optional[int] = None,
        set_name: Optional[str] = None,
        parallel: Optional[str] = None,
        card_number: Optional[str] = None,
        exclude_id: Optional[str] = None,
    ) -> Optional[CompMatch]:
        """First level that both applies and still has min_comps_required
        points after self-exclusion and outlier trimming.

        A level "applies" only when the listing itself has every identity
        field that level keys on, all of them known. Missing means skip the
        level -- never a guessed match (same rule as card_identity.py).

        None means "no market value known for this card". It never means
        zero, and it is never a reason to flag; it is a reason to report the
        listing as unvalued (audit failure mode #9).
        """
        cleaned_type = _clean(card_type)
        fields = {
            "player": _clean(player),
            "card_type": cleaned_type.lower() if cleaned_type else None,
            "price": price,
            "year": _clean_year(year),
            "set_name": _clean(set_name),
            "parallel": _clean(parallel),
            "card_number": _clean(card_number),
            "market": market_key(card_type, grader, grade, qualifier),
        }
        if fields["player"] is None:
            return None

        for spec in LEVEL_SPECS:
            key = spec.key_fn(fields)
            if key is None:
                continue
            points = self._buckets[spec.name].get(key)
            if not points:
                continue
            stats = self._stats_for(spec.name, key, points, exclude_id)
            if stats is None or stats.sample_size < self.min_comps_required:
                continue
            return assess_comp_match(stats, spec.name, self.min_comps_required)
        return None

    def coverage(self) -> dict:
        """{level: number of buckets holding at least min_comps_required
        observations} -- the data-quality footer's "what could this run
        actually comp against". Counts raw bucket size, before per-listing
        self-exclusion, because it describes the corpus rather than any one
        lookup.
        """
        return {
            level: sum(1 for points in buckets.values() if len(points) >= self.min_comps_required)
            for level, buckets in self._buckets.items()
        }

    def skipped_observations(self) -> int:
        """Observations dropped at construction (no player / bad price / no
        parseable date). Nothing should ever be silently discarded."""
        return self._skipped

    # -- internals ------------------------------------------------------------

    def _stats_for(
        self, level: str, key: tuple, points: List[dict], exclude_id: Optional[str]
    ) -> Optional[CompStatsV2]:
        """Cached per (level, bucket, exclude_id): every listing in a bucket
        triggers the same trim-and-weight work, and a run comps hundreds of
        listings against a few dozen buckets."""
        cache_key = (level, key, exclude_id)
        if cache_key not in self._stats_cache:
            self._stats_cache[cache_key] = compute_comp_stats(
                points,
                today=self.today,
                exclude_id=exclude_id,
                half_life_days=self.half_life_days,
                stale_after_days=self.stale_after_days,
                max_dispersion=self.max_dispersion,
                mad_threshold=self.mad_threshold,
                min_distinct_comp_dates=self.min_distinct_comp_dates,
                min_comp_span_days=self.min_comp_span_days,
            )
        return self._stats_cache[cache_key]
