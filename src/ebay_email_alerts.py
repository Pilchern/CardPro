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

TRUNCATED TITLES: eBay truncates long titles in these emails with an
ellipsis, which can cut a grade number mid-digit -- a "PSA 10" arriving as
"PSA 1…", which parses as PSA 1. CardPro does NOT fetch the item page to
resolve that. Doing so would mean automated access to eBay's site, which
their User Agreement prohibits and which principle #1 in
docs/PROJECT_STATUS.md rules out outright ("never build tooling to defeat a
site's anti-bot measures") -- the same reason this project refuses to
scrape Craigslist. There is no clever version of that request; there is
only the request.

So the grade on a truncated title is simply unknown. looks_truncated()
flags it, main.mark_truncated_titles() sets `title_truncated`, the report
surfaces it as a risk ("eBay truncated the title, so the grade shown may
not be the real grade"), and a truncated listing that parsed a grade is
rejected with GRADE_UNCERTAIN rather than valued against comps for a grade
it may not have. Uncertain beats confidently wrong.

VALIDATED (2026-08-18): extract_listings_from_html() was confirmed working
against 14 real alert emails / 327 real extracted listings / 96 correctly
matched watchlist listings, with no observed player-name collisions. If
eBay changes their email template later, re-run
`python -m scripts.test_ebay_alerts --raw` to re-validate.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from email.message import Message
from functools import lru_cache
from typing import Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

#: Socket timeout for every IMAP operation. Without one the socket blocks
#: forever: a server that accepts the connection and then stalls mid-FETCH
#: raises nothing, so main's failure-notification handler never fires and the
#: job hangs until GitHub's six-hour cap kills it. That is the only path in
#: the system that produces no report AND no failure email -- the one place
#: "never go silent" genuinely did not hold.
IMAP_TIMEOUT_SECONDS = 60


class AlertFetchFailed(Exception):
    """The mailbox could not be read, as distinct from having nothing in it.

    The two used to be the same value -- an empty list -- which is how a
    server error became a report that said "nothing new today" with full
    confidence. Raising means the failure-notification path handles it.
    """

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
# eBay serves the same item under several link shapes -- bare
# (/itm/336749665825), SEO ("/itm/2024-Panini-Prizm-Caleb-Williams-RC-301/
# 336749665825"), with a ?hash= tail, off m.ebay.com or ebay.co.uk, and
# wrapped in a rover redirect. The identity that survives all of that is the
# item NUMBER, so that -- not the link text -- is what CardPro keys on.
#
# Everything that asks "is this anchor an item link, and which item?" goes
# through _item_number, so scoping one listing's text away from its
# neighbour's compares item numbers rather than link shapes. A regex that
# only answered "some item" used to serve that purpose, and could not tell a
# second link to THIS item from the next listing's.
#
# The number itself. Required to be a whole path segment of 9+ digits so a
# slug can never masquerade as one: "/itm/2024-Panini-.../336749665825" used
# to yield the "2024" inside the slug, which is a dead link AND collapses two
# different cards onto one dedupe key.
ITEM_NUMBER_RE = re.compile(r"""/itm/(?:[^/?#\s"'<>]+/)?(\d{9,})(?![^/?#\s"'<>])""")

# Item numbers shorter than that predate eBay's current numbering and only
# ever appear bare, with no slug to be confused with -- read them rather than
# dropping a listing whose link is perfectly good.
SHORT_ITEM_NUMBER_RE = re.compile(r"""/itm/(\d+)(?![^/?#\s"'<>])""")

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


def fetch_alert_messages(
    gmail_address: str,
    gmail_app_password: str,
    sender_contains: str,
    lookback_days: int,
    mailbox: str = DEFAULT_MAILBOX,
    counters: Optional[dict] = None,
) -> list[Message]:
    """Logs into Gmail read-only over IMAP and returns parsed Message
    objects for recent emails from `sender_contains`. Never deletes,
    marks read, or modifies anything -- opens `mailbox` readonly, which
    defaults to All Mail rather than INBOX specifically so archiving (or a
    filter that skips the inbox) doesn't make alert emails invisible here
    -- see DEFAULT_MAILBOX.
    """
    messages: list[Message] = []
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS) as imap:
        imap.login(gmail_address, gmail_app_password)
        # imaplib doesn't quote the mailbox name itself -- a name containing
        # a space (like the default "[Gmail]/All Mail") gets sent to the
        # server unquoted and rejected outright ("BAD Could not parse
        # command"). Quoting is always valid IMAP syntax, whether or not the
        # name has special characters, so just always do it.
        imap.select(f'"{mailbox}"', readonly=True)

        since = _imap_date(lookback_days)
        status, data = imap.search(None, f'(SINCE {since} FROM "{sender_contains}")')
        # A NO/BAD from SEARCH is a server error, and it used to return an
        # empty list -- which the report then rendered as "Emails scanned: 0,
        # Listings parsed: 0", internally consistent and indistinguishable
        # from a genuinely quiet day, directly above prose telling you the
        # counts are the proof it looked. Raise instead: the failure email is
        # the honest output here.
        if status != "OK":
            raise AlertFetchFailed(
                "IMAP SEARCH returned {} rather than OK. Treating this as an empty "
                "inbox would report a quiet day that never happened.".format(status)
            )
        if not data or not data[0]:
            return messages

        for num in data[0].split():
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                # One bad message out of twenty is not worth losing the run
                # over, but it must not vanish either: a silently skipped
                # message shrinks the "scanned" count with nothing to explain
                # the gap. Counted, and surfaced in the health footer.
                if counters is not None:
                    counters["fetch_failures"] = counters.get("fetch_failures", 0) + 1
                logger.warning("IMAP FETCH of message %s returned %s -- skipping it", num, status)
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


def extract_listings_from_html(html: str, counters: Optional[dict] = None) -> list[dict]:
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
    #: item number -> the listing built so far. An email links the same item
    #: more than once (the photo and the title are separate anchors, often in
    #: separate cells), and each anchor sees a different part of the row, so
    #: they are merged into one listing rather than the first one winning.
    listings = OrderedDict()
    refused_by_item = {}

    for a in soup.find_all("a", href=True):
        item_number = _item_number(a["href"])
        if item_number is None:
            continue

        title, fuller_refused, title_verified = _fullest_title(a)

        price = _find_nearby(a, _extract_price, item_number)
        shipping_price = _find_nearby(a, _extract_shipping, item_number)
        context = _nearby_text(a, item_number)
        listing_type, bid_count = _detect_listing_type(context)

        found = {
            "title": title,
            "url": _item_url(item_number),
            "price": price,
            "shipping_price": shipping_price,
            "listing_type": listing_type,
            "bid_count": bid_count,
            "has_best_offer": bool(BEST_OFFER_RE.search(context)),
            "time_left_text": _extract_time_left(context),
            "title_verified": title_verified,
        }

        existing = listings.get(item_number)
        if existing is None:
            if not title:
                # No title from this anchor and nothing to merge it into --
                # a bare photo link with no alt text says nothing about the
                # card. A later anchor for the same item may still carry one.
                continue
            listings[item_number] = found
            refused_by_item[item_number] = fuller_refused
        elif _merge_listing(existing, found):
            # This anchor's title is the one we kept, so its near-miss is the
            # one still costing us something -- and whatever the earlier
            # anchor refused, it no longer has the title we are reporting.
            refused_by_item[item_number] = fuller_refused

    results = list(listings.values())
    for listing in results:
        # Bookkeeping for the merge, not a fact about the listing.
        listing.pop("title_verified", None)

    if counters is not None and results:
        counters["titles_seen"] = counters.get("titles_seen", 0) + len(results)
        counters["titles_truncated"] = counters.get("titles_truncated", 0) + sum(
            1 for listing in results if looks_truncated(listing["title"])
        )
        # Without this, a report still saying "98% truncated" cannot be read:
        # it could mean eBay's HTML simply carries no fuller copy of the
        # title (a real ceiling, nothing to fix here), or that a fuller copy
        # was sitting right there and our own stem check threw it away (our
        # bug, and fixable). Those two need different work, so they get
        # counted apart rather than argued about.
        counters["titles_recovery_refused"] = counters.get("titles_recovery_refused", 0) + sum(
            1 for number in listings if refused_by_item.get(number)
        )

    return results


#: What a second anchor for the same item may fill in. Every one of these is
#: "unknown until something says otherwise", so the first real answer wins
#: and a later anchor can never overwrite it with another unknown.
_FILLABLE_FIELDS = ("price", "shipping_price", "bid_count", "time_left_text")


def _merge_listing(existing: dict, found: dict) -> bool:
    """Fold a second anchor for the same item into the listing already built.

    eBay's alert template gives one item several links -- the photo, the
    title, sometimes a "Buy It Now" button -- and they do not see the same
    thing. The photo anchor usually carries the untruncated title in its alt
    text and sits in its own table cell with no price in it; the title anchor
    sits next to the price. Keeping only the first anchor threw away whatever
    the other one knew, which is how every listing in a live run arrived with
    a full title and no price at all.

    Returns whether the title got fuller here, so the caller can stop
    counting a refused recovery that a later anchor made good.
    """
    title_improved = False
    if _title_wins(existing, found):
        existing["title"] = found["title"]
        existing["title_verified"] = existing["title_verified"] or found["title_verified"]
        title_improved = True
    elif found["title_verified"] and existing["title_verified"] is False:
        # The title we are keeping was read off a photo link with no visible
        # text, and this anchor has now supplied that text and agrees with
        # it. That is the check _fullest_title could not run, run late.
        existing["title_verified"] = True

    for field in _FILLABLE_FIELDS:
        if existing.get(field) is None and found.get(field) is not None:
            existing[field] = found[field]

    if existing["listing_type"] == LISTING_TYPE_UNKNOWN:
        existing["listing_type"] = found["listing_type"]
    elif found["listing_type"] == LISTING_TYPE_AUCTION:
        # Bid evidence anywhere in the row wins, for the reason given in
        # _detect_listing_type: calling a current bid an asking price is the
        # expensive mistake, so it is the one this resolves against.
        existing["listing_type"] = LISTING_TYPE_AUCTION

    existing["has_best_offer"] = existing["has_best_offer"] or found["has_best_offer"]
    return title_improved


def _title_wins(existing: dict, found: dict) -> bool:
    """Whether this anchor's title should replace the one already kept.

    The same rule _fullest_title applies within one anchor, applied across
    two anchors for one item: longer is not enough on its own, because a
    "See more from this seller" link would win on length alone. It has to
    contain the part of the title we can be sure of.

    With one addition the single-anchor version cannot make. A photo link
    has no visible text, so its alt was accepted with nothing to check it
    against -- and "Shop eBay for great deals on trading cards" is longer
    than most real titles. When a sibling link finally supplies visible
    text, an unchecked title that does not contain it was never this card's
    title, however long it is, and loses to the one that was displayed.
    """
    current, candidate = existing["title"], found["title"]
    if not candidate:
        return False
    if not current:
        return True
    if found["title_verified"] and not existing["title_verified"]:
        return _title_stem(candidate) not in current.lower()
    return len(candidate) > len(current) and _title_stem(current) in candidate.lower()


#: Where a fuller copy of the title may be hiding, in the order we prefer
#: them. The anchor's visible text is what eBay TRUNCATED for display; these
#: attributes are written for tooltips and screen readers and are often the
#: whole thing.
_TITLE_ATTRIBUTES = ("title", "aria-label")


def _title_stem(text: str) -> str:
    """The part of a truncated title we can be sure of.

    eBay cuts mid-word and appends an ellipsis, so "2024 Panini Caleb
    Williams Pr..." tells us the title starts with everything before "Pr".
    That stem is what a longer candidate has to match to be believable.
    """
    stripped = text.rstrip()
    for marker in TRUNCATION_MARKERS:
        if stripped.endswith(marker):
            stripped = stripped[: -len(marker)].rstrip()
            # Drop the partial word the cut left behind.
            head, _, tail = stripped.rpartition(" ")
            return (head or tail).strip().lower()
    return stripped.strip().lower()


def _fullest_title(anchor) -> tuple[str, bool, bool]:
    """The longest title for this listing that the email actually contains,
    whether a longer candidate was refused for not matching it, and whether
    any of it is anchored to text eBay actually displayed for this listing.

    THIS IS THE BOTTLENECK, measured. Of the first 350 titles the live run
    stored, 98% arrived truncated, at a median of 30 characters -- "2024
    Panini Caleb Williams Pr...", cut off mid-word before the set name. The
    set, the parallel, the card number and the grade all live past that cut,
    which is the whole reason set_name resolved for a sixth of listings and
    the flagship-set path (which needs a card number) almost never fires. It
    is not that the parser is weak; it is that it was being handed thirty
    characters.

    eBay truncates the VISIBLE link text. The same anchor's title attribute,
    its aria-label, and the item image's alt text are written for tooltips
    and screen readers and are frequently the untruncated title. Reading them
    is parsing an email we were sent -- no request to eBay, nothing that
    touches their anti-automation measures.

    A candidate is only accepted when it is longer than the visible text AND
    begins with the part of it we can be sure of. Without that check a
    generic alt like "eBay" or a seller's store name would silently replace a
    real title, which is worse than a short one.

    The second return value is True when that check was the only thing
    standing between us and a longer title. It is the difference between
    "eBay sent no fuller copy" and "eBay sent one and we refused it", which
    a truncation rate on its own cannot tell you -- see the counters in
    extract_listings_from_html.

    The third says whether the anchor had any visible text at all. A photo
    link has none, so there is no stem to check its alt against and whatever
    the alt says is taken on trust -- which is fine when the alt is the card
    title and wrong when it is "Shop eBay for great deals". Nothing here can
    tell those apart; _merge_listing can, once a sibling link supplies the
    visible text this one lacked, so it needs to know which titles were
    never checked.
    """
    # The separator is load-bearing: these are HTML emails, so a title is
    # routinely split across sibling <span>s or table cells, and joining the
    # child nodes with nothing welds two words together ("Panini PrizmCaleb
    # Williams"). That fused pair appears in no real title, so the stem built
    # from it matched nothing and every recovery was silently refused.
    visible = " ".join(anchor.get_text(" ", strip=True).split())
    stem = _title_stem(visible) if visible else ""

    candidates = [anchor.get(name) for name in _TITLE_ATTRIBUTES]
    for image in anchor.find_all("img"):
        candidates.append(image.get("alt"))
        candidates.append(image.get("title"))

    best = visible
    refused = False
    for candidate in candidates:
        if not candidate:
            continue
        candidate = " ".join(str(candidate).split())
        if len(candidate) <= len(best):
            continue
        # startswith is the common case -- eBay simply cuts the string --
        # but "contains" is accepted too, because a template that prefixes
        # the visible text (or the attribute) with something like "New
        # listing" would otherwise throw the real title away. The stem is
        # twenty-odd characters of this specific listing either way, so it
        # still cannot match a generic alt like "Shop eBay for great deals".
        if stem and stem not in candidate.lower():
            refused = True
            continue
        best = candidate
    # A refusal only counts while it is still costing us something: if a
    # later candidate got through, nothing was lost and reporting it as a
    # near-miss would send tomorrow's reader after a title we already have.
    return best, refused and best == visible, bool(visible)


@lru_cache(maxsize=8192)
def _item_number(href: str) -> Optional[str]:
    """The eBay item number in this href, or None.

    Tries the URL as written and then URL-decoded, because marketing and
    redirect links often wrap the real destination as a percent-encoded query
    param (e.g. ...?mpre=https%3A%2F%2Fwww.ebay.com%2Fitm%2F123456789012),
    which won't match literally without decoding first.
    """
    for text in (href, unquote(href)):
        match = ITEM_NUMBER_RE.search(text) or SHORT_ITEM_NUMBER_RE.search(text)
        if match:
            return match.group(1)
    return None


def _item_url(number: str) -> str:
    """The canonical URL for an item number. One place, because this string
    is the listing's id -- the dedupe key in seen_listings.json and the key
    the comp corpus stores observations under -- and two spellings of it
    would be two listings."""
    return "https://www.ebay.com/itm/" + number


def _find_item_url(href: str) -> Optional[str]:
    """The canonical https://www.ebay.com/itm/<number> URL for this link.

    Rebuilt from the item number rather than returned as found, because this
    string is the listing's id: the dedupe key in seen_listings.json and the
    key the comp corpus stores observations under. The same item reached via
    http://, m.ebay.com, bare ebay.com, ebay.co.uk or a rover wrapper is the
    same item, and returning the link as written made each of those a
    separate id -- so a listing already reported came back as new, and its
    price history forked into a row per link shape. Host and scheme carry no
    identity, so they are not part of it.

    The bare form is what eBay's own alert emails and every already-stored id
    use, so this is byte-identical to the old behaviour for those and only
    changes the shapes that were wrong.
    """
    number = _item_number(href)
    if number is None:
        return None
    return _item_url(number)


def _links_another_listing(tag, item_number: str) -> bool:
    """Whether `tag` is, or contains, a link to a DIFFERENT eBay item.

    The distinction matters more than it looks. An email row links the same
    item several times -- the photo, the title, a "Buy It Now" button -- and
    those are all this listing. Treating any item link as a boundary meant a
    listing's own second link cut it off from its own price, which is exactly
    what happened once photo anchors started being read: every listing in a
    live run came out with a full title and price None.

    Compared by item NUMBER, not by link shape, and read with the same
    decoding as _find_item_url, so a rover-wrapped copy of this item's link
    is recognised as this item rather than mistaken for the next one.
    """
    if getattr(tag, "find_all", None) is None:
        return False
    if tag.name == "a" and tag.get("href") and _is_other_item(tag["href"], item_number):
        return True
    return any(_is_other_item(a["href"], item_number) for a in tag.find_all("a", href=True))


def _is_other_item(href: str, item_number: str) -> bool:
    other = _item_number(href)
    return other is not None and other != item_number


def _scoped_texts(anchor_tag, item_number: str):
    """Every chunk of text CardPro may read for THIS listing, nearest first.

    Deliberately scoped narrowly so it doesn't pick up a neighbouring
    listing's value when several listings share a flat structure with no
    per-listing wrapper (title link, then a price/shipping element as its
    sibling, repeated -- a common real-world email-template pattern).

    Shared by _find_nearby and _nearby_text so a price and a bid count can
    never be read from different listings.
    """
    # 1. the link's own text
    yield anchor_tag.get_text(" ", strip=True)

    # 2. next siblings at the same level, stopping at the next LISTING's
    #    item link (another link to this same item is still this listing)
    for sibling in anchor_tag.find_next_siblings():
        if _links_another_listing(sibling, item_number):
            break
        yield sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling)

    # 3. fall back to the narrowest ancestor that doesn't itself contain
    #    another listing's item link (too broad = wrong listing's value)
    for ancestor in anchor_tag.parents:
        if any(
            a is not anchor_tag and _is_other_item(a["href"], item_number)
            for a in ancestor.find_all("a", href=True)
        ):
            break
        yield ancestor.get_text(" ", strip=True)


