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
never "quietly imported something wrong".

WHAT COUNTS AS A SALE. Not "a price near a date". A copied results page is
full of numbers that are not sale prices (a sponsored Buy It Now injected
into sold results, a "$1 START" in somebody's title, a current bid) and
dates that are not sale dates (an estimated delivery, an auction end time).
Pairing on line distance alone reads all of them: a sponsored active row
between two sold rows took the second row's date and put a $599 ASKING
price in the store while dropping the real $375 sale.

So a sale here is anchored on the evidence that a sale happened -- a line
that says BOTH that something sold and when. Prices attach to those
anchors, nearest first, and a price no anchor claims is discarded rather
than paired with whatever date is closest. Nothing on a page of live
listings can produce an anchor, which is what makes the refusal below
mean something.

WHICH WAY ROUND THE ROWS ARE. Copied pages disagree about whether the sale
date comes before its price (130point's table) or after it (an eBay item
card). Rather than hard-code a guess about a page layout that nobody here
can check against the live site, the orientation is worked out from the
text in front of us -- see ``_orientation`` -- and every row is then paired
strictly in that direction. Pairing in whichever direction happens to be
nearer, row by row, is the one thing that must not happen: in an evenly
spaced list every price sits equidistant between its own date and the next
row's, so it silently lands one row out and every sale gets its neighbour's
date. When the text cannot say which way round it is, this refuses.
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
#
# The lookbehind is not decoration. eBay prints "26 sold" on every active
# Buy It Now row, so a bare \bsold\b is satisfied by a page on which nothing
# has sold at all -- and that one line was the entire defence.
# \s would match the newline before a line beginning "Sold", which is the
# shape this is meant to ACCEPT -- so the lookbehind is a literal space or
# tab, matching only a counter sitting on the same line.
SOLD_MARKERS = re.compile(
    r"(?<!\d[ \t])\b(sold|sale\s+date|date\s+sold|price\s+realized|realized\s+price|hammer)\b",
    re.I,
)

# Text that means this line's number is NOT an item's sale price.
SHIPPING_RE = re.compile(r"\b(shipping|postage|delivery|freight)\b", re.I)
BID_RE = re.compile(r"\b\d+\s+bids?\b", re.I)
OFFER_RE = re.compile(r"\bbest\s+offer\b", re.I)

# Text that means this line's DATE is not a sale date. Delivery estimates
# are the dangerous one: they sit one line under the price on an active
# listing, in exactly the place a sold date sits on a sold one.
NOT_A_SALE_DATE_RE = re.compile(
    r"\b(delivery|delivers|arrives|arriving|ships?\s+(by|on)|returns?\s+(by|until|accepted)"
    r"|ends?|ending|left|expires?)\b",
    re.I,
)

# A currency symbol is not a currency. AU$560 is about US$370, and importing
# it at face value inflates the comp by half with the engine's full
# confidence behind it -- so a $ carrying any prefix other than US is not a
# number this module will read.
FOREIGN_CURRENCY_RE = re.compile(r"([A-Za-z]{1,3})\s?\$\s?[\d,]")

# The trailing lookahead is what stops "$344.5" being read as $344.00 and
# "$1.299,00" (European grouping) as $1.29: a partial match of a number is
# not a number, and a wrong sold price is worse than a skipped line.
PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)(?![\d.,])")

# Month names in full, with no trailing wildcard. "mar[a-z]*" made "Marino
# 13" a date -- and a phantom date does not merely add a bad row, it can
# outvote the real ones on orientation and re-date the entire page.
MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
DATE_PATTERNS = [
    # 2026-08-15
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    # Aug 15, 2026  /  August 15 2026
    (re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), "mdy"),
    # 15 Aug 2026
    (re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})\.?\s+(\d{{4}})\b", re.I), "dmy"),
    # 08/15/2026
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "us"),
    # Aug 15 -- no year; eBay omits it for sales inside the last year. Last,
    # so a line carrying a full date is never read by this pattern instead.
    (re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}})\b(?!\s*,?\s*\d{{4}})", re.I), "md"),
]

