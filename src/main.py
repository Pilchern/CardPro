"""Daily entry point.

Scan the watchlist, work out what each listing actually is, value it
against comparable sales, run the economics, and email a decision-first
report. Nothing is ever bought automatically -- CardPro discovers and
analyses, you decide.

Run manually:   python -m src.main
Dry run (no email sent, no state written):   python -m src.main --dry-run

CardPro 2.0 changed the shape of this file in three ways that matter:

1. **One evaluation path.** There used to be two near-duplicate flagging
   functions, one per data source, which had to be kept in sync by hand.
   Both sources now produce the same `Listing` objects and go through the
   same `evaluate_listings()`, differing only in where their observations
   came from and whether those observations are sold prices or asking
   prices (`basis`).

2. **Nothing leaves silently.** Every listing exits with either a slot in
   the report or exactly one recorded reason (src/reasons.py), counted in
   the run's `RunStats` (src/observability.py). Previously 21% of listings
   simply vanished when no comp was found, which made "quiet day" and
   "silently broken" look identical.

3. **Order of operations.** Truncated titles are flagged BEFORE valuation,
   not after. eBay truncates long titles and "PSA 1..." parses as PSA 1, so
   a PSA 10 could be valued as a PSA 1 with the flag decision already made.
   CardPro does not fetch the item page to recover the real title -- that
   would be automated access to eBay's site (principle #1). Instead a
   truncated title that parsed a grade is rejected as GRADE_UNCERTAIN and
   sent to NEEDS REVIEW, because a wrong grade is worse than no valuation.
"""
from __future__ import annotations

import argparse
import logging
import traceback
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import (
    card_identity,
    comp_requests,
    comps,
    craigslist_links,
    dedupe,
    desirability,
    ebay_client,
    ebay_email_alerts,
    economics,
    emailer,
    matcher,
    observability,
    price_history,
    reasons,
    report,
    report_html,
    run_marker,
    sold_comps,
    search_terms,
    targets,
)
from src.config import ROOT_DIR, load_config
from src.models import Listing

LOG_PATH = ROOT_DIR / "logs" / "scraper.log"

# Module-level, like every other module in src/. It used to be created
# fresh inside each function that logged, which meant a function that
# logged but forgot the line (fetch_ebay_alert_active did) raised
# NameError mid-scan instead of writing a log record. Logger objects
# resolve their handlers at call time, so binding one here before
# setup_logging() runs is safe.
logger = logging.getLogger("main")

# Caps scraper.log at ~2MB, keeping 5 rotated backups (scraper.log.1 .. .5)
# so a script that runs once a day forever doesn't grow the log unbounded.
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 5

# Which negative signal maps to which rejection reason. Signals that aren't
# here (currently only "damaged") are surfaced as risks rather than blocks --
# a damaged card is still a real card, just worth less.
SIGNAL_TO_REASON = {
    "reprint": reasons.Reason.REPRINT,
    "replica": reasons.Reason.REPLICA,
    "custom": reasons.Reason.CUSTOM_CARD,
    "digital": reasons.Reason.DIGITAL_CARD,
    "facsimile_auto": reasons.Reason.FACSIMILE_AUTO,
    "sealed_product": reasons.Reason.SEALED_PRODUCT,
    "break_slot": reasons.Reason.BREAK_SLOT,
    "pick_your_card": reasons.Reason.PICK_YOUR_CARD,
    "lot": reasons.Reason.LOT,
}

# comps.CompMatch.blocked_reasons -> the canonical rejection reason.
BLOCKED_TO_REASON = {
    "context_only_level": reasons.Reason.CONTEXT_ONLY_LEVEL,
    "thin_sample": reasons.Reason.THIN_SAMPLE,
    "stale_comps": reasons.Reason.STALE_COMPS,
    "dispersed_comps": reasons.Reason.DISPERSED_COMPS,
    "concentrated_sample": reasons.Reason.CONCENTRATED_SAMPLE,
}


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_handler, logging.StreamHandler(sys.stdout)],
        force=True,
    )


# --------------------------------------------------------------------------
# Building Listings from each source
# --------------------------------------------------------------------------


def _build_listing(cfg, *, listing_id, source, title, price, url, players, shipping_price=None,
                   listing_type="unknown", bid_count=None, time_left_text=None, has_best_offer=False):
    """One Listing with identity fully extracted. Returns None when no
    watchlist player is in the title -- the caller records the reason."""
    matched = matcher.match_players(title, players)
    if not matched:
        return None

    identity = card_identity.extract_card_identity(title)
    grade_info = matcher.detect_grade_details(title)
    return Listing(
        id=listing_id,
        source=source,
        title=title,
        price=price,
        url=url,
        player=matched[0],
        card_type=grade_info.card_type,
        grader=grade_info.grader,
        grade=grade_info.grade,
        qualifier=grade_info.qualifier,
        player_tier=cfg.player_tiers.get(matched[0], "legend"),
        is_rookie_card=matcher.detect_rookie_card(title),
        card_identity=identity,
        shipping_price=shipping_price,
        listing_type=listing_type,
        bid_count=bid_count,
        time_left_text=time_left_text,
        has_best_offer=has_best_offer,
        negative_signals=tuple(identity.negative_signals.value or ()),
        matched_players=tuple(matched),
    )