def _find_nearby(anchor_tag, extractor, item_number: str) -> Optional[float]:
    """The first value (price or shipping cost -- whatever `extractor` pulls
    out of text) found near this listing's link, nearest chunk first."""
    for text in _scoped_texts(anchor_tag, item_number):
        value = extractor(text)
        if value is not None:
            return value
    return None


def _nearby_text(anchor_tag, item_number: str) -> str:
    """The text CardPro is allowed to read for THIS listing, joined. Same
    scoping as _find_nearby -- reused rather than reinvented so listing-type
    detection can never pick up the neighbouring listing's "3 bids" and
    mislabel a Buy It Now as an auction.
    """
    return " ".join(part for part in _scoped_texts(anchor_tag, item_number) if part)


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
    """True if eBay cut this title short. That is the end of what CardPro
    can know about it -- there is no recovery step, because recovering the
    full title would mean fetching the item page (see the module docstring).
    A truncated title means an unreliable grade, nothing more and nothing
    less, and callers treat it as such.
    """
    return title.rstrip().endswith(TRUNCATION_MARKERS)


def fetch_alert_listings(
    gmail_address: str,
    gmail_app_password: str,
    sender_contains: str,
    lookback_days: int,
    mailbox: str = DEFAULT_MAILBOX,
    counters: Optional[dict] = None,
) -> list[dict]:
    """Full pipeline: IMAP fetch -> HTML extraction -> [{title, url, price}].

    `counters`, when given, gets `counters["messages"]` set to how many alert
    emails were actually read. The daily report's data-quality footer needs
    that number to distinguish "eBay sent nothing" from "we read 14 emails
    and got nothing out of them" -- the same distinction the template-change
    canary below exists for, surfaced in the email rather than only the log.

    Logs a warning if alert emails were found but nothing could be
    extracted from any of them -- that combination almost always means
    eBay changed their email template (or something else broke the HTML
    parse), not that eBay legitimately had nothing new to report. Without
    this, "the parser silently stopped working" and "no new listings
    today" look identical in the report/logs -- see docs/AUDIT_AND_ROADMAP.md.
    """
    messages = fetch_alert_messages(
        gmail_address, gmail_app_password, sender_contains, lookback_days, mailbox, counters=counters
    )
    if counters is not None:
        counters["messages"] = len(messages)
    listings = []
    for msg in messages:
        html = get_html_body(msg)
        if not html:
            continue
        listings.extend(extract_listings_from_html(html, counters=counters))

    if messages and not listings:
        # Recorded for the caller, not only logged. This is the single
        # highest-value alarm in the system -- eBay changed their template
        # and every downstream number is now a fabricated quiet day -- and
        # until now it went exclusively to a log file on an ephemeral runner.
        if counters is not None:
            counters["template_warning"] = (
                "Read {} eBay alert email(s) and extracted 0 listings from all of them. "
                "That combination almost always means eBay changed their email template "
                "and extract_listings_from_html needs updating -- not that there was "
                "nothing new. Run `python -m scripts.test_ebay_alerts --raw` to check."
            ).format(len(messages))
        logger.warning(
            "Found %d eBay alert email(s) but extracted 0 listings from any of them -- "
            "this usually means eBay changed their email template and extract_listings_from_html "
            "needs updating, not that there were legitimately no new listings. "
            "Re-run `python -m scripts.test_ebay_alerts --raw` to check.",
            len(messages),
        )
    return listings