MONTH_NUM = {
    month: number
    for number, month in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}

#: How far apart an anchor and its price may sit, in lines. Copied rows put
#: the title, condition, seller and shipping between them, but not much more
#: -- and a wide window is exactly how one row's anchor reaches the next
#: row's price.
PAIR_WINDOW_LINES = 6

#: How much text a price may share its line with before that line stops
#: looking like a price and starts looking like a title. "$1 START NO
#: RESERVE" in a seller's title is a real string that parsed cleanly as a
#: $1.00 sale. Lines carrying the anchor itself are exempt -- there the
#: association is stated, not inferred.
MAX_PRICE_LINE_EXTRA_CHARS = 20

#: Which side of its price the sale date sits on. Names rather than booleans
#: because they appear in the message when this refuses to guess.
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
    alone: bool = True  # a price: is this line just a price, or a title too?


def parse_pasted_sales(text, today: Optional[date] = None) -> list:
    """Sales found in copied text, in the order they appear on the page.

    Refuses (rather than returning nothing) when the text carries no
    evidence of completed sales, and again when it cannot tell which way
    round the rows are. Silence would read as "that page had no sales on
    it" when the real problem is the wrong page, or the right page read
    backwards.
    """
    if not isinstance(text, str):
        raise ImportRefused(
            "There is no text to import -- got {} instead of a string.".format(type(text).__name__)
        )
    if not SOLD_MARKERS.search(text):
        raise ImportRefused(
            "This text has no sign of being about SOLD items -- no 'Sold', 'Sale date', or "
            "'Price realized' anywhere in it. (eBay's '26 sold' counter on an active listing "
            "does not count, and is not meant to.)\n"
            "On eBay, tick the 'Sold items' filter before copying. Importing an "
            "active-listing page would put ASKING prices into the sold-comp store, "
            "which is the one place that must contain nothing but real sales."
        )

    today = today or date.today()
    lines = [line.strip() for line in text.splitlines()]

    prices, dates = [], []
    for index, line in enumerate(lines):
        if not line:
            continue
        found = _extract_date(line, today)
        if found is not None and not NOT_A_SALE_DATE_RE.search(line):
            dates.append(_Event(index, line, found))
        price = _extract_price(line)
        if price is not None:
            prices.append(_Event(index, line, price, _looks_like_only_a_price(line)))

    anchors = _anchors(dates)
    return _pair(anchors, prices)


def _anchors(dates: list) -> list:
    """The lines that say a sale happened AND when.

    A page of live listings cannot produce one of these: a delivery estimate
    has a date but no sale, and a "26 sold" counter has neither. That is the
    whole defence against a sponsored Buy It Now row -- or an entire active
    search -- landing in the store, so it is preferred wherever it exists.

    The fallback, for a tabular page whose header says "Sale date" once over
    a column of bare dates, is every date line. It only ever runs when no
    line pairs the two, and the text-wide check above has already refused
    anything with no sale wording at all.
    """
    marked = [event for event in dates if SOLD_MARKERS.search(event.line)]
    return marked or dates


def _orientation(anchors: list, prices: list) -> Optional[str]:
    """Whether this page puts the sale date before its price or after it,
    or None when the text does not say.

    Decided once for the whole paste, by asking each anchor which side its
    nearest price is on and taking the majority. An anchor with a price the
    same distance away on both sides -- the middle of an evenly spaced list
    -- abstains, because that is exactly the case carrying no information.
    """
    votes = {DATE_FIRST: 0, PRICE_FIRST: 0}
    for anchor in anchors:
        after = min(
            (price.index - anchor.index for price in prices if price.index > anchor.index),
            default=None,
        )
        before = min(
            (anchor.index - price.index for price in prices if price.index < anchor.index),
            default=None,
        )
        if after is not None and (before is None or after < before):
            votes[DATE_FIRST] += 1
        elif before is not None and (after is None or before < after):
            votes[PRICE_FIRST] += 1
    if votes[DATE_FIRST] == votes[PRICE_FIRST]:
        return None
    return DATE_FIRST if votes[DATE_FIRST] > votes[PRICE_FIRST] else PRICE_FIRST


