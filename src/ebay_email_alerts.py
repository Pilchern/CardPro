"""Reads eBay's own saved-search alert emails out of your Gmail inbox via
IMAP, instead of eBay's API (this account's application was declined) or
scraping eBay's site (nobody's asked us to fight eBay's own defenses, and
this isn't that).

Setup this depends on (one-time, on eBay's site, not in this repo): for
each watchlist player, create a saved search on eBay and turn on email
alerts for it. eBay decides what to send and when (their docs say once
daily, only when new matching listings appeared in the last 24h) -- this
just reads mail you already receive, using the same Gmail App Password
already used for SMTP (App Passwords work for IMAP too, no new credential).

VALIDATED (2026-08-18): extract_listings_from_html() was confirmed working
against 14 real alert emails / 327 real extracted listings / 96 correctly
matched watchlist listings, with no observed player-name collisions. One
known minor quirk: eBay truncates long titles in these emails, which can
occasionally cut a grade number mid-digit (e.g. "PSA 1…" that's really a
truncated "PSA 10"). That only affects the grade text shown in reports,
not comp bucketing (raw vs. graded is unaffected), so it's cosmetic. If
eBay changes their email template later, re-run
`python -m scripts.test_ebay_alerts --raw` to re-validate.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Optional
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{2})?)")
ITEM_URL_RE = re.compile(r"(https?://[^\s\"'<>]*?/itm/\d+)|(/itm/\d+)")

TRUNCATION_MARKERS = ("…", "...")  # eBay truncates long titles in these emails with an ellipsis

_FULL_TITLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}
_TITLE_SUFFIXES_TO_STRIP = (" | eBay", " for sale online | eBay", " | eBay US")


def fetch_alert_messages(
    gmail_address: str,
    gmail_app_password: str,
    sender_contains: str,
    lookback_days: int,
) -> list[Message]:
    """Logs into Gmail read-only over IMAP and returns parsed Message
    objects for recent emails from `sender_contains`. Never deletes,
    marks read, or modifies anything -- opens INBOX readonly.
    """
    messages: list[Message] = []
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
        imap.login(gmail_address, gmail_app_password)
        imap.select("INBOX", readonly=True)

        since = _imap_date(lookback_days)
        status, data = imap.search(None, f'(SINCE {since} FROM "{sender_contains}")')
        if status != "OK" or not data or not data[0]:
            return messages

        for num in data[0].split():
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            messages.append(email.message_from_bytes(msg_data[0][1]))
    return messages


def _imap_date(lookback_days: int) -> str:
    since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    return since_dt.strftime("%d-%b-%Y")


def get_html_body(msg: Message) -> Optional[str]:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return None
    if msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return None


def extract_listings_from_html(html: str) -> list[dict]:
    """Returns [{title, url, price}] best-effort -- see module docstring
    on why this is provisional.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        clean_url = _find_item_url(a["href"])
        if not clean_url or clean_url in seen_urls:
            continue

        title = a.get_text(strip=True)
        if not title:
            continue

        price = _find_nearby_price(a)
        seen_urls.add(clean_url)
        results.append({"title": title, "url": clean_url, "price": price})

    return results


def _find_item_url(href: str) -> Optional[str]:
    """Matches eBay's stable /itm/<id> item-link pattern directly in the
    href, and falls back to URL-decoding first -- marketing/redirect links
    often wrap the real destination as a percent-encoded query param (e.g.
    ...?url=https%3A%2F%2Fwww.ebay.com%2Fitm%2F123), which won't match
    literally without decoding first.
    """
    match = ITEM_URL_RE.search(href) or ITEM_URL_RE.search(unquote(href))
    if not match:
        return None
    url = match.group(1) or match.group(2)
    if not url.startswith("http"):
        url = "https://www.ebay.com" + url
    return url


