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

Searches Gmail's All Mail (not just Inbox) by default -- see
DEFAULT_MAILBOX below -- so you're free to set up a Gmail filter that
skips the inbox for these alerts (keeps your normal inbox clean) or
archive them after the fact, without CardPro losing sight of them. Only
actually deleting a message (emptying Trash) makes it disappear here.

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

# Gmail's IMAP "Archive" action only removes the \Inbox label -- the
# message still exists under All Mail. Searching All Mail (rather than
# INBOX) means you can freely archive processed alert emails (or set up a
# filter that skips the inbox entirely, e.g. to keep them out of your main
# inbox view) without CardPro ever losing sight of them. Only genuine
# deletion (emptying Trash) makes a message actually disappear from here.
DEFAULT_MAILBOX = "[Gmail]/All Mail"

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{2})?)")
SHIPPING_RE = re.compile(r"\+?\s*\$([\d,]+(?:\.\d{2})?)\s*shipping", re.IGNORECASE)
FREE_SHIPPING_RE = re.compile(r"\bfree\s+shipping\b", re.IGNORECASE)
ITEM_URL_RE = re.compile(r"(https?://[^\s\"'<>]*?/itm/\d+)|(/itm/\d+)")

# --- Listing-type detection -------------------------------------------------
# An auction's current bid is NOT a price. Treating one as a completed sale
# price is the fastest way to make CardPro recommend a bad buy (it's an
# explicit non-negotiable for this project), so the parser has to be able to
# tell the two apart -- and, just as importantly, has to be able to say "I
# can't tell" instead of quietly assuming Buy It Now.
#
# These patterns are deliberately conservative. eBay's alert-email template
# isn't a documented interface and can change without notice, so a miss here
# yields LISTING_TYPE_UNKNOWN (which the report states plainly) rather than a
# confident wrong answer.
BID_COUNT_RE = re.compile(r"\b(\d{1,4})\s*bids?\b", re.IGNORECASE)
CURRENT_BID_RE = re.compile(r"\bcurrent\s+bid\b|\bstarting\s+bid\b|\bplace\s+bid\b", re.IGNORECASE)
BUY_IT_NOW_RE = re.compile(r"\bbuy\s*it\s*now\b|\bbuy-it-now\b|\bBIN\b", re.IGNORECASE)
BEST_OFFER_RE = re.compile(r"\bor\s+best\s+offer\b|\bbest\s+offer\s+accepted\b|\bmake\s+(?:an\s+)?offer\b|\bOBO\b", re.IGNORECASE)
# "6d 04h", "1h 12m", "Time left: 45m" -- eBay shows a countdown only on auctions.
TIME_LEFT_RE = re.compile(
    r"\btime\s+left\b|\b(\d{1,3})\s*d\s*(\d{1,2})\s*h\b|\b(\d{1,2})\s*h\s*(\d{1,2})\s*m\b|\bends?\s+in\b",
    re.IGNORECASE,
)

COUNTDOWN_RE = re.compile(r"\b\d{1,3}\s*d\s*\d{1,2}\s*h\b|\b\d{1,2}\s*h\s*\d{1,2}\s*m\b|\b\d{1,2}\s*m\s*\d{1,2}\s*s\b", re.IGNORECASE)

