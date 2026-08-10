"""Craigslist has explicit anti-automation defenses: confirmed via a
dedicated blockID-tagged 403 page that blocks Playwright/CDP-driven
browser automation (both headless and headed) exactly the same as plain
HTTP clients. That means it's fingerprinting the automation layer itself,
not just headers or IP -- deliberately defeating that kind of block wall
(stealth-patching the browser fingerprint, etc.) isn't something this
project does.

Instead of scraping, this just builds a ready-to-click search URL per
watchlist player so the daily email can link straight to a live Craigslist
search. Nothing is fetched or parsed automatically -- you skim it
yourself.
"""
from __future__ import annotations

from urllib.parse import urlencode


def search_url(term: str, site: str, category: str = "sss") -> str:
    query = urlencode({"query": term})
    return f"https://{site}.craigslist.org/search/{category}?{query}"