def _pair(anchors: list, prices: list) -> list:
    """One sale per anchor that can be given a price it is entitled to.

    Same-line pairs are settled first and separately: "Price realized:
    $1,250.00 on 2026-08-01" states the association rather than implying it,
    so it needs no orientation and votes on none.

    The rest are assigned nearest-pair-first across the whole page, not
    anchor by anchor in order. Greedy in page order lets an early anchor
    whose own price was thrown out (a Best Offer line) reach four lines down
    and take the next row's price, which is the mis-dating this whole module
    is built to avoid.
    """
    taken_prices, taken_anchors = set(), set()
    found = {}  # anchor position -> price event

    for position, anchor in enumerate(anchors):
        for index, price in enumerate(prices):
            if index in taken_prices or price.index != anchor.index:
                continue
            found[position] = price
            taken_prices.add(index)
            taken_anchors.add(position)
            break

    pending = [(position, anchor) for position, anchor in enumerate(anchors)
               if position not in taken_anchors]
    available = [(index, price) for index, price in enumerate(prices) if index not in taken_prices]

    if pending and available:
        orientation = _orientation([anchor for _, anchor in pending],
                                   [price for _, price in available])
        if orientation is None:
            raise ImportRefused(
                "Cannot tell whether this page prints the sale date above its price or "
                "below it -- every row sits the same distance from both, so pairing them "
                "either way would be a coin toss, and half of it would file each sale "
                "under its neighbour's date.\n"
                "Copy the results including the listing titles (that is what separates one "
                "row from the next), or enter these one at a time without --paste."
            )
        candidates = []
        for position, anchor in pending:
            for index, price in available:
                if orientation == DATE_FIRST:
                    if price.index <= anchor.index:
                        continue
                    distance = price.index - anchor.index
                else:
                    if price.index >= anchor.index:
                        continue
                    distance = anchor.index - price.index
                if distance > PAIR_WINDOW_LINES or not price.alone:
                    continue
                candidates.append((distance, position, index))
        for _, position, index in sorted(candidates):
            if position in taken_anchors or index in taken_prices:
                continue
            found[position] = prices[index]
            taken_anchors.add(position)
            taken_prices.add(index)

    sales = []
    for position, anchor in enumerate(anchors):
        price = found.get(position)
        if price is None:
            continue
        iso, inferred = anchor.value
        sales.append(
            ParsedSale(price=price.value, date=iso, year_inferred=inferred,
                       source_line=price.line)
        )
    return sales


def _looks_like_only_a_price(line: str) -> bool:
    """Whether this line is a price rather than a title with a price in it.

    "2024 Prizm Caleb Williams Silver $1 START NO RESERVE PSA 10" parsed as
    a clean $1.00 sale. A price standing on its own line is the shape every
    results page uses for the figure that matters; a price buried in a
    sentence is somebody's marketing.
    """
    match = PRICE_RE.search(line)
    if match is None:
        return False
    rest = (line[: match.start()] + line[match.end():]).strip(" \t$.,:-|")
    return len(rest) <= MAX_PRICE_LINE_EXTRA_CHARS


def _extract_price(line: str) -> Optional[float]:
    """The item's sale price on this line, or None.

    Shipping, bid counts and Best Offer wording disqualify the whole line
    rather than just one number: a line reading "$344.00 +$5.99 shipping" is
    ambiguous about which figure is which, and a wrong sold price is worse
    than a missing one.
    """
    if SHIPPING_RE.search(line) or BID_RE.search(line) or OFFER_RE.search(line):
        return None
    foreign = FOREIGN_CURRENCY_RE.search(line)
    if foreign is not None and foreign.group(1).upper() != "US":
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
