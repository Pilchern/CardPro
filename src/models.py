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