LISTING_TYPE_AUCTION = "auction"
LISTING_TYPE_FIXED = "fixed_price"
LISTING_TYPE_UNKNOWN = "unknown"

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
    mailbox: str = DEFAULT_MAILBOX,
) -> list[Message]:
    """Logs into Gmail read-only over IMAP and returns parsed Message
    objects for recent emails from `sender_contains`. Never deletes,
    marks read, or modifies anything -- opens `mailbox` readonly, which
    defaults to All Mail rather than INBOX specifically so archiving (or a
    filter that skips the inbox) doesn't make alert emails invisible here
    -- see DEFAULT_MAILBOX.
    """
    messages: list[Message] = []
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
        imap.login(gmail_address, gmail_app_password)
        # imaplib doesn't quote the mailbox name itself -- a name containing
        # a space (like the default "[Gmail]/All Mail") gets sent to the
        # server unquoted and rejected outright ("BAD Could not parse
        # command"). Quoting is always valid IMAP syntax, whether or not the
        # name has special characters, so just always do it.
        imap.select(f'"{mailbox}"', readonly=True)

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
    """Returns [{title, url, price, shipping_price}] best-effort -- see
    module docstring on why this is provisional. shipping_price is None
    when it couldn't be determined (no "shipping" text found nearby) --
    callers must treat that as "unknown", not "$0 shipping". Unlike price
    (VALIDATED against real alert emails, see module docstring), shipping
    extraction hasn't been checked against real data yet -- worst case it
    just stays None for everything and total-cost calculations fall back
    to price alone, same as before this existed. Re-run
    `python -m scripts.test_ebay_alerts --raw` to check the real hit rate.

    Also returns listing_type ("auction" | "fixed_price" | "unknown"),
    bid_count, has_best_offer and time_left_text -- see _detect_listing_type.
    Like shipping, these have NOT been validated against real alert-email
    HTML yet, and they fail safe: no evidence either way yields "unknown",
    which the report states plainly instead of assuming Buy It Now.
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

        price = _find_nearby(a, _extract_price)
        shipping_price = _find_nearby(a, _extract_shipping)
        context = _nearby_text(a)
        listing_type, bid_count = _detect_listing_type(context)
        seen_urls.add(clean_url)
        results.append(
            {
                "title": title,
                "url": clean_url,
                "price": price,
                "shipping_price": shipping_price,
                "listing_type": listing_type,
                "bid_count": bid_count,
                "has_best_offer": bool(BEST_OFFER_RE.search(context)),
                "time_left_text": _extract_time_left(context),
            }
        )

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


def _find_nearby(anchor_tag, extractor) -> Optional[float]:
    """Looks for a value (price or shipping cost -- whatever `extractor`
    pulls out of text) close to this specific listing's title link --
    deliberately scoped narrowly so it doesn't pick up a neighboring
    listing's value when several listings share a flat structure with no
    per-listing wrapper (title link, then a price/shipping element as its
    sibling, repeated -- a common real-world email-template pattern).
    Shared by _find_nearby_price and _find_nearby_shipping so both use the
    identical, already-validated scoping logic.
    """
    # 1. the link's own text
    value = extractor(anchor_tag.get_text(" ", strip=True))
    if value is not None:
        return value

    # 2. next siblings at the same level, stopping at the next item link
    #    (that belongs to the next listing, not this one)
    for sibling in anchor_tag.find_next_siblings():
        if getattr(sibling, "find", None) and (
            (sibling.name == "a" and sibling.get("href") and ITEM_URL_RE.search(sibling.get("href", "")))
            or sibling.find("a", href=ITEM_URL_RE)
        ):
            break
        value = extractor(sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling))
        if value is not None:
            return value

    # 3. fall back to the narrowest ancestor that doesn't itself contain
    #    another listing's item link (too broad = wrong listing's value)
    for ancestor in anchor_tag.parents:
        other_item_links = [
            a for a in ancestor.find_all("a", href=True) if a is not anchor_tag and ITEM_URL_RE.search(a["href"])
        ]
        if other_item_links:
            break
        value = extractor(ancestor.get_text(" ", strip=True))
        if value is not None:
            return value

    return None


def _nearby_text(anchor_tag) -> str:
    """The text CardPro is allowed to read for THIS listing: the link's own
    text plus following siblings up to (not including) the next listing's
    item link, plus the narrowest ancestor that doesn't contain another
    listing. Same scoping rule as _find_nearby -- reused rather than
    reinvented so listing-type detection can never pick up the neighbouring
    listing's "3 bids" and mislabel a Buy It Now as an auction.
    """
    parts = [anchor_tag.get_text(" ", strip=True)]

    for sibling in anchor_tag.find_next_siblings():
        if getattr(sibling, "find", None) and (
            (sibling.name == "a" and sibling.get("href") and ITEM_URL_RE.search(sibling.get("href", "")))
            or sibling.find("a", href=ITEM_URL_RE)
        ):
            break
        parts.append(sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling))

    for ancestor in anchor_tag.parents:
        other_item_links = [
            a for a in ancestor.find_all("a", href=True) if a is not anchor_tag and ITEM_URL_RE.search(a["href"])
        ]
        if other_item_links:
            break
        parts.append(ancestor.get_text(" ", strip=True))

    return " ".join(part for part in parts if part)


def _detect_listing_type(text: str):
    """Returns (listing_type, bid_count).

    Bid evidence wins over Buy It Now evidence: eBay auctions can also carry
    a Buy It Now price until the first bid, and once bidding has started the
    number shown is a *current bid*. Being wrong in that direction is the
    expensive mistake, so it's the one this resolves against.

    Returns LISTING_TYPE_UNKNOWN when there's no evidence either way. That is
    a real answer, not a failure -- callers must surface it rather than
    defaulting to "fixed price", because a silent default is exactly how a
    current bid gets reported as an asking price.
    """
    bid_match = BID_COUNT_RE.search(text)
    bid_count = int(bid_match.group(1)) if bid_match else None

    if bid_match or CURRENT_BID_RE.search(text) or TIME_LEFT_RE.search(text):
        return LISTING_TYPE_AUCTION, bid_count
    if BUY_IT_NOW_RE.search(text):
        return LISTING_TYPE_FIXED, None
    return LISTING_TYPE_UNKNOWN, None


def _extract_time_left(text: str) -> Optional[str]:
    """The raw countdown string ("6d 04h") when eBay included one, else None.
    Kept as text on purpose: it's shown to a human for triage, and converting
    it to an absolute end time would require assuming when the email was
    generated, which is a guess this project doesn't make.
    """
    match = TIME_LEFT_RE.search(text)
    if not match:
        return None
    # Prefer an actual countdown ("2d 04h") over the bare words "Time left"
    # when both appear -- the countdown is the part a human triages on.
    countdown = COUNTDOWN_RE.search(text)
    return (countdown or match).group(0).strip()


def _extract_price(text: str) -> Optional[float]:
    match = PRICE_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_shipping(text: str) -> Optional[float]:
    """None means unknown, not "$0 shipping" -- only an explicit "Free
    shipping" gets treated as a confirmed $0."""
    if FREE_SHIPPING_RE.search(text):
        return 0.0
    match = SHIPPING_RE.search(text)
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
    mailbox: str = DEFAULT_MAILBOX,
) -> list[dict]:
    """Full pipeline: IMAP fetch -> HTML extraction -> [{title, url, price}].

    Logs a warning if alert emails were found but nothing could be
    extracted from any of them -- that combination almost always means
    eBay changed their email template (or something else broke the HTML
    parse), not that eBay legitimately had nothing new to report. Without
    this, "the parser silently stopped working" and "no new listings
    today" look identical in the report/logs -- see docs/AUDIT_AND_ROADMAP.md.
    """
    messages = fetch_alert_messages(gmail_address, gmail_app_password, sender_contains, lookback_days, mailbox)
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
