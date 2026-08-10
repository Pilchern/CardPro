"""Craigslist has no API; this drives a real (headless) Chromium instance
to fetch each search's RSS feed instead of using plain HTTP requests.

Plain HTTP clients (Python requests, curl) get hard-blocked by Craigslist's
bot mitigation even with a realistic browser User-Agent -- confirmed via
their blockID-tagged 403 page -- while a real browser on the same network
loads the same URL fine. A genuine Chromium instance (via Playwright)
presents the TLS/JS fingerprint of an actual browser and gets through the
same way a manually-opened tab does.

https://<site>.craigslist.org/search/<category>?format=rss&query=<term>
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{2})?)")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class CraigslistSession:
    """One browser instance reused across every player search in a run --
    launching a fresh Chromium per search would be slow and wasteful for
    what's otherwise a handful of sequential lookups.

    Usage:
        with CraigslistSession(headless=True) as session:
            session.search("Michael Jordan card", "chicago")
    """

    def __init__(self, headless: bool = True):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._context = self._browser.new_context(user_agent=USER_AGENT)

    def search(self, term: str, site: str, category: str = "sss") -> list[dict]:
        """Returns a list of {title, link, price} dicts for one search term."""
        query = urlencode({"format": "rss", "query": term})
        url = f"https://{site}.craigslist.org/search/{category}?{query}"

        page = self._context.new_page()
        try:
            resp = page.goto(url, timeout=30000)
            if resp is None or resp.status != 200:
                status = resp.status if resp is not None else "no response"
                body_preview = resp.text()[:300] if resp is not None else ""
                logger.warning("Craigslist search failed for %r: %s -- body: %s", term, status, body_preview)
                return []
            body = resp.body()
        finally:
            page.close()

        try:
            root = ET.fromstring(body)
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

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()

    def __enter__(self) -> "CraigslistSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _extract_price(title: str) -> Optional[float]:
    match = PRICE_RE.search(title)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None
