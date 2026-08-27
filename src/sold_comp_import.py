"""Reads sold prices out of text you copied off a page.

THE PROBLEM THIS SOLVES. CardPro has never observed a sale, so almost every
number in it is one asking price compared to other asking prices. Sold data
is not hidden -- it is right there on eBay's Sold filter, in Terapeak, on
PSA's Auction Prices Realized, on 130point. What is missing is a legitimate
way for a program to go and get it: eBay's User Agreement forbids automated
access and Marketplace Insights is closed to new users. Building a fetcher
for any of it would be defeating a site's access controls, which this
project does not do.

What IS legitimate, and always has been, is you looking at a page and
copying it. That is a person browsing, not a robot fetching. This module
turns the resulting blob of text into structured sales, so seeding a card
costs one paste instead of one ``scripts.add_sold_comp`` invocation per
sale. That difference is the difference between the sold-comp store being
populated and staying empty.

DELIBERATELY SUSPICIOUS. These numbers are the only figures CardPro is
allowed to call market value, and a wrong sold comp is worse than no sold
comp -- it makes every listing it touches look mispriced with the engine's
full confidence behind it. So the failure mode here is "refused to guess",
never "quietly imported something wrong": the text must carry positive
evidence that it describes completed sales, lines that could be shipping or
a bid count are skipped whole, an inferred year is flagged, and nothing is
written until you have seen every row.

WHICH WAY ROUND THE ROWS ARE. Copied pages disagree about whether the sale
date comes before its price (130point's table) or after it (an eBay item
card). Rather than hard-code a guess about a page layout that nobody here
can check against the live site, ``parse_pasted_sales`` works out the
orientation from the text in front of it -- see ``_orientation`` -- and then
pairs strictly in that direction. Pairing in whichever direction happens to
be nearer, row by row, is the one thing that must not happen: in an evenly
spaced list every price sits equidistant between its own date and the next
row's, so it silently lands one row out and every sale gets its neighbour's
date.
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
SOLD_MARKERS = re.compile(
    r"\b(sold|sale\s+date|date\s+sold|price\s+realized|realized\s+price|hammer)\b", re.I
)

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
    # 08/15/2026
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "us"),
    # Aug 15 -- no year; eBay omits it for sales inside the last year. Last,
    # so a line carrying a full date is never read by this pattern instead.
    (re.compile(rf"\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}})\b(?!\s*,?\s*\d{{4}})", re.I), "md"),
]

MONTH_NUM = {
    month: number
    for number, month in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}

#: How far apart a date and its price may sit, in lines. Copied rows put the
#: title, condition, seller and shipping between them, but not much more --
#: and a wide window is exactly how one row's date reaches the next row's
#: price.
PAIR_WINDOW_LINES = 6

#: Which side of its price the date sits on. Names rather than booleans
#: because these appear in the preview the user reads before confirming.
DATE_FIRST = "date_first"
PRICE_FIRST = "price_first"


class ParsedSale(NamedTuple):
    price: float
    date: str  # YYYY-MM-DD
    year_inferred: bool  # the source omitted the year and one was assumed
    source_line: str  # the line the price came off, so a bad row is findable


class ImportRefused(Exception):
    """Raised instead of importing something we cannot vouch for."""


class _Event(NamedTuple):
    index: int  # line number
    line: str
    value: object  # float for a price; (iso, year_inferred) for a date


def parse_pasted_sales(text: str, today: Optional[date] = None) -> list[ParsedSale]:
    """Sales found in copied text, in the order they appear.

    Refuses (rather than returning nothing) when the text carries no
    evidence of being about completed sales. Silence would read as "that
    page had no sales on it" when the real problem is that the wrong page
    was pasted, and the cost of that confusion is asking prices in the sold
    store.
    """
    if not SOLD_MARKERS.search(text or ""):
        raise ImportRefused(
            "This text has no sign of being about SOLD items -- no 'Sold', "
            "'Sale date', or 'Price realized' anywhere in it.\n"
            "On eBay, tick the 'Sold items' filter before copying. Importing an "
            "active-listing page would put ASKING prices into the sold-comp store, "
            "which is the one place that must contain nothing but real sales."
        )

    today = today or date.today()
    lines = [line.strip() for line in text.splitlines()]

    prices: list[_Event] = []
    dates: list[_Event] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        found = _extract_date(line, today)
        if found is not None:
            dates.append(_Event(index, line, found))
        price = _extract_price(line)
        if price is not None:
            prices.append(_Event(index, line, price))

    orientation = _orientation(prices, dates)
    return _pair(prices, dates, orientation)


def _orientation(prices: list, dates: list) -> str:
    """Whether this text puts the date before its price or after it.

    Decided once for the whole paste, by asking each price which side its
    nearest date is on and taking the majority. A price with a date the same
    distance away on both sides -- the middle of an evenly spaced list --
    abstains, because it is exactly the case that carries no information.

    Ties and no-votes fall back to date-first: that is the tabular shape
    (130point, a spreadsheet, an auction-house results page), where the date
    is a column to the left of the price and the two are on one line, and it
    is what the original of this parser assumed.
    """
    votes = {DATE_FIRST: 0, PRICE_FIRST: 0}
    for price in prices:
        before = min(
            (price.index - d.index for d in dates if d.index <= price.index),
            default=None,
        )
        after = min(
            (d.index - price.index for d in dates if d.index >= price.index),
            default=None,
        )
        # A date on the price's own line is zero away in both directions and
        # says nothing about orientation, which is what the equality check
        # in both comprehensions above produces: before == after == 0.
        if before is not None and (after is None or before < after):
            votes[DATE_FIRST] += 1
        elif after is not None and (before is None or after < before):
            votes[PRICE_FIRST] += 1
    return PRICE_FIRST if votes[PRICE_FIRST] > votes[DATE_FIRST] else DATE_FIRST


def _pair(prices: list, dates: list, orientation: str) -> list[ParsedSale]:
    """One sale per (price, date) pair, pairing only in ``orientation``.

    Each date is claimed by at most one price, so a row whose own price was
    thrown out (a Best Offer line, two figures on one line) leaves a gap
    rather than handing its date to the next row's price.
    """
    sales = []
    claimed = set()
    for price in prices:
        best = None
        for position, candidate in enumerate(dates):
            if position in claimed:
                continue
            if orientation == DATE_FIRST:
                if candidate.index > price.index:
                    continue
                distance = price.index - candidate.index
            else:
                if candidate.index < price.index:
                    continue
                distance = candidate.index - price.index
            if distance > PAIR_WINDOW_LINES:
                continue
            if best is None or distance < best[0]:
                best = (distance, position, candidate)
        if best is None:
            continue
        _, position, candidate = best
        claimed.add(position)
        iso, inferred = candidate.value
        sales.append(
            ParsedSale(
                price=price.value,
                date=iso,
                year_inferred=inferred,
                source_line=price.line,
            )
        )
    return sales


def _extract_price(line: str) -> Optional[float]:
    """The item's sale price on this line, or None.

    Shipping, bid counts and Best Offer wording disqualify the whole line
    rather than just one number: a line reading "$344.00 +$5.99 shipping" is
    ambiguous about which figure is which, and a wrong sold price is worse
    than a missing one.
    """
    if SHIPPING_RE.search(line) or BID_RE.search(line) or OFFER_RE.search(line):
        return None
    matches = PRICE_RE.findall(line)
    if len(matches) != 1:
        return None  # zero, or several with no way to tell which is the sale
    try:
        price = float(matches[0].replace(",", ""))
    except ValueError:
        return None
    return price if price > 0 else None


def _extract_date(line: str, today: date) -> Optional[tuple]:
    """``(YYYY-MM-DD, year_was_inferred)`` for the first date on this line."""
    for pattern, shape in DATE_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        try:
            if shape == "iso":
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif shape == "mdy":
                month = MONTH_NUM[match.group(1)[:3].lower()]
                day, year = int(match.group(2)), int(match.group(3))
            elif shape == "dmy":
                day = int(match.group(1))
                month = MONTH_NUM[match.group(2)[:3].lower()]
                year = int(match.group(3))
            elif shape == "us":
                month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            else:  # "md" -- the source carried no year
                month = MONTH_NUM[match.group(1)[:3].lower()]
                day = int(match.group(2))
                # A page omits the year only for recent sales, so assume this
                # one and step back a year if that lands in the future. A
                # future date would make a stale sale look fresh, and
                # freshness is what decides whether a comp counts at all.
                year = today.year
                if date(year, month, day) > today:
                    year -= 1
                return date(year, month, day).isoformat(), True
            return date(year, month, day).isoformat(), False
        except (ValueError, KeyError):
            continue  # e.g. Feb 31 -- not a date. Keep looking on this line.
    return None