def fetch_ebay_active(cfg, players, token, stats) -> list:
    """Active listings via the eBay Browse API (dormant unless credentials
    are set). Unlike the email path, Browse tells us the buying option
    outright, so listing_type is known rather than inferred."""
    listings = []
    for player in players:
        items = ebay_client.search_active_listings(
            query=player,
            token=token,
            category_id=cfg.ebay_category_id,
            marketplace_id=cfg.ebay_marketplace_id,
            limit=cfg.ebay_active_listing_limit,
        )
        for item in items:
            stats.listings_extracted += 1
            title = item.get("title", "")
            price = ebay_client.extract_price(item)
            buying_options = item.get("buyingOptions") or []
            listing = _build_listing(
                cfg,
                listing_id=item.get("itemId", item.get("itemWebUrl", title)),
                source="ebay",
                title=title,
                price=price,
                url=item.get("itemWebUrl", ""),
                players=[player],
                listing_type=_browse_listing_type(buying_options),
                has_best_offer="BEST_OFFER" in buying_options,
            )
            if listing is None:
                stats.rejections.record(reasons.Reason.NO_PLAYER_MATCH)
                continue
            listings.append(listing)
    return listings


def _browse_listing_type(buying_options) -> str:
    if "AUCTION" in buying_options:
        return "auction"
    if "FIXED_PRICE" in buying_options:
        return "fixed_price"
    return "unknown"


def fetch_ebay_alert_active(cfg, stats) -> list:
    """eBay-via-email-alerts path (see ebay_email_alerts.py)."""
    counters = {}
    items = ebay_email_alerts.fetch_alert_listings(
        cfg.gmail_address,
        cfg.gmail_app_password,
        cfg.ebay_alerts_sender_contains,
        cfg.ebay_alerts_lookback_days,
        cfg.ebay_alerts_mailbox,
        counters=counters,
    )
    stats.alert_emails_scanned += counters.get("messages", 0)
    stats.listings_extracted += len(items)
    # Both of these used to exist only in a log file on a runner GitHub
    # deletes minutes later. The template warning in particular is the alarm
    # that says every number below it is a fabricated quiet day.
    if counters.get("template_warning"):
        stats.warn(counters["template_warning"], broken=True)
    seen = counters.get("titles_seen", 0)
    cut = counters.get("titles_truncated", 0)
    refused = counters.get("titles_recovery_refused", 0)
    if seen:
        # The measured bottleneck, reported every run so a change to title
        # recovery is visible the next morning rather than argued about. A
        # truncated title is missing its set, parallel, card number and
        # grade -- everything the comp key needs -- so this number is close
        # to a ceiling on what the valuation engine can ever do.
        stats.titles_truncated_pct = 100.0 * cut / seen
        # ... and the half of that number we can actually act on: how often a
        # fuller title was in the email and our own match check refused it.
        stats.titles_recovery_refused_pct = 100.0 * refused / seen
        logger.info(
            "Titles: %d seen, %d still truncated (%.0f%%), %d had a fuller copy refused",
            seen, cut, stats.titles_truncated_pct, refused,
        )

    if counters.get("fetch_failures"):
        stats.warn(
            "{} alert email(s) could not be read from the mailbox and were skipped, so "
            "the scanned count below understates what eBay actually sent.".format(
                counters["fetch_failures"]
            ),
            broken=True,
        )

    listings = []
    for item in items:
        listing = _build_listing(
            cfg,
            listing_id=item["url"],
            source="ebay-alert",
            title=item["title"],
            price=item["price"],
            url=item["url"],
            players=cfg.players,
            shipping_price=item.get("shipping_price"),
            listing_type=item.get("listing_type", "unknown"),
            bid_count=item.get("bid_count"),
            time_left_text=item.get("time_left_text"),
            has_best_offer=bool(item.get("has_best_offer")),
        )
        if listing is None:
            stats.rejections.record(reasons.Reason.NO_PLAYER_MATCH)
            continue
        listings.append(listing)
    return listings


def fetch_ebay_sold_observations(cfg, players, token) -> list:
    """Real sold comps via Marketplace Insights, as observation dicts with
    basis="sold" -- the only source in this project that can produce them.
    Empty when the API isn't available, which is the normal case."""
    observations = []
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for player in players:
        items = ebay_client.search_sold_items(
            query=player,
            token=token,
            category_id=cfg.ebay_category_id,
            marketplace_id=cfg.ebay_marketplace_id,
            lookback_days=cfg.ebay_sold_lookback_days,
        )
        for item in items:
            title = item.get("title", "")
            if not matcher.match_player(title, [player]):
                continue
            identity = card_identity.extract_card_identity(title)
            if card_identity.is_excluded_from_deals(identity):
                continue  # a lot / reprint / sealed box price is not a single-card comp
            price = ebay_client.extract_price(item)
            if price is None:
                continue
            grade_info = matcher.detect_grade_details(title)
            observations.append(
                {
                    "price": price,
                    "date": (item.get("lastSoldDate") or today_str)[:10],
                    "id": item.get("itemId", title),
                    "player": player,
                    "card_type": grade_info.card_type,
                    "year": identity.year.value,
                    "set_name": identity.set_name.value,
                    "parallel": identity.parallel.value,
                    "card_number": identity.card_number.value,
                    "grader": grade_info.grader,
                    "grade": grade_info.grade,
                    "qualifier": grade_info.qualifier,
                    "basis": comps.BASIS_SOLD,
                }
            )
    return observations


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------


