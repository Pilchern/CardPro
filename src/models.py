"""Plain data holders shared across modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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

    # filled in once comps + dedupe run
    comp_median: Optional[float] = None
    comp_sample_size: int = 0
    comp_is_fallback: bool = False  # True if comp came from active-listing proxy, not real sold data
    pct_under_market: Optional[float] = None
    dollar_savings: Optional[float] = None  # comp_median - price; primary ranking key, see report.rank_deals
