"""eBay API access: an OAuth app token, active-listing search (Browse API),
and sold/completed comps (Marketplace Insights API).

IMPORTANT LIMITATION -- read before relying on comps:
The Browse API (buy.browse) only returns ACTIVE listings; eBay does not
expose sold/completed items through it. Historical sold prices require the
Marketplace Insights API (buy.marketplace.insights), which is a *limited
release* -- a standard free developer account is not granted access to it
by default, you have to apply for it in the eBay developer portal. If your
app doesn't have that scope, calls below will fail with a 403 and this
module logs a warning and returns an empty list rather than crashing.

Practically: until/unless Marketplace Insights access is approved, comps.py
falls back to using the median of *current active* listings as a rough
proxy for market value (see comps.py docstring). Treat that fallback as
weaker signal than real sold comps, and consider maintaining a manual
comps override (not built in v1) if you want tighter numbers sooner.
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


def search_active_listings(
    query: str,
    token: str,
    category_id: str,
    marketplace_id: str,
    limit: int = 50,
) -> list[dict]:
    """Active for-sale listings for `query`. Returns raw Browse API item dicts."""
    resp = requests.get(
        BROWSE_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        },
        params={
            "q": query,
            "category_ids": category_id,
            "limit": str(limit),
            "filter": "buyingOptions:{FIXED_PRICE|AUCTION}",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("eBay Browse search failed for %r: %s %s", query, resp.status_code, resp.text[:300])
        return []
    return resp.json().get("itemSummaries", [])


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
