"""Parses sold prices out of text you copied from a page.

THE PROBLEM THIS SOLVES. CardPro has never observed a sale, so every
automated number in it is one asking price compared to other asking prices.
Sold data is not hidden -- it is right there on eBay's Sold filter, in
Terapeak, on PSA's Auction Prices Realized, on 130point. What is missing is
a legitimate way for a program to go get it: eBay's User Agreement forbids
automated access, Marketplace Insights is closed to new users, and Terapeak
has no export. See docs/AUDIT_2026-08.md section 6.

What IS legitimate, and always has been, is you looking at a page and
copying it. That is a person browsing, not a robot fetching. This module
turns the resulting blob of text into structured sales, so seeding a card
costs one copy-paste instead of seven typed flags per sale. That difference
decides whether the sold-comp store gets populated at all.

DELIBERATELY SUSPICIOUS. These numbers become the only figures CardPro is
allowed to call market value, so the parser's failure mode must be
"refused to guess", never "quietly imported something wrong". It requires
positive evidence that the text describes completed sales, skips shipping
and bid-count numbers, flags any year it had to infer, and shows you
everything it found before writing a single line.
"""
from __future__ import annotations

import re
from datetime import date
from typing import NamedTuple, Optional

# Positive evidence that this text describes SOLD items rather than active
# listings. Without one of these the import is refused outright: pasting an
# active-search page would put asking prices into the one store that is
# supposed to hold nothing but real sales, which is the single worst thing
# that could happen to this project's data.
SOLD_MARKERS = re.compile(r"\b(sold|sale\s+date|date\s+sold|price\s+realized|realized\s+price|hammer)\b", re.I)

# Text that means this line's number is NOT an item's sale price.
SHIPPING_RE = re.compile(r"\b(shipping|postage|delivery|freight)\b", re.I)
BID_RE = re.compile(r"\b\d+\s+bids?\b", re.I)
OFFER_RE = re.compile(r"\bbest\s+offer\b", re.I)

PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_PATTERNS = [
    # 2026-08-15
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    # Aug 15, 2026  /  August 15 2026
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), "mdy"),
    # 15 Aug 2026
    (re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})[a-z]*\.?\s+(\d{{4}})\b", re.I), "dmy"),
    # Aug 15  -- no year; eBay omits it for the current year
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}})\b(?!\s*,?\s*\d{{4}})", re.I), "md"),
    # 08/15/2026
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "us"),
]

MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# How far apart a date and its price may sit. Copied pages interleave the
# title, condition and seller between them, but not by much -- a wide window
# starts pairing one listing's date with the next listing's price.
PAIR_WINDOW_LINES = 6


class ParsedSale(NamedTuple):
    price: float
    date: str  # YYYY-MM-DD
    year_inferred: bool  # the source omitted the year and we assumed one
    source_line: str


class ImportRefused(Exception):
    """Raised instead of importing something we can't vouch for."""


def parse_pasted_sales(text: str, today: Optional[date] = None) -> list[ParsedSale]:
    """Sales found in copied text, oldest line first.

    Refuses (rather than returning nothing) when the text carries no
    evidence of being about completed sales -- silence would look like
    "that page had no sales on it" when the real problem is that you pasted
    the wrong page.
    """
    if not SOLD_MARKERS.search(text):
        raise ImportRefused(
            "This text has no sign of being about SOLD items -- no 'Sold', "
            "'Sale date', or 'Price realized' anywhere in it.\n"
            "On eBay, tick the 'Sold items' filter before copying. Importing an "
            "active-listing page would put ASKING prices into the sold-comp store, "
            "which is the one place that must contain nothing but real sales."
        )

    today = today or date.today()
    lines = [ln.strip() for ln in text.splitlines()]

    pending_date: Optional[tuple[str, bool, int]] = None  # (iso, inferred, line index)
    sales: list[ParsedSale] = []

    for index, line in enumerate(lines):
        if not line:
            continue

        found = _extract_date(line, today)
        if found:
            pending_date = (found[0], found[1], index)

        price = _extract_price(line)
        if price is not None and pending_date and index - pending_date[2] <= PAIR_WINDOW_LINES:
            iso, inferred, _ = pending_date
            sales.append(ParsedSale(price=price, date=iso, year_inferred=inferred, source_line=line))
            pending_date = None  # one price per date; don't pair a shipping line too

    return sales


def _extract_price(line: str) -> Optional[float]:
    """The item's sale price on this line, or None.

    Shipping, bid counts and Best Offer wording disqualify the whole line
    rather than just one number: a line reading "$344.00 +$5.99 shipping"
    is ambiguous about which figure is which, and a wrong sold price is
    worse than a missing one.
    """
    if SHIPPING_RE.search(line) or BID_RE.search(line) or OFFER_RE.search(line):
        return None
    matches = PRICE_RE.findall(line)
    if len(matches) != 1:
        return None  # zero, or several and no way to tell which is the sale
    try:
        return float(matches[0].replace(",", ""))
    except ValueError:
        return None


def _extract_date(line: str, today: date) -> Optional[tuple[str, bool]]:
    """(YYYY-MM-DD, year_was_inferred) for the first date on this line."""
    for pattern, shape in DATE_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        try:
            if shape == "iso":
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif shape == "mdy":
                m, d, y = MONTH_NUM[match.group(1)[:3].lower()], int(match.group(2)), int(match.group(3))
            elif shape == "dmy":
                d, m, y = int(match.group(1)), MONTH_NUM[match.group(2)[:3].lower()], int(match.group(3))
            elif shape == "us":
                m, d, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
            else:  # "md" -- no year in the source
                m, d = MONTH_NUM[match.group(1)[:3].lower()], int(match.group(2))
                # eBay omits the year only for recent sales, so assume this
                # year and step back one if that lands in the future.
                y = today.year
                if date(y, m, d) > today:
                    y -= 1
                return date(y, m, d).isoformat(), True
            return date(y, m, d).isoformat(), False
        except (ValueError, KeyError):
            continue  # e.g. Feb 31 -- not a date, keep looking
    return None