def listings_as_observations(listings, today_str: str) -> list:
    """Today's asking prices as comp observations, for a source that has no
    persisted corpus of its own (the eBay API path).

    Sold observations always win: a bucket's `basis` is only "sold" when every
    point in it is a real transaction, so mixing these in downgrades the
    bucket to asking-basis and caps its confidence -- which is the honest
    outcome, not a loss. Auctions, hard-blocked listings, and listings with
    an untrustworthy grade are excluded for the same reasons as
    record_observations.
    """
    observations = []
    for listing in listings:
        if listing.price is None or listing.is_auction:
            continue
        if listing.title_truncated and listing.grade is not None:
            # A grade read off a truncated title is probably wrong, and a
            # wrong grade in the corpus is worse than a missing one: it sits
            # there for the full retention window mis-valuing every genuine
            # card at that grade (principle #6). The listing itself is
            # already rejected as GRADE_UNCERTAIN; this keeps it from
            # damaging everything else too.
            continue
        identity = listing.card_identity
        if identity is not None and card_identity.is_excluded_from_deals(identity):
            continue
        grade_info = matcher.detect_grade_details(listing.title)
        observations.append(
            {
                "price": listing.price,
                "date": today_str,
                "id": listing.id,
                "player": listing.player,
                "card_type": listing.card_type,
                "year": identity.year.value if identity else None,
                "set_name": identity.set_name.value if identity else None,
                "parallel": identity.parallel.value if identity else None,
                "card_number": identity.card_number.value if identity else None,
                "grader": listing.grader,
                "grade": listing.grade,
                "qualifier": grade_info.qualifier,
                "basis": comps.BASIS_ASKING,
            }
        )
    return observations


def record_observations(listings, history, today_str: str) -> int:
    """Append each listing's asking price to the self-built comp corpus.

    Auctions are deliberately NOT recorded: an auction's current bid is not
    a price, and letting one into the comp corpus would poison every future
    valuation of that card with a number that was never paid. Listings with
    a hard negative signal (reprint, lot, sealed box) are excluded for the
    same reason -- their price isn't the price of the card. So are
    listings whose grade came off a truncated title: recording one writes a
    grade that is probably wrong into a corpus other cards are valued
    against.
    """
    recorded = 0
    for listing in listings:
        if listing.price is None or listing.is_auction:
            continue
        if listing.title_truncated and listing.grade is not None:
            # A grade read off a truncated title is probably wrong, and a
            # wrong grade in the corpus is worse than a missing one: it sits
            # there for the full retention window mis-valuing every genuine
            # card at that grade (principle #6). The listing itself is
            # already rejected as GRADE_UNCERTAIN; this keeps it from
            # damaging everything else too.
            continue
        identity = listing.card_identity
        if identity is not None and card_identity.is_excluded_from_deals(identity):
            continue
        grade_info = matcher.detect_grade_details(listing.title)
        price_history.record(
            history,
            listing.player,
            listing.card_type,
            listing.price,
            today_str,
            listing.id,
            year=identity.year.value if identity else None,
            set_name=identity.set_name.value if identity else None,
            parallel=identity.parallel.value if identity else None,
            card_number=identity.card_number.value if identity else None,
            grader=listing.grader,
            grade=listing.grade,
            qualifier=grade_info.qualifier,
            print_run=identity.print_run.value if identity else None,
            manufacturer=identity.manufacturer.value if identity else None,
            is_base=identity.is_base.value if identity else None,
            title=listing.title,
            basis=comps.BASIS_ASKING,
        )
        recorded += 1
    return recorded


# --------------------------------------------------------------------------
# Truncated titles -- must run BEFORE valuation
# --------------------------------------------------------------------------


def mark_truncated_titles(listings) -> None:
    """eBay's alert emails truncate long titles, and a truncated grade parses
    as the wrong grade ("PSA 1..." -> PSA 1, when it's really PSA 10). Flag
    those listings so nothing downstream trusts the grade.

    CardPro does not go and fetch the real title from the item page. That
    would be automated access to eBay's site, which their User Agreement
    prohibits and which principle #1 rules out -- the same reason this
    project won't scrape Craigslist. An unknown grade is an acceptable
    outcome; defeating a site's defenses to learn it is not.

    This runs before valuation on purpose: a listing whose grade can't be
    trusted is worth knowing about before the comp lookup, not after one has
    already been made against a grade that could be off by a factor of ten.
    See docs/CARDPRO_2_AUDIT.md failure mode #5.
    """
    uncertain = 0
    for listing in listings:
        if not ebay_email_alerts.looks_truncated(listing.title):
            continue
        listing.title_truncated = True
        uncertain += 1

    if uncertain:
        logger.info(
            "%d listing(s) carry an uncertain grade (eBay truncated the title); "
            "the grade on those is not trusted",
            uncertain,
        )


