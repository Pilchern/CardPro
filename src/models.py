"""Plain data holders shared across modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.card_identity import CardIdentity


@dataclass
class Listing:
    """One active for-sale listing, eBay or Craigslist."""

    id: str  # eBay itemId, or the Craigslist URL (both are stable + unique)
    source: str  # "ebay" | "craigslist"
    title: str
    price: Optional[float]  # None if we couldn't parse a price (CL titles vary)
    url: str
    player: str  # matched watchlist player
    card_type: str  # "graded" | "raw"
    grader: Optional[str] = None  # PSA / BGS / SGC / CSG
    grade: Optional[str] = None  # e.g. "9", "9.5"
    title_truncated: bool = False  # True if the grade might be wrong -- see ebay_email_alerts.looks_truncated
    player_tier: str = "legend"  # "legend" | "young_core" -- display tag only, see config/watchlist.json
    is_rookie_card: bool = False  # keyword match on "RC"/"Rookie" -- see matcher.detect_rookie_card
    card_identity: Optional[CardIdentity] = None  # year/set/parallel/etc -- see card_identity.py
    shipping_price: Optional[float] = None  # None means unknown, NOT $0 -- see ebay_email_alerts._extract_shipping

    # filled in once comps + dedupe run
    comp_median: Optional[float] = None
    comp_sample_size: int = 0
    comp_is_fallback: bool = False  # True if comp came from active-listing proxy, not real sold data
    pct_under_market: Optional[float] = None
    dollar_savings: Optional[float] = None  # comp_median - price; primary ranking key, see report.rank_deals
    comp_level_matched: Optional[str] = None  # "exact" | "near_exact" | "family" | "price_tier" -- see comps.py
    comp_confidence: Optional[str] = None  # "high" | "medium" | "low" -- derived from comp_level_matched

    # --- CardPro 2.0 fields -------------------------------------------------
    # Listing type. "unknown" is a real answer, not a placeholder: eBay's
    # alert emails don't always say, and an auction's current bid is not a
    # price. Never default this to "fixed_price" -- see
    # ebay_email_alerts._detect_listing_type.
    listing_type: str = "unknown"
    bid_count: Optional[int] = None
    time_left_text: Optional[str] = None  # raw countdown text, e.g. "2d 04h"
    has_best_offer: bool = False

    # Canonical negative signals found in the title (reprint, custom, sealed
    # product, break slot, ...) -- see card_identity.NEGATIVE_SIGNAL_LABELS.
    negative_signals: tuple = ()
    matched_players: tuple = ()  # every watchlist player in the title; >1 means a multi-player card

    # Why this listing did NOT become a reported opportunity, or why it was
    # downgraded. Exactly one canonical reason from src/reasons.py. Nothing
    # leaves the pipeline unexplained -- 21% of listings used to vanish
    # silently, which made it impossible to tell "found nothing" from "broke".
    rejection_reason: Optional[str] = None

    # The full comp match (comps.CompMatch) backing market_value: level,
    # confidence, sample size, range, recency, dispersion, why it was or
    # wasn't allowed to declare a deal. Kept whole rather than flattened so
    # the report can explain the valuation instead of asserting it.
    comp_match: Optional[object] = None
    market_value: Optional[float] = None  # the estimate actually used, from comp_match.stats.median

    # Deal economics (economics.DealEconomics): acquisition cost, fees,
    # expected net proceeds, profit, ROI, and the assumptions behind them.
    economics: Optional[object] = None
    max_rational_bid: Optional[float] = None  # auctions only -- highest bid that still keeps your margin

    # An explicit acquisition target this listing satisfies (targets.TargetHit),
    # or None. A target hit is NOT a claim that the card is underpriced -- it
    # means "the card you asked for is available at the price you set". The two
    # are reported separately on purpose.
    target_hit: Optional[object] = None

    # Set by the pipeline once every gate has run. is_opportunity means
    # "CardPro is willing to stand behind this as below market": it requires a
    # flag-eligible comp, so it is never true off a context-only level.
    is_opportunity: bool = False
    is_price_drop: bool = False  # seen before, and cheaper than last time
    previous_price: Optional[float] = None  # what it was last time we saw it, for price drops

    @property
    def is_auction(self) -> bool:
        return self.listing_type == "auction"

    @property
    def listing_type_known(self) -> bool:
        return self.listing_type in ("auction", "fixed_price")

    @property
    def total_cost(self) -> Optional[float]:
        """Price + shipping when shipping is known, else just price (the
        same number used before shipping tracking existed) -- never
        silently assumes $0 shipping. Check shipping_price is None
        separately to know whether this figure includes shipping or not."""
        if self.price is None:
            return None
        if self.shipping_price is None:
            return self.price
        return self.price + self.shipping_price
