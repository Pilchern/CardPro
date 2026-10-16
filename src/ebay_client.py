"""eBay API access: an OAuth app token, active-listing search (Browse API),
and sold/completed comps (Marketplace Insights API).

Browse is the live path: approved 2026-10-15, and main.py runs it alongside
the alert emails into one comp corpus. It returns ACTIVE listings only.

Marketplace Insights (sold prices) is a limited release that a standard
developer account is not granted; on 2026-10-15 it returned 404 for this
app. It is off by default (ebay.marketplace_insights_enabled) so a run does
not make a wasted call per player. If enabled without access, the calls
below log a warning and return an empty list rather than crashing. Real
sold prices come from config/sold_comps.json, entered by hand.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
INSIGHTS_SEARCH_URL = "https://api.ebay.com/buy/marketplace/insights/v1_beta/item_sales/search"

_token_cache: dict[str, tuple[str, float]] = {}


def get_app_token(client_id: str, client_secret: str) -> str:
    """Client-credentials OAuth token, cached in-process until near expiry."""
    cached = _token_cache.get(client_id)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        OAUTH_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_at = time.time() + int(payload.get("expires_in", 7200))
    _token_cache[client_id] = (token, expires_at)
    return token


# Browse caps a page at 200 items and an offset at 10,000.
BROWSE_MAX_PAGE_SIZE = 200


def search_active_listings(
    query: str,
    token: str,
    category_id: str,
    marketplace_id: str,
    limit: int = 50,
    *,
    sort: Optional[str] = None,
    buying_options: tuple = ("FIXED_PRICE", "AUCTION"),
    max_pages: int = 1,
    min_price: Optional[float] = None,
    failures: Optional[list] = None,
) -> list[dict]:
    """Active for-sale listings for `query`. Returns raw Browse API item dicts.

    `min_price` (USD, item price) narrows the search server-side; None
    searches every price.

    `limit` is the page size and `max_pages` how many pages to walk, so one
    call returns at most limit * max_pages items. Measured 2026-10-15: every
    watchlist player filled a 200-item page, so one page in eBay's default
    best-match order is a slice of the market, not the market. `sort` is
    Browse's own sort key -- "newlyListed" or "endingSoonest" -- and None
    keeps best match.

    A failed page ends the walk and keeps what earlier pages returned; a
    failed first page is [] with a logged warning, same as before. Pass a
    list as `failures` to also get each failure back as "<status> <body>",
    because a log nobody reads is how a dead search looks like a quiet day.
    """
    params = {
        "q": query,
        "category_ids": category_id,
        "limit": str(min(limit, BROWSE_MAX_PAGE_SIZE)),
        "filter": "buyingOptions:{%s}" % "|".join(buying_options),
    }
    if min_price is not None:
        # eBay's documented lower-bound form: "price:[20]" (no "..").
        params["filter"] += ",price:[%s],priceCurrency:USD" % format(min_price, "g")
    if sort:
        params["sort"] = sort
    items: list[dict] = []
    for page in range(max(max_pages, 1)):
        params["offset"] = str(page * min(limit, BROWSE_MAX_PAGE_SIZE))
        resp = requests.get(
            BROWSE_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
            },
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("eBay Browse search failed for %r: %s %s", query, resp.status_code, resp.text[:300])
            if failures is not None:
                failures.append("{} {}".format(resp.status_code, resp.text[:120]))
            break
        payload = resp.json()
        items.extend(payload.get("itemSummaries", []))
        if not payload.get("next"):
            break
    return items


def search_sold_items(
    query: str,
    token: str,
    category_id: str,
    marketplace_id: str,
    lookback_days: int,
    limit: int = 100,
) -> list[dict]:
    """Sold/completed items for `query` via Marketplace Insights.

    Returns [] (with a logged warning) if the app lacks Insights access --
    see the module docstring. Callers must treat an empty list as "no
    comps available", not as "market value is zero".
    """
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = requests.get(
        INSIGHTS_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        },
        params={
            "q": query,
            "category_ids": category_id,
            "limit": str(limit),
            "filter": f"lastSoldDate:[{since}..]",
        },
        timeout=30,
    )
    if resp.status_code == 403:
        logger.warning(
            "eBay Marketplace Insights API returned 403 for %r -- your app "
            "likely doesn't have Insights access (it's a limited-release "
            "API you must apply for separately). Falling back to active-"
            "listing-based comps for this query.",
            query,
        )
        return []
    if resp.status_code != 200:
        logger.warning("eBay sold-items search failed for %r: %s %s", query, resp.status_code, resp.text[:300])
        return []
    return resp.json().get("itemSales", [])


def extract_price(item: dict) -> Optional[float]:
    price = item.get("price") or item.get("lastSoldPrice")
    if not price:
        return None
    try:
        return float(price["value"])
    except (KeyError, TypeError, ValueError):
        return None


def extract_listing_price(item: dict) -> Optional[float]:
    """What a Browse listing costs right now: the current bid for an auction,
    the asking price otherwise. Browse omits `price` on most auctions and
    carries the bid in `currentBidPrice`; reading `price` alone would drop
    every auction, which is the cheap-auction section's whole supply."""
    if "AUCTION" in (item.get("buyingOptions") or []) and item.get("currentBidPrice"):
        try:
            return float(item["currentBidPrice"]["value"])
        except (KeyError, TypeError, ValueError):
            pass
    return extract_price(item)


def extract_shipping(item: dict) -> Optional[float]:
    """Cheapest stated shipping cost, or None when eBay doesn't state one
    (calculated shipping has no cost until a buyer's postcode is known).
    None, never 0.0: an unknown cost assumed free is a deal that isn't."""
    costs = []
    for option in item.get("shippingOptions") or []:
        cost = option.get("shippingCost")
        if not cost:
            continue
        try:
            costs.append(float(cost["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    return min(costs) if costs else None


def time_left_text(item: dict, now: Optional[datetime] = None) -> Optional[str]:
    """itemEndDate as the countdown text the alert emails use ("2d 04h",
    "3h 12m", "45m"), so the report reads both sources with one parser.
    None when the end date is missing or unreadable -- unknown sorts last."""
    end = item.get("itemEndDate")
    if not end:
        return None
    try:
        ends_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    minutes = int(((ends_at - (now or datetime.now(timezone.utc))).total_seconds()) // 60)
    if minutes < 0:
        return None
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"