# --------------------------------------------------------------------------
# Evaluation -- the single path both sources go through
# --------------------------------------------------------------------------


def _fee_model(cfg):
    return economics.FeeModel(
        marketplace_fee_pct=cfg.fee_marketplace_pct,
        marketplace_fixed_fee=cfg.fee_marketplace_fixed,
        payment_fee_pct=cfg.fee_payment_pct,
        outbound_shipping=cfg.outbound_shipping,
        supplies=cfg.supplies_cost,
        sales_tax_pct=cfg.sales_tax_pct,
    )


def _count_identity(listing, stats) -> None:
    identity = listing.card_identity
    known = 0
    if identity is not None:
        known = sum(
            1
            for field in (identity.year, identity.set_name, identity.parallel, identity.card_number)
            if field.value is not None
        )
    if known >= 4:
        stats.identity_exact += 1
    elif known:
        stats.identity_partial += 1
    else:
        stats.identity_none += 1


def _count_shape(listing, stats) -> None:
    if listing.listing_type == "auction":
        stats.auctions += 1
    elif listing.listing_type == "fixed_price":
        stats.fixed_price += 1
    else:
        stats.listing_type_unknown += 1
    if listing.shipping_price is None:
        stats.shipping_unknown += 1
    else:
        stats.shipping_known += 1


