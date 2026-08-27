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

TOP_REASONS_SHOWN = 5


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
    #: Whole days since the last completed scan, or None when unknown. 1 is
    #: the ordinary cadence; more than that is a hole in the corpus, which
    #: matters more than any single day's contents -- a gap values things
    #: slightly wrong for the whole retention window afterwards.
    days_since_last_run: Optional[int] = None
    #: What share of alert titles arrived cut short by eBay. The set,
    #: parallel, card number and grade all live past that cut, so this is
    #: close to a ceiling on what the valuation engine can ever do.
    titles_truncated_pct: Optional[float] = None
    #: What share of alert titles had a longer copy elsewhere in the same
    #: anchor that we refused because it did not match the visible text.
    #: Without it the truncation rate above is unreadable: a stubborn 98%
    #: could mean eBay sends no fuller title (a real ceiling) or that we are
    #: throwing one away (our bug). None means the run never measured it.
    titles_recovery_refused_pct: Optional[float] = None
    #: Of the days missing from the run marker, how many the corpus DOES
    #: hold observations for. Those days were scanned and only the email
    #: failed, which is a different loss from never having looked -- see
    #: price_history.observed_dates.
    scanned_but_unreported_days: Optional[int] = None

    rejections: reasons.RejectionLog = field(default_factory=reasons.RejectionLog)
    warnings: list = field(default_factory=list)
    #: The subset of `warnings` that mean the run may not have worked --
    #: see warn(broken=True). Only these change the subject line.
    breakage_warnings: list = field(default_factory=list)

    def warn(self, message: str, *, broken: bool = False) -> None:
        """A run-level concern worth surfacing in the email, not just the
        log file. Use sparingly -- a footer nobody reads is worse than no
        footer.

        ``broken=True`` means "the run itself may not have worked, do not
        trust the numbers below" -- eBay changed its email template, messages
        could not be read. Those change the subject line and the top of the
        email.

        Everything else is a notable-but-normal state: no comp bucket is
        strong enough to flag today, a gate has been configured off. Those
        belong in the footer and nowhere else. The distinction is the whole
        value of the alarm: "no flag-eligible bucket" is true on most days
        right now, and an alarm that fires every morning is not an alarm.
        """
        self.warnings.append(message)
        if broken:
            self.breakage_warnings.append(message)

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

        if self.days_since_last_run is not None and self.days_since_last_run > 1:
            missed = self.days_since_last_run - 1
            # The marker only records runs that finished by EMAILING you, and
            # the corpus is written before the send. So a gap in the marker
            # has two causes with opposite consequences, and claiming the
            # worse one when the corpus disproves it is the same overclaim
            # this project refuses everywhere else.
            scanned = self.scanned_but_unreported_days or 0
            unseen = max(missed - scanned, 0)
            if unseen:
                lines.append(
                    "!! {} day(s) since the last completed scan -- {} day(s) of listings were "
                    "never seen and cannot be recovered (eBay's alert emails only look back "
                    "a couple of days). Check the Actions tab: GitHub drops scheduled "
                    "workflows under load.".format(self.days_since_last_run, unseen)
                )
            if scanned:
                lines.append(
                    "!! {} day(s) were scanned but never reached you -- the listings are in "
                    "the corpus and still count towards comps, but no email went out, so "
                    "anything worth acting on that day went unseen. That is a send failure "
                    "rather than a missed run: check the Actions tab and the app "
                    "password.".format(scanned)
                )

        if self.titles_truncated_pct is not None:
            lines.append(
                "Titles: {:.0f}% arrived truncated by eBay -- a cut title is missing its set, "
                "parallel, card number and grade, which is most of why comps do not "
                "form".format(self.titles_truncated_pct)
            )

        if self.titles_recovery_refused_pct is not None:
            # Printed even at 0%, because 0% is the answer to the question
            # the line above always raises -- "is that ceiling eBay's or
            # ours?" -- and a line that only appears on bad days leaves the
            # good days looking unmeasured.
            lines.append(
                "Titles: a fuller copy was present but refused as not-this-listing for "
                "{:.0f}% -- 0% means eBay's HTML carries no fuller title, so the rate "
                "above is a real ceiling, not a check of ours discarding "
                "it".format(self.titles_recovery_refused_pct)
            )

        if self.sold_comps_summary:
            lines.append(self.sold_comps_summary)

        # The category roll-up and the top reasons are the same data at two
        # granularities, and printing both on consecutive lines is a footer
        # saying one thing twice. The reasons are the useful half -- "94x no
        # comparable listing at any level" tells you what to go and fix,
        # "94 valuation" does not -- so the roll-up only earns its line when
        # the top five do not already cover everything.
        all_reasons = list(self.rejections.counts().items())
        by_category = self.rejections.counts_by_category()
        if by_category and len(all_reasons) > TOP_REASONS_SHOWN:
            lines.append(
                "Not reported, by category: "
                + ", ".join("{} {}".format(count, name.replace("_", " ")) for name, count in by_category.items())
            )

        top = all_reasons[:TOP_REASONS_SHOWN]
        if top:
            label = "Top reasons" if len(all_reasons) > TOP_REASONS_SHOWN else "Not reported"
            lines.append(
                "{}: ".format(label)
                + "; ".join("{}x {}".format(c, reasons.label(r)) for r, c in top)
            )

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
