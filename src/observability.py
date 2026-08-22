"""Run-level data-quality accounting.

A pipeline can finish with exit code 0 and produce garbage. Before this
module existed, the only way to tell "eBay legitimately had nothing new"
apart from "the parser silently broke" was to notice that the email looked
thin -- and 21% of listings were dropped with no comp, no reason, and no
count anywhere.

RunStats is the counter-side of src/reasons.py: reasons explain individual
listings, RunStats explains the run. Both end up in the report's SYSTEM
HEALTH footer, deliberately as a small footer rather than a wall of
metrics -- the report's job is to tell you where to look first, and health
data earns its place only by being short enough to skim and alarming
enough to notice when it's wrong.

Everything here is a plain counter incremented by main.py. No I/O, no
clock, no globals -- so a test can build one in three lines and assert on
it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src import reasons


def _pct(part: int, whole: int) -> Optional[float]:
    """None when there's nothing to take a percentage of. A "0%" printed
    from an empty denominator reads as a failure when it's actually an
    absence of input, and those need to look different."""
    if not whole:
        return None
    return part / whole * 100.0


@dataclass
class RunStats:
    """Counts for one daily scan. Field names are the metric names printed
    in the report, so renaming one is a user-visible change."""

    alert_emails_scanned: int = 0
    listings_extracted: int = 0
    listings_matched_to_watchlist: int = 0

    # identity
    identity_exact: int = 0        # year + set + parallel + card number all known
    identity_partial: int = 0      # some fields known
    identity_none: int = 0         # nothing beyond the player

    # valuation
    valued: int = 0                # got a comp at any level
    valued_flag_eligible: int = 0  # got a comp a deal may actually be declared from
    unvalued: int = 0              # no comp at any level

    # listing shape
    auctions: int = 0
    fixed_price: int = 0
    listing_type_unknown: int = 0
    shipping_known: int = 0
    shipping_unknown: int = 0

    # outcomes
    opportunities_reported: int = 0
    price_drops: int = 0
    duplicates_suppressed: int = 0
    blocked_by_negative_signal: int = 0

    # One line about the hand-entered sold-comp corpus. Worth a permanent
    # slot in the footer rather than a warning: "every comp is an asking
    # price" is the single biggest caveat on everything above it, and it
    # should be visible on the days it is true, not only when it changes.
    sold_comps_summary: str = ""

    rejections: reasons.RejectionLog = field(default_factory=reasons.RejectionLog)
    warnings: list = field(default_factory=list)

    def warn(self, message: str) -> None:
        """A run-level concern worth surfacing in the email, not just the
        log file. Use sparingly -- a footer nobody reads is worse than no
        footer."""
        self.warnings.append(message)

    # --- derived rates -----------------------------------------------------

    @property
    def exact_identity_rate(self) -> Optional[float]:
        return _pct(self.identity_exact, self.listings_matched_to_watchlist)

    @property
    def comp_coverage_rate(self) -> Optional[float]:
        return _pct(self.valued, self.listings_matched_to_watchlist)

    @property
    def flag_eligible_coverage_rate(self) -> Optional[float]:
        return _pct(self.valued_flag_eligible, self.listings_matched_to_watchlist)

    @property
    def unknown_shipping_rate(self) -> Optional[float]:
        known = self.shipping_known + self.shipping_unknown
        return _pct(self.shipping_unknown, known)

    @property
    def unknown_listing_type_rate(self) -> Optional[float]:
        total = self.auctions + self.fixed_price + self.listing_type_unknown
        return _pct(self.listing_type_unknown, total)

    def unexplained_count(self) -> int:
        """Listings that reached the pipeline and left it with neither a
        report slot nor a recorded reason. **This must be zero.** A non-zero
        value means something is being dropped silently again, which is the
        exact defect this accounting exists to prevent, so the report calls
        it out rather than burying it.
        """
        accounted = self.opportunities_reported + self.rejections.total()
        return max(0, self.listings_matched_to_watchlist - accounted)

    def health_lines(self) -> list:
        """The SYSTEM HEALTH footer, as a list of short lines."""
        lines = [
            "Emails scanned: {}   Listings parsed: {}   Matched to watchlist: {}".format(
                self.alert_emails_scanned, self.listings_extracted, self.listings_matched_to_watchlist
            ),
            "Identity: {} exact / {} partial / {} unidentified{}".format(
                self.identity_exact,
                self.identity_partial,
                self.identity_none,
                _rate_suffix(self.exact_identity_rate, "exact"),
            ),
            "Comps: {} valued{}, {} of those strong enough to flag a deal{}, {} with no comp at any level".format(
                self.valued,
                _rate_suffix(self.comp_coverage_rate, "coverage"),
                self.valued_flag_eligible,
                _rate_suffix(self.flag_eligible_coverage_rate, "of matched"),
                self.unvalued,
            ),
            "Listing types: {} auction / {} fixed price / {} unknown{}".format(
                self.auctions,
                self.fixed_price,
                self.listing_type_unknown,
                _rate_suffix(self.unknown_listing_type_rate, "unknown"),
            ),
            "Shipping: {} known / {} unknown{}".format(
                self.shipping_known, self.shipping_unknown, _rate_suffix(self.unknown_shipping_rate, "unknown")
            ),
            "Outcomes: {} reported, {} price drops, {} already-seen suppressed, {} blocked on a negative signal".format(
                self.opportunities_reported,
                self.price_drops,
                self.duplicates_suppressed,
                self.blocked_by_negative_signal,
            ),
        ]

        if self.sold_comps_summary:
            lines.append(self.sold_comps_summary)

        by_category = self.rejections.counts_by_category()
        if by_category:
            lines.append(
                "Not reported, by category: "
                + ", ".join("{} {}".format(count, name.replace("_", " ")) for name, count in by_category.items())
            )

        top = list(self.rejections.counts().items())[:5]
        if top:
            lines.append("Top reasons: " + "; ".join("{}x {}".format(c, reasons.label(r)) for r, c in top))

        unexplained = self.unexplained_count()
        if unexplained:
            lines.append(
                "!! {} listing(s) left the pipeline with no outcome and no recorded reason -- "
                "that is a bug, not a quiet day. See logs/scraper.log.".format(unexplained)
            )

        for warning in self.warnings:
            lines.append("!! " + warning)

        return lines


def _rate_suffix(rate: Optional[float], noun: str) -> str:
    if rate is None:
        return ""
    return " ({:.0f}% {})".format(rate, noun)