def evaluate_listings(listings, engine, cfg, stats) -> None:
    """Value every listing and decide what, if anything, it is.

    Sets `rejection_reason` on everything that does not become an
    opportunity, so the run can account for every listing it saw. A listing
    can carry a rejection reason and still appear in the report (as an
    auction, a target hit, or in NEEDS REVIEW) -- the reason explains why it
    isn't being called a deal, not that it was thrown away.
    """
    fees = _fee_model(cfg)

    if not cfg.require_flag_eligible_comp:
        # You turned this off deliberately, so CardPro will do it -- but it
        # will not do it quietly. Context-only levels are the price-tier
        # bucket (defined by price, so the cheap end of every bucket reads as
        # under market) and same_set (parallel unknown on both sides). Both
        # produced real false positives in production; see
        # docs/CARDPRO_2_AUDIT.md failure modes #1 and #3.
        stats.warn(
            "valuation.require_flag_eligible_comp is FALSE, so deals may be declared from "
            "context-only comps -- including the price-bracket level, which is defined by "
            "price and therefore cannot be evidence about price. Every one of v1's false "
            "positives came from exactly this. Treat anything flagged today at a "
            "context-only level as unverified."
        )

    for listing in listings:
        stats.listings_matched_to_watchlist += 1
        _count_identity(listing, stats)
        _count_shape(listing, stats)

        if listing.price is None:
            listing.rejection_reason = reasons.Reason.NO_PRICE
            stats.rejections.record(reasons.Reason.NO_PRICE, listing.id)
            continue

        identity = listing.card_identity
        if identity is not None and card_identity.is_excluded_from_deals(identity):
            blocking = [s for s in listing.negative_signals if s in SIGNAL_TO_REASON]
            reason = SIGNAL_TO_REASON[blocking[0]] if blocking else reasons.Reason.IDENTITY_UNCERTAIN
            listing.rejection_reason = reason
            stats.rejections.record(reason, listing.id)
            stats.blocked_by_negative_signal += 1
            continue

        if len(listing.matched_players) > 1:
            # A dual/triple auto is a different market from either player's
            # single card, and no comp bucket here represents it.
            listing.rejection_reason = reasons.Reason.MULTI_PLAYER_CARD
            stats.rejections.record(reasons.Reason.MULTI_PLAYER_CARD, listing.id)
            continue

        if listing.title_truncated and listing.grade is not None:
            # eBay cut the title short and a grade came out of what was left,
            # so that grade is not merely unknown -- it is probably wrong
            # ("PSA 1..." reads as PSA 1 when the card is a PSA 10). Comping
            # it would compare two different cards as if they were the same
            # (principle #6) and could declare a deal off a grade nobody ever
            # verified. This sits BEFORE the comp lookup on purpose: valuing
            # first and rejecting after would still attach a market value
            # derived from the wrong grade to the listing, and the report
            # would print that number in NEEDS REVIEW. A number from the
            # wrong bucket is worse than no number (principles #4 and #7).
            # Raw cards are unaffected -- there is no grade to get wrong.
            listing.rejection_reason = reasons.Reason.GRADE_UNCERTAIN
            stats.rejections.record(reasons.Reason.GRADE_UNCERTAIN, listing.id)
            continue

        listing.desirable_attributes = desirability.attributes_of(listing)
        listing.is_cheap = cfg.cheap_cards_enabled and listing.price < cfg.cheap_price_ceiling

        if (
            listing.is_cheap
            and cfg.cheap_require_desirable_attribute
            and desirability.is_commodity(listing, cfg.cheap_price_ceiling)
        ):
            # Cheap is fine; cheap AND indistinguishable is not. There are
            # thousands of base commons and a 60%-off base common is still a
            # base common. Rejected with a stated reason and counted, not
            # silently dropped -- see config/settings.json "cheap_cards".
            listing.rejection_reason = reasons.Reason.COMMON_CARD
            stats.rejections.record(reasons.Reason.COMMON_CARD, listing.id)
            continue

        listing.target_hit = targets.best_hit(
            cfg.target_cards,
            player=listing.player,
            total_cost=listing.total_cost,
            year=identity.year.value if identity else None,
            set_name=identity.set_name.value if identity else None,
            parallel=identity.parallel.value if identity else None,
            card_number=identity.card_number.value if identity else None,
            grader=listing.grader,
            grade=listing.grade,
            card_type=listing.card_type,
        )

        grade_info = matcher.detect_grade_details(listing.title)
        match = engine.lookup(
            player=listing.player,
            card_type=listing.card_type,
            price=listing.price,
            grader=listing.grader,
            grade=listing.grade,
            qualifier=grade_info.qualifier,
            year=identity.year.value if identity else None,
            set_name=identity.set_name.value if identity else None,
            parallel=identity.parallel.value if identity else None,
            card_number=identity.card_number.value if identity else None,
            # A listing must never be part of the comp set used to judge it.
            exclude_id=listing.id,
        )

        if match is None:
            stats.unvalued += 1
            listing.rejection_reason = reasons.Reason.NO_COMP_AT_ANY_LEVEL
            stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL, listing.id)
            continue

        stats.valued += 1
        listing.comp_match = match
        listing.market_value = match.stats.median
        listing.comp_median = match.stats.median
        listing.comp_sample_size = match.stats.sample_size
        listing.comp_is_fallback = match.stats.basis == comps.BASIS_ASKING
        listing.comp_level_matched = match.level
        listing.comp_confidence = match.confidence
        listing.economics = economics.evaluate(
            economics.Acquisition(
                price=listing.price, shipping=listing.shipping_price, sales_tax_pct=cfg.sales_tax_pct
            ),
            match.stats.median,
            fees,
            resale_haircut_pct=cfg.resale_haircut_pct,
        )
        # Take the discount from economics rather than recomputing it here.
        # The two used different acquisition costs -- this one excluded sales
        # tax, economics.Acquisition includes it -- so at any non-zero
        # sales_tax_pct one card block printed two different totals: a Cost
        # line saying $32.50 above a profit figure whose arithmetic only
        # works at $35.10. One card, one acquisition cost.
        listing.dollar_savings = listing.economics.gross_discount
        listing.pct_under_market = listing.economics.discount_pct
        # Below roughly $10 a card, postage and fees eat the whole spread, so
        # a negative profit here is arithmetic, not a warning. The report says
        # "collector buy" rather than showing a scary ROI on a card nobody
        # would ever flip.
        listing.resale_uneconomic = listing.economics.expected_profit <= 0

        if listing.is_auction:
            # ONLY off a comp CardPro will stand behind. A max bid is the one
            # number in this report that tells you to spend money -- "the most
            # you can pay and still keep your margin. Above this, stop." --
            # and it was being computed from whatever median was available,
            # including a price-bracket bucket. On the live corpus that put a
            # $729.31 ceiling on a $125 Ernie Banks card, two lines under a
            # Market line reading "not established ... CardPro has no
            # valuation for this card". A number nobody may act on must not
            # come back wearing the one label that says act on it.
            #
            # It cuts the other way too: focus._has_bidding_room drops an
            # auction whose current bid is over this ceiling, so a
            # context-only median was silently removing real auctions from
            # the email. With no ceiling there is nothing to be over, and the
            # report says plainly that none could be computed.
            listing.max_rational_bid = economics.max_rational_bid(
                match.stats.median,
                required_margin_pct=cfg.auction_required_margin_pct,
                shipping_in=listing.shipping_price,
                fees=fees,
                # The same haircut evaluate() applies. Without it the two
                # disagree about the same card, and the ceiling comes out
                # high -- which is the expensive direction to be wrong in.
                resale_haircut_pct=cfg.resale_haircut_pct,
            ) if match.flag_eligible else None
            # Unknown shipping makes the ceiling an upper bound, not a
            # figure. The report has to be able to say so; the bare float
            # cannot carry that.
            listing.max_rational_bid_shipping_known = listing.shipping_price is not None
            # A current bid is not a price, so an auction is never a
            # confirmed deal no matter how far under market it sits. It gets
            # its own report section and its own math instead.
            listing.rejection_reason = reasons.Reason.AUCTION_CURRENT_BID_NOT_A_PRICE
            stats.rejections.record(reasons.Reason.AUCTION_CURRENT_BID_NOT_A_PRICE, listing.id)
            continue

        if not match.flag_eligible and cfg.require_flag_eligible_comp:
            blocked = match.blocked_reasons or ("context_only_level",)
            reason = BLOCKED_TO_REASON.get(blocked[0], reasons.Reason.CONTEXT_ONLY_LEVEL)
            listing.rejection_reason = reason
            stats.rejections.record(reason, listing.id)
            continue

        stats.valued_flag_eligible += 1

        # Cheap cards clear a higher percentage bar and a lower dollar bar.
        # A flat dollar floor is the wrong shape at both ends: $10 excluded a
        # $4 card worth $12, while being trivially met by anything expensive.
        required_pct = cfg.cheap_min_discount_pct if listing.is_cheap else cfg.discount_threshold_pct
        required_savings = (
            cfg.cheap_min_savings_dollars if listing.is_cheap else cfg.min_savings_dollars
        )

        if listing.pct_under_market < required_pct:
            listing.rejection_reason = reasons.Reason.BELOW_DISCOUNT_THRESHOLD
            stats.rejections.record(reasons.Reason.BELOW_DISCOUNT_THRESHOLD, listing.id)
            continue
        if listing.dollar_savings < required_savings:
            listing.rejection_reason = reasons.Reason.BELOW_MIN_SAVINGS
            stats.rejections.record(reasons.Reason.BELOW_MIN_SAVINGS, listing.id)
            continue

        listing.is_opportunity = True
        stats.opportunities_reported += 1