def _find_nearby_price(anchor_tag) -> Optional[float]:
    """Looks for a price close to this specific listing's title link --
    deliberately scoped narrowly so it doesn't pick up a neighboring
    listing's price when several listings share a flat structure with no
    per-listing wrapper (title link, then a price element as its sibling,
    repeated -- a common real-world email-template pattern).
    """
    # 1. the link's own text
    price = _extract_price(anchor_tag.get_text(" ", strip=True))
    if price is not None:
        return price

    # 2. next siblings at the same level, stopping at the next item link
    #    (that belongs to the next listing, not this one)
    for sibling in anchor_tag.find_next_siblings():
        if getattr(sibling, "find", None) and (
            (sibling.name == "a" and sibling.get("href") and ITEM_URL_RE.search(sibling.get("href", "")))
            or sibling.find("a", href=ITEM_URL_RE)
        ):
            break
        price = _extract_price(sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling))
        if price is not None:
            return price

    # 3. fall back to the narrowest ancestor that doesn't itself contain
    #    another listing's item link (too broad = wrong listing's price)
    for ancestor in anchor_tag.parents:
        other_item_links = [
            a for a in ancestor.find_all("a", href=True) if a is not anchor_tag and ITEM_URL_RE.search(a["href"])
        ]
        if other_item_links:
            break
        price = _extract_price(ancestor.get_text(" ", strip=True))
        if price is not None:
            return price

    return None


def _extract_price(text: str) -> Optional[float]:
    match = PRICE_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def looks_truncated(title: str) -> bool:
    return title.rstrip().endswith(TRUNCATION_MARKERS)


def fetch_full_title(url: str, timeout: int = 10) -> Optional[str]:
    """Best-effort fetch of the real (non-truncated) title from an eBay
    item page -- eBay's alert emails truncate long titles, which can cut a
    grade number mid-digit (a "PSA 10" showing as "PSA 1…"). Uses the
    page's <title> element rather than guessing eBay's CSS class names,
    since document titles are a far more stable target across redesigns.

    Returns None on ANY failure (network error, non-200, no <title>) --
    callers MUST treat that as "keep the truncated title," not an error.
    This has not been tested against eBay's real item pages (this
    project's sandbox can't reach ebay.com to check) -- if it turns out
    eBay blocks plain requests here the way Craigslist did, this will
    reliably return None and the caller's fallback (marking the grade as
    uncertain rather than asserting a possibly-wrong number) takes over
    with no further escalation attempted.

    Only call this for listings that already cleared the deal threshold
    (a handful a day) -- not the full raw batch -- to keep this occasional
    rather than high-volume.
    """
    try:
        resp = requests.get(url, headers=_FULL_TITLE_HEADERS, timeout=timeout)
    except requests.RequestException:
        logger.warning("Couldn't fetch full title for %s (request failed)", url)
        return None
    if resp.status_code != 200:
        logger.warning("Couldn't fetch full title for %s (HTTP %s)", url, resp.status_code)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    if not soup.title or not soup.title.string:
        return None
    title = soup.title.get_text(strip=True)
    for suffix in _TITLE_SUFFIXES_TO_STRIP:
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            break
    return title.strip() or None


def fetch_alert_listings(
    gmail_address: str,
    gmail_app_password: str,
    sender_contains: str,
    lookback_days: int,
) -> list[dict]:
    """Full pipeline: IMAP fetch -> HTML extraction -> [{title, url, price}].

    Logs a warning if alert emails were found but nothing could be
    extracted from any of them -- that combination almost always means
    eBay changed their email template (or something else broke the HTML
    parse), not that eBay legitimately had nothing new to report. Without
    this, "the parser silently stopped working" and "no new listings
    today" look identical in the report/logs -- see docs/AUDIT_AND_ROADMAP.md.
    """
    messages = fetch_alert_messages(gmail_address, gmail_app_password, sender_contains, lookback_days)
    listings = []
    for msg in messages:
        html = get_html_body(msg)
        if not html:
            continue
        listings.extend(extract_listings_from_html(html))

    if messages and not listings:
        logger.warning(
            "Found %d eBay alert email(s) but extracted 0 listings from any of them -- "
            "this usually means eBay changed their email template and extract_listings_from_html "
            "needs updating, not that there were legitimately no new listings. "
            "Re-run `python -m scripts.test_ebay_alerts --raw` to check.",
            len(messages),
        )
    return listings
