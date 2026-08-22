"""The one canonical vocabulary for *why a listing did not become a
reported opportunity* (or why it was downgraded).

Why this module exists: today the pipeline silently drops ~21% of the
listings it looks at -- no flag, no log, no count, no explanation (see
docs/CARDPRO_2_AUDIT.md, Failure Mode #9). Silence is the worst possible
answer, because the listings that vanish are disproportionately the rare
and interesting ones: the cards with no comparable history are exactly
the cards you would most want to look at yourself. "I found nothing" and
"I threw 60 listings away without telling you" look identical from the
outside, and only one of them is honest.

So: every listing must exit the pipeline carrying one of these reasons.

Design tradeoffs made here, deliberately:

* **Strings, not an Enum.** These values get counted, grouped, written
  into JSON state files and read back by a future SQLite schema. A plain
  lowercase snake_case string survives all of that round-trip with no
  serialization layer and no import of this module by the reader. The
  cost is that a typo is not a syntax error -- which is why
  ``RejectionLog.record`` *raises* on an unknown reason (see below).
* **Unknown reasons raise.** A silently-accepted typo'd reason would
  recreate the exact bug this module exists to kill, except harder to
  see, because the count would look plausible. Loud failure in a
  personal tool with 168 tests is cheaper than a quiet wrong number in
  the morning email.
* **No imports from the rest of the package.** This is a vocabulary, not
  a pipeline stage. It takes strings and gives back strings, so it can
  be imported from anywhere without a cycle and tested with no fixtures.

Nothing in here decides anything. Choosing *which* reason applies is the
caller's job; this module only guarantees the name is real, has a
human-readable label, and belongs to a category.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple


class Reason:
    """String constants for every reason a listing can fail to be reported.

    Use ``Reason.NO_COMP_AT_ANY_LEVEL`` rather than the literal string so
    that a typo is caught at import time by your editor, not at runtime
    by ``RejectionLog``. The *values* are the stable contract (they get
    persisted); the attribute names are just ergonomics.
    """

    # -- identity: we do not know what card this is -----------------------
    NO_PLAYER_MATCH = "no_player_match"
    MULTI_PLAYER_CARD = "multi_player_card"
    IDENTITY_UNCERTAIN = "identity_uncertain"
    GRADE_UNCERTAIN = "grade_uncertain"

    # -- data quality: we know the card, but not its value ----------------
    NO_PRICE = "no_price"
    NO_COMP_AT_ANY_LEVEL = "no_comp_at_any_level"
    THIN_SAMPLE = "thin_sample"
    STALE_COMPS = "stale_comps"
    DISPERSED_COMPS = "dispersed_comps"
    CONTEXT_ONLY_LEVEL = "context_only_level"
    SHIPPING_UNKNOWN = "shipping_unknown"

    # -- economics: we know the card and its value, the math says no ------
    BELOW_DISCOUNT_THRESHOLD = "below_discount_threshold"
    BELOW_MIN_SAVINGS = "below_min_savings"

    # -- auction: a bid is not a price ------------------------------------
    AUCTION_CURRENT_BID_NOT_A_PRICE = "auction_current_bid_not_a_price"
    AUCTION_BID_EXCEEDS_MAX_RATIONAL_BID = "auction_bid_exceeds_max_rational_bid"

    # -- policy: things we refuse to call a deal, whatever the math says --
    LOT = "lot"
    REPRINT = "reprint"
    REPLICA = "replica"
    CUSTOM_CARD = "custom_card"
    DIGITAL_CARD = "digital_card"
    FACSIMILE_AUTO = "facsimile_auto"
    SEALED_PRODUCT = "sealed_product"
    BREAK_SLOT = "break_slot"
    PICK_YOUR_CARD = "pick_your_card"
    DAMAGED_CONDITION = "damaged_condition"
    SELLER_RISK = "seller_risk"
    COMMON_CARD = "common_card"

    # -- dedupe: real deal, but you have already seen it ------------------
    ALREADY_REPORTED = "already_reported"
    PRICE_NOT_DROPPED = "price_not_dropped"


# Categories exist for the report's data-quality footer: "we looked at 280
# listings; 58 had no usable comps, 12 were reprints, 9 were duplicates of
# yesterday" is a different sentence than a flat list of 28 counters.
CATEGORY_IDENTITY = "identity"
CATEGORY_DATA_QUALITY = "data_quality"
CATEGORY_ECONOMICS = "economics"
CATEGORY_AUCTION = "auction"
CATEGORY_POLICY = "policy"
CATEGORY_DEDUPE = "dedupe"

ALL_CATEGORIES: Tuple[str, ...] = (
    CATEGORY_IDENTITY,
    CATEGORY_DATA_QUALITY,
    CATEGORY_ECONOMICS,
    CATEGORY_AUCTION,
    CATEGORY_POLICY,
    CATEGORY_DEDUPE,
)


# Written to be read at 7am by a person holding coffee, not by a developer
# reading a stack trace. Each one says what was measured, not what we
# suspect: "comps are stale" is a fact about the corpus; "probably not
# worth it" would be an opinion, and opinions do not belong in a counter.
REASON_LABELS: Dict[str, str] = {
    # identity
    Reason.NO_PLAYER_MATCH: "no watchlist player found in the title",
    Reason.MULTI_PLAYER_CARD: "a multi-player card -- it has no single player's market",
    Reason.IDENTITY_UNCERTAIN: "could not identify the card confidently enough to value it",
    Reason.GRADE_UNCERTAIN: "the grade is uncertain (title truncated or unreadable)",
    # data quality
    Reason.NO_PRICE: "no price could be read from the listing",
    Reason.NO_COMP_AT_ANY_LEVEL: "no comparable sales at any level",
    Reason.THIN_SAMPLE: "too few comparable sales to trust a median",
    Reason.STALE_COMPS: "comps are stale (newest is older than the freshness window)",
    Reason.DISPERSED_COMPS: "comps disagree with each other too much to name one value",
    Reason.CONTEXT_ONLY_LEVEL: "only context-only comps (card family or price tier), which can never declare a deal",
    Reason.SHIPPING_UNKNOWN: "shipping cost is unknown, so the real acquisition cost is unknown",
    # economics
    Reason.BELOW_DISCOUNT_THRESHOLD: "discount is below your threshold",
    Reason.BELOW_MIN_SAVINGS: "dollar savings is below your minimum",
    # auction
    Reason.AUCTION_CURRENT_BID_NOT_A_PRICE:
        "current bid, not a sale price -- an auction's bid is not what it will close at",
    Reason.AUCTION_BID_EXCEEDS_MAX_RATIONAL_BID:
        "the bid is already above the most you could pay and keep your margin",
    # policy
    Reason.LOT: "a lot of several cards, not one card",
    Reason.REPRINT: "a reprint, not the original card",
    Reason.REPLICA: "a replica, not the original card",
    Reason.CUSTOM_CARD: "a custom or art card, not a licensed release",
    Reason.DIGITAL_CARD: "a digital card -- nothing physical ships",
    Reason.FACSIMILE_AUTO: "a printed facsimile autograph, not a signed card",
    Reason.SEALED_PRODUCT: "sealed wax (box, pack or case), not a single card",
    Reason.BREAK_SLOT: "a spot in a group break, not a card you are guaranteed",
    Reason.PICK_YOUR_CARD: "a pick-your-card listing -- the price is not for one specific card",
    Reason.DAMAGED_CONDITION: "described as damaged, creased, trimmed or altered",
    Reason.SELLER_RISK: "seller risk signals make this one to skip",
    Reason.COMMON_CARD: (
        "a cheap card with nothing that makes a copy scarce -- no rookie, auto, patch, "
        "serial number, parallel or grade. Cheap because it is common"
    ),
    # dedupe
    Reason.ALREADY_REPORTED: "already reported to you before",
    Reason.PRICE_NOT_DROPPED: "seen before and the price has not dropped since",
}


REASON_CATEGORIES: Dict[str, str] = {
    # identity
    Reason.NO_PLAYER_MATCH: CATEGORY_IDENTITY,
    Reason.MULTI_PLAYER_CARD: CATEGORY_IDENTITY,
    Reason.IDENTITY_UNCERTAIN: CATEGORY_IDENTITY,
    Reason.GRADE_UNCERTAIN: CATEGORY_IDENTITY,
    # data quality
    Reason.NO_PRICE: CATEGORY_DATA_QUALITY,
    Reason.NO_COMP_AT_ANY_LEVEL: CATEGORY_DATA_QUALITY,
    Reason.THIN_SAMPLE: CATEGORY_DATA_QUALITY,
    Reason.STALE_COMPS: CATEGORY_DATA_QUALITY,
    Reason.DISPERSED_COMPS: CATEGORY_DATA_QUALITY,
    Reason.CONTEXT_ONLY_LEVEL: CATEGORY_DATA_QUALITY,
    Reason.SHIPPING_UNKNOWN: CATEGORY_DATA_QUALITY,
    # economics
    Reason.BELOW_DISCOUNT_THRESHOLD: CATEGORY_ECONOMICS,
    Reason.BELOW_MIN_SAVINGS: CATEGORY_ECONOMICS,
    # auction
    Reason.AUCTION_CURRENT_BID_NOT_A_PRICE: CATEGORY_AUCTION,
    Reason.AUCTION_BID_EXCEEDS_MAX_RATIONAL_BID: CATEGORY_AUCTION,
    # policy
    Reason.LOT: CATEGORY_POLICY,
    Reason.REPRINT: CATEGORY_POLICY,
    Reason.REPLICA: CATEGORY_POLICY,
    Reason.CUSTOM_CARD: CATEGORY_POLICY,
    Reason.DIGITAL_CARD: CATEGORY_POLICY,
    Reason.FACSIMILE_AUTO: CATEGORY_POLICY,
    Reason.SEALED_PRODUCT: CATEGORY_POLICY,
    Reason.BREAK_SLOT: CATEGORY_POLICY,
    Reason.PICK_YOUR_CARD: CATEGORY_POLICY,
    Reason.DAMAGED_CONDITION: CATEGORY_POLICY,
    Reason.SELLER_RISK: CATEGORY_POLICY,
    Reason.COMMON_CARD: CATEGORY_POLICY,
    # dedupe
    Reason.ALREADY_REPORTED: CATEGORY_DEDUPE,
    Reason.PRICE_NOT_DROPPED: CATEGORY_DEDUPE,
}


# Insertion-ordered (Python 3.7+), so iteration order is deterministic and
# tests can rely on it. Derived from REASON_LABELS rather than hand-written
# a third time -- three hand-maintained lists is two too many.
ALL_REASONS: Tuple[str, ...] = tuple(REASON_LABELS)


class UnknownReasonError(ValueError):
    """Raised when a reason string is not in ``ALL_REASONS``.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers
    still behave, but is nameable so a caller can treat "the developer
    typo'd a reason" differently from "the user typed a bad number".
    """


def validate(reason: str) -> str:
    """Return ``reason`` unchanged, or raise ``UnknownReasonError``.

    Returns the value so it can be used inline: ``r = validate(reason)``.
    """
    if reason not in REASON_LABELS:
        raise UnknownReasonError(
            "unknown rejection reason {!r} -- add it to src/reasons.py (Reason, "
            "REASON_LABELS and REASON_CATEGORIES) rather than inventing it at the "
            "call site, or it will never be counted or labelled".format(reason)
        )
    return reason


def label(reason: str) -> str:
    """Human-readable phrase for a reason. Raises on an unknown reason."""
    return REASON_LABELS[validate(reason)]


def category(reason: str) -> str:
    """Category ("identity", "economics", ...). Raises on an unknown reason."""
    return REASON_CATEGORIES[validate(reason)]


class RejectionLog:
    """Accumulates rejection reasons over one pipeline run.

    Deliberately dumb: it counts, it labels, it refuses unknown reasons.
    It does not know what a Listing is, does not read or write files, and
    does not decide anything -- so it can be constructed in a test in one
    line and asserted on directly.

    Listing ids are optional because two different callers want two
    different things: the report footer only needs "12 x reprint", while
    debugging a surprising count needs to know *which* 12. Passing an id
    costs nothing and gives you both.

    Not thread-safe; the pipeline is single-threaded by design.
    """

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}
        self._listing_ids: Dict[str, List[str]] = {}

    def record(self, reason: str, listing_id: Optional[str] = None) -> None:
        """Count one rejection.

        Raises ``UnknownReasonError`` if ``reason`` is not a known reason.
        That is the whole point: a typo'd reason that quietly incremented
        a counter nobody ever prints would put us straight back to
        silently dropping listings, which is the bug this module exists
        to prevent.
        """
        validate(reason)
        self._counts[reason] = self._counts.get(reason, 0) + 1
        if listing_id is not None:
            self._listing_ids.setdefault(reason, []).append(listing_id)

    def record_many(self, reason: str, listing_ids: Iterable[str]) -> None:
        """Record one rejection per id. Convenience for bulk filtering."""
        for listing_id in listing_ids:
            self.record(reason, listing_id)

    def counts(self) -> Dict[str, int]:
        """Reason -> count, ordered by count descending.

        Ties break alphabetically by reason so the report is byte-stable
        across runs with the same input -- a diffable email is worth more
        than a marginally prettier one.
        """
        ordered = sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return dict(ordered)

    def counts_by_category(self) -> Dict[str, int]:
        """Category -> total count, ordered by count descending (ties
        alphabetical). Only categories actually seen appear."""
        totals: Dict[str, int] = {}
        for reason, count in self._counts.items():
            cat = REASON_CATEGORIES[reason]
            totals[cat] = totals.get(cat, 0) + count
        return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])))

    def total(self) -> int:
        """Every rejection recorded, across all reasons."""
        return sum(self._counts.values())

    def listing_ids(self, reason: str) -> List[str]:
        """Ids recorded against a reason, in the order they were seen.

        Returns a copy, so a caller sorting or truncating the list cannot
        corrupt the log. Raises on an unknown reason.
        """
        return list(self._listing_ids.get(validate(reason), []))

    def summary_lines(self) -> List[str]:
        """Report-ready lines, e.g. ``"12 x no comparable sales at any level"``.

        Same order as ``counts()``. Returns an empty list when nothing was
        rejected -- the caller decides whether "nothing was rejected" is
        worth a line of email.
        """
        return ["{} x {}".format(count, REASON_LABELS[reason]) for reason, count in self.counts().items()]