def apply_dedupe(listings, seen, today_str, stats) -> list:
    """Suppress opportunities already reported at the same or a higher price,
    and mark genuine price drops. Everything else (auctions, target hits,
    needs-review) passes through untouched -- deduping those would hide an
    auction that's still live, which is the opposite of useful.
    """
    kept = []
    for listing in listings:
        if not listing.is_opportunity:
            kept.append(listing)
            continue

        prior = seen.get(listing.id)
        if prior is None:
            dedupe.record_flagged(listing.id, listing.price, seen, today_str)
            kept.append(listing)
            continue

        if listing.price < prior["price"]:
            listing.is_price_drop = True
            listing.previous_price = prior["price"]
            stats.price_drops += 1
            dedupe.record_flagged(listing.id, listing.price, seen, today_str)
            kept.append(listing)
            continue

        listing.is_opportunity = False
        listing.rejection_reason = reasons.Reason.PRICE_NOT_DROPPED
        stats.rejections.record(reasons.Reason.PRICE_NOT_DROPPED, listing.id)
        stats.duplicates_suppressed += 1
        # evaluate_listings already counted this as an opportunity; dedupe
        # is taking it back, so the "every listing is accounted for"
        # invariant has to stay true through this stage too.
        stats.opportunities_reported -= 1
        kept.append(listing)
    return kept


def build_craigslist_links(cfg, players) -> dict:
    return {
        player: craigslist_links.search_url(f"{player} card", cfg.craigslist_site, cfg.craigslist_category)
        for player in players
    }


def build_search_suggestions(cfg, listings) -> dict:
    """Saved searches worth adding, for the players where today's data shows
    no sign of coverage. See src/search_terms.py for why this matters:
    graded cards are about 1% of everything observed so far, and set_name
    resolves for about a sixth of listings.

    What is recorded here is what makes a suggestion stop being suggested, so
    under-recording means being nagged forever to create a search you already
    have. Every dimension the generator suggests on has to be recorded back:
    the grader (by name -- "psa" alone marked a BGS slab as PSA coverage),
    the grade, the set, and the auto/numbered attributes.

    Still only ever evidence of ABSENCE. eBay's alert emails do not say which
    saved search produced a listing, so a match here means "something arrived
    that this search would have found", never "you have this search".
    """
    observed = defaultdict(set)
    for listing in listings:
        seen = observed[listing.player]
        if listing.card_type == "graded":
            if listing.grader:
                seen.add(listing.grader.lower())
                if listing.grade:
                    seen.add("{} {}".format(listing.grader, listing.grade).lower())
            else:
                # A slab we could not read the label on is still evidence that
                # graded listings are reaching us for this player.
                seen.add("psa")
        identity = listing.card_identity
        if identity is None:
            continue
        if identity.is_autograph.value:
            seen.add("auto")
        if identity.set_name.value:
            seen.add(identity.set_name.value.lower())
        if identity.print_run.value is not None or identity.serial_number.value is not None:
            seen.add("/99")
        if identity.parallel.value:
            seen.add(identity.parallel.value.lower())
    return search_terms.coverage_gaps(cfg.players, observed)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _scanned_but_unreported(history, last_run_path, today_str) -> Optional[int]:
    """How many of the days missing from the run marker the corpus covers.

    A day in here was scanned and only the email failed, so its listings are
    on disk and still count towards comps. A day NOT in here was never
    looked at, and eBay's alert emails do not look back far enough to get it
    later. The report says the two differently because the reader can act on
    one of them and not the other.
    """
    last = run_marker.last_run_date(last_run_path)
    if last is None:
        return None
    seen = price_history.observed_dates(history)
    return sum(1 for date in seen if last < date < today_str)


