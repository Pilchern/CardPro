"""Craigslist has no API; this pulls each search's RSS feed instead.

https://<site>.craigslist.org/search/<category>?format=rss&query=<term>
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{2})?)")

# Craigslist's bot filter rejects generic/self-identifying User-Agents
# (e.g. our old "CardDealScraper/1.0") outright. Present as a real desktop
# browser instead, with the rest of the headers a browser would normally
# send along -- a bare UA swap alone is sometimes not enough.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def search(term: str, site: str, category: str = "sss") -> list[dict]:
    """Returns a list of {title, link, price} dicts for one search term."""
    url = f"https://{site}.craigslist.org/search/{category}"
    resp = requests.get(url, params={"format": "rss", "query": term}, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        logger.warning(
            "Craigslist search failed for %r: %s -- body: %.300s", term, resp.status_code, resp.text
        )
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        logger.warning("Craigslist RSS for %r didn't parse as XML", term)
        return []

    # RSS 2.0: channel/item, each with title/link/description
    results = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None or not title_el.text or not link_el.text:
            continue
        title = title_el.text.strip()
        link = link_el.text.strip()
        results.append({"title": title, "link": link, "price": _extract_price(title)})
    return results


def _extract_price(title: str) -> Optional[float]:
    match = PRICE_RE.search(title)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None
