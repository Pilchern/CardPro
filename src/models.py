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

    # filled in once comps + dedupe run
    comp_median: Optional[float] = None
    comp_sample_size: int = 0
    comp_is_fallback: bool = False  # True if comp came from active-listing proxy, not real sold data
    pct_under_market: Optional[float] = None
    dollar_savings: Optional[float] = None  # comp_median - price; primary ranking key, see report.rank_deals
    comp_level_matched: Optional[str] = None  # "exact" | "near_exact" | "family" | "price_tier" -- see comps.py
    comp_confidence: Optional[str] = None  # "high" | "medium" | "low" -- derived from comp_level_matched