def run(args: argparse.Namespace) -> None:
    logger.info("Starting daily card deal scan (dry_run=%s)", args.dry_run)

    cfg = load_config()
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")
    stats = observability.RunStats()
    # Read BEFORE the run writes its own marker, or the answer is always 0.
    stats.days_since_last_run = run_marker.gap_days(cfg.last_run_path, today_str)

    ebay_api_enabled = bool(cfg.ebay_client_id and cfg.ebay_client_secret)
    ebay_data_available = ebay_api_enabled or cfg.ebay_alerts_enabled
    listings = []
    observations = []
    history = None

    if ebay_api_enabled:
        token = ebay_client.get_app_token(cfg.ebay_client_id, cfg.ebay_client_secret)
        logger.info("Fetching eBay active listings for %d players", len(cfg.players))
        listings = fetch_ebay_active(cfg, cfg.players, token, stats)
        logger.info("Fetching eBay sold comps for %d players", len(cfg.players))
        observations = fetch_ebay_sold_observations(cfg, cfg.players, token)
        logger.info("%d real sold observation(s) available", len(observations))
        # Marketplace Insights access is normally declined, in which case the
        # sold list is empty. Today's active listings still give the engine
        # something to work with -- as asking-basis observations, which are
        # capped at "medium" confidence and can only reach a flag-eligible
        # level when the card is fully identified.
        observations.extend(listings_as_observations(listings, today_str))

    elif cfg.ebay_alerts_enabled:
        logger.info("eBay API unavailable -- using saved-search email alerts (IMAP) instead")
        listings = fetch_ebay_alert_active(cfg, stats)
        logger.info("Matched %d listing(s) from eBay alert emails", len(listings))

        # Flag truncated titles FIRST -- a grade we can't trust has to be
        # known before valuation, not after.
        mark_truncated_titles(listings)

        history = price_history.load(cfg.ebay_alert_price_history_path)
        recorded = record_observations(listings, history, today_str)
        logger.info("Recorded %d asking-price observation(s) (auctions and blocked listings excluded)", recorded)
        history = price_history.prune_old(history, cfg.ebay_alert_price_history_max_age_days, today)
        # Read before this run records anything, so today is not counted as
        # one of the days that went unreported. See observed_dates: a gap in
        # the run marker means "no email went out", which is not the same as
        # "nothing was scanned" now that the corpus is saved before the send.
        stats.scanned_but_unreported_days = _scanned_but_unreported(
            history, cfg.last_run_path, today_str
        )
        observations = price_history.deduped_observations(history)

    else:
        logger.warning(
            "Neither the eBay API nor eBay email alerts are configured -- skipping eBay entirely, "
            "sending Craigslist links only"
        )

    # Hand-entered sold prices go in alongside everything else. They are the
    # only real transactions in the corpus -- every other observation is a
    # seller's asking price -- so they are the only thing that can reach
    # "high" confidence, and the concentration gate exempts them (three real
    # sales on one day are three real sales). Usually an empty list: most
    # runs have none, and that is fine.
    sold = sold_comps.load(cfg.sold_comps_path)
    if sold:
        observations = list(observations) + sold
    stats.sold_comps_summary = sold_comps.summary(sold)
    logger.info("%s", stats.sold_comps_summary)

    engine = comps.CompEngine(
        observations,
        min_comps_required=cfg.valuation_min_comps_required,
        today=today,
        half_life_days=cfg.valuation_half_life_days,
        stale_after_days=cfg.valuation_stale_after_days,
        max_dispersion=cfg.valuation_max_dispersion,
        mad_threshold=cfg.valuation_mad_threshold,
        min_distinct_comp_dates=cfg.valuation_min_distinct_comp_dates,
        min_comp_span_days=cfg.valuation_min_comp_span_days,
    )
    coverage = engine.coverage()
    logger.info(
        "Comp buckets: %s (from %d observation(s))",
        ", ".join("{} {}".format(count, level) for level, count in coverage.items()),
        len(observations),
    )
    if not any(coverage.get(level) for level in comps.FLAG_ELIGIBLE_LEVELS):
        stats.warn(
            "No comp bucket anywhere is strong enough to declare a deal from (needs an "
            "identified card at a known grade). Nothing can be flagged today -- that is the "
            "system being honest, not broken. See SEARCH COVERAGE below."
        )

    evaluate_listings(listings, engine, cfg, stats)

    seen = dedupe.load_seen(cfg.seen_listings_path)
    listings = apply_dedupe(listings, seen, today_str, stats)
    logger.info(
        "%d opportunity(ies) after dedupe; %d listing(s) not reported, top reasons: %s",
        stats.opportunities_reported,
        stats.rejections.total(),
        "; ".join(stats.rejections.summary_lines()[:3]) or "none",
    )

    model = report.build_model(
        listings,
        cfg.discount_threshold_pct,
        date.today(),
        build_craigslist_links(cfg, cfg.players),
        ebay_data_available,
        cfg.min_savings_dollars,
        stats=stats,
        search_suggestions=build_search_suggestions(cfg, listings),
        immediate_min_savings=cfg.immediate_alert_min_savings_dollars,
        immediate_min_discount_pct=cfg.immediate_alert_min_discount_pct,
        ending_soon_hours=cfg.auction_ending_soon_hours,
        focus_rules=cfg.focus_rules,
        comp_requests_list=comp_requests.build_requests(
            listings, sold, min_comps_required=cfg.valuation_min_comps_required
        ),
        unidentified_listings=comp_requests.unidentified_count(listings),
        cheap_find_ceiling=cfg.cheap_find_ceiling,
        cheap_auction_floor=cfg.cheap_auction_floor,
        cheap_auction_ceiling=cfg.cheap_auction_ceiling,
    )
    body = report.render_text(model)
    html_body = report_html.render(model)
    subject = f"{cfg.email_subject_prefix} {model.subject}"

    if args.dry_run:
        # The text part, because that is the one a terminal can show. The
        # HTML part is still built above so a dry run exercises it -- a
        # renderer that only runs when email is actually being sent is a
        # renderer whose first failure happens in production.
        print(f"SUBJECT: {subject}\n\n{body}")
        logger.info("Dry run -- not sending email or updating state files")
        return

    # BEFORE the send, and deliberately not with the two state writes below.
    # The corpus is a record of what was OBSERVED, and today's alert emails
    # were observed whether or not SMTP is up. Saving it after the send meant
    # a failed send threw the day's asking prices away: the alert emails only
    # look back ebay_alerts_lookback_days (a couple of days), so if the
    # recovery run also misses that window those prices are gone for good and
    # the comp corpus has a permanent hole. The workflow's persist step runs
    # `if: always()`, so a corpus written here is still committed back when
    # the run later fails.
    if history is not None:
        price_history.save(cfg.ebay_alert_price_history_path, history)

    emailer.send_email(subject, body, cfg.gmail_address, cfg.gmail_app_password,
                       cfg.email_to, html_body=html_body)

    # AFTER the send, and it must stay after -- do not "tidy" this back
    # together with the corpus save above. The seen file means "I have
    # already TOLD you about this listing", not "I saw this listing". A send
    # that raised told the user nothing, so marking these seen would suppress
    # them from tomorrow's email and the user would never hear about them.
    seen = dedupe.prune_old(seen, cfg.prune_after_days, today)
    dedupe.save_seen(cfg.seen_listings_path, seen)
    # Last, and only on the path where the email actually went out -- also
    # not to be merged with the corpus save above. The marker's one job is to
    # answer "did today's scan complete", and the 17:00 backup run keys off
    # it via --skip-if-ran-today. Writing it before the send would let a
    # failed send record a run that never reached anybody, and the backup run
    # would then skip the one day it exists for.
    run_marker.save(cfg.last_run_path, today_str, len(listings))
    logger.info("Done")


_TRACE = ""


def _notify_failure(trace: str = "") -> None:
    global _TRACE
    _TRACE = (trace or traceback.format_exc() or "").strip()[:12000]
    """Best-effort failure email so a crashed run doesn't fail silently --
    "never go silent" applies to errors too, not just quiet days."""
    try:
        cfg = load_config()
    except Exception:
        logger.error("Can't send a failure email either -- config itself failed to load")
        return
    try:
        emailer.send_email(
            subject=f"{cfg.email_subject_prefix} Scan FAILED -- traceback below",
            body=(
                "Today's card deal scan crashed with an unhandled error and did not "
                "complete. The traceback follows -- it travels in this email because "
                "it may be the only copy that survives. This runs on GitHub "
                "Actions, where logs/scraper.log lives on a runner that is "
                "destroyed when the job ends.\n\n"
                + (_TRACE or "(no traceback available)")
            ),
            gmail_address=cfg.gmail_address,
            gmail_app_password=cfg.gmail_app_password,
            to_address=cfg.email_to,
        )
    except Exception:
        logger.exception("Failure-notification email itself failed to send")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the report instead of emailing it; don't write state files"
    )
    parser.add_argument(
        "--skip-if-ran-today",
        action="store_true",
        help="Exit without doing anything if a scan already completed today. For the "
        "backup scheduled run -- GitHub drops scheduled workflows under load, and a "
        "dropped run is the one failure nothing else here can see.",
    )
    args = parser.parse_args()

    setup_logging()

    try:
        if args.skip_if_ran_today:
            from src.config import ROOT_DIR as _root

            marker = _root / "data" / "last_run.json"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if run_marker.ran_on(marker, today):
                logger.info("A scan already completed on %s -- nothing to do.", today)
                return
            logger.info("No scan recorded for %s yet -- running the backup scan.", today)
        run(args)
    except Exception:
        logger.exception("Card deal scan failed with an unhandled error")
        if not args.dry_run:
            _notify_failure(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
