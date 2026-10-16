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
import dataclasses
import logging
import traceback
import sys
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import requests

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
    flips,
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
FLIPS_PATH = ROOT_DIR / "data" / "flips.csv"

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

#: The buying-format canary. eBay's alert template prints "N bids" under an
#: auction that has bids and prints nothing at all under a Buy It Now -- no
#: "Buy It Now" label exists in it (six real alerts, 2026-09-23) -- so a high
#: "unknown" share is the template's normal state, not a symptom. The alarm
#: that used to fire at 40% unknown therefore fired every single run, which
#: teaches the reader to skip the warnings block. What a template change
#: WOULD look like is the bid lines vanishing: in those six alerts roughly a
#: fifth of listings read as auctions. A run this big with none is broken.
NO_AUCTIONS_ALARM_MIN_LISTINGS = 50


def buying_format_warning(stats) -> Optional[str]:
    """The warning to raise when no listing in a large run read as an
    auction, else None. See NO_AUCTIONS_ALARM_MIN_LISTINGS."""
    total = stats.auctions + stats.fixed_price + stats.listing_type_unknown
    if total < NO_AUCTIONS_ALARM_MIN_LISTINGS or stats.auctions:
        return None
    return (
        "None of {} listings read as an auction. eBay's alerts print a bid count under "
        "every auction with bids, so zero means the parser has stopped seeing them -- and "
        "an auction it cannot see is recorded as an asking price, putting a CURRENT BID "
        "into the comp corpus. Run `python -m scripts.test_ebay_alerts --raw` to check "
        "whether eBay changed the markup.".format(total)
    )

# comps.CompMatch.blocked_reasons -> the canonical rejection reason.
BLOCKED_TO_REASON = {
    "context_only_level": reasons.Reason.CONTEXT_ONLY_LEVEL,
    "thin_sample": reasons.Reason.THIN_SAMPLE,
    "stale_comps": reasons.Reason.STALE_COMPS,
    "dispersed_comps": reasons.Reason.DISPERSED_COMPS,
    "concentrated_sample": reasons.Reason.CONCENTRATED_SAMPLE,
    "mixed_card_numbers": reasons.Reason.MIXED_CARD_NUMBERS,
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
                   listing_type="unknown", bid_count=None, time_left_text=None, has_best_offer=False,
                   search_query=None, listed_at=None):
    """One Listing with identity fully extracted. Returns None when no
    watchlist player is in the title or, failing that, in the saved search
    the listing matched -- the caller records the reason."""
    matched = matcher.match_players(title, players)
    from_search = False
    if not matched and search_query:
        # Measured 2026-09-23 over six real alerts: 64 of 79 search matches
        # arrived with the player's surname cut off ("Pete Crow-A...") and
        # were being dropped here, while eBay's recommendations -- full
        # titles -- all got through. eBay matches a saved search against
        # the full title, so a search naming exactly one watchlist player is
        # as good as the name. Two players in one search proves nothing.
        by_search = matcher.match_players(search_query, players)
        if len(by_search) == 1:
            matched, from_search = by_search, True
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
        listed_at=listed_at,
        has_best_offer=has_best_offer,
        negative_signals=tuple(identity.negative_signals.value or ()),
        matched_players=tuple(matched),
        player_from_search=from_search,
        search_query=search_query,
    )


def fetch_ebay_active(cfg, players, token, stats) -> list:
    """Active listings via the eBay Browse API (dormant unless credentials
    are set). Unlike the email path, Browse tells us the buying option
    outright, so listing_type is known rather than inferred, and its titles
    are never truncated.

    Two passes per player. Newest first, because a daily scan's job is what
    appeared since yesterday and best match buries it: on 2026-10-15 every
    player filled a 200-item best-match page, with as few as 3 auctions in
    it. Then soonest-ending auctions, because those are what the auction
    sections act on and newest-first reaches them only on the day they were
    listed. The same item from both passes is one listing."""
    listings = []
    seen_ids = set()
    capped = []
    failed = {}  # player -> first failure, for one warning in the email
    searches = failed_searches = 0
    searches_by_player, failures_by_player = {}, {}
    now = datetime.now(timezone.utc)
    passes = [
        # A floor on the newest pass only: measured 2026-10-16, 77% of a
        # newest page was under $20, a band where no flip clears the $15
        # net floor -- it filled the 200-item page (hiding the rest of the
        # day's listings) and grew the corpus ~3k rows a day. The auction
        # pass keeps every price: a cheap current bid is the point of it.
        dict(sort="newlyListed", max_pages=cfg.ebay_newest_pages_per_player,
             min_price=cfg.ebay_newest_min_price),
        dict(sort="endingSoonest", buying_options=("AUCTION",), max_pages=cfg.ebay_auction_pages_per_player),
    ]
    for player in players:
        for search in passes:
            if search["max_pages"] < 1:
                continue
            failures = []
            items = ebay_client.search_active_listings(
                query=player,
                token=token,
                category_id=cfg.ebay_category_id,
                marketplace_id=cfg.ebay_marketplace_id,
                limit=cfg.ebay_active_listing_limit,
                failures=failures,
                **search,
            )
            searches += 1
            searches_by_player[player] = searches_by_player.get(player, 0) + 1
            if failures:
                failed_searches += 1
                failures_by_player[player] = failures_by_player.get(player, 0) + 1
                failed.setdefault(player, failures[0])
            if search["sort"] == "newlyListed" and len(items) >= cfg.ebay_active_listing_limit * search["max_pages"]:
                capped.append(player)
            for item in items:
                listing_id = _browse_listing_id(item)
                if listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)
                stats.listings_extracted += 1
                title = item.get("title", "")
                buying_options = item.get("buyingOptions") or []
                listing = _build_listing(
                    cfg,
                    listing_id=listing_id,
                    source="ebay",
                    title=title,
                    price=ebay_client.extract_listing_price(item),
                    url=listing_id if listing_id.startswith("http") else item.get("itemWebUrl", ""),
                    players=[player],
                    shipping_price=ebay_client.extract_shipping(item),
                    listing_type=_browse_listing_type(buying_options),
                    bid_count=item.get("bidCount"),
                    time_left_text=ebay_client.time_left_text(item, now),
                    has_best_offer="BEST_OFFER" in buying_options,
                    # itemOriginDate survives a relist; itemCreationDate is
                    # reset by one, so it is only the fallback.
                    listed_at=item.get("itemOriginDate") or item.get("itemCreationDate"),
                )
                if listing is None:
                    stats.rejections.record(reasons.Reason.NO_PLAYER_MATCH)
                    continue
                listings.append(listing)
    if failed:
        # A non-200 used to reach only the log, so a player whose search
        # failed looked exactly like a player with nothing new today.
        # Every search refused is a 401/429 storm: the run did not work.
        # One pass of several is a partial gap, and says only that.
        whole = all(failures_by_player[p] == searches_by_player[p] for p in failed)
        stats.warn(
            "The eBay API refused {} of {} searches, for {} player(s) ({}), so {} from "
            "today's report. First error: {}".format(
                failed_searches, searches, len(failed),
                ", ".join(list(failed)[:5]) + (" and more" if len(failed) > 5 else ""),
                "their listings are missing" if whole else "some listings may be missing",
                next(iter(failed.values())),
            ),
            broken=failed_searches == searches,
        )
    if capped:
        # Newest first, so a full walk means the oldest of today's new
        # listings fell off the end -- the alert path's capped-search
        # warning, for the API.
        stats.warn(
            "The eBay API returned its full {} newest listings for {} player(s) ({}), so some "
            "of the past day's new listings were not reached. Raise ebay.newest_pages_per_player "
            "in config/settings.json to see further back.".format(
                cfg.ebay_active_listing_limit * cfg.ebay_newest_pages_per_player,
                len(capped), ", ".join(capped[:5]) + (" and more" if len(capped) > 5 else ""),
            )
        )
    return listings


def _browse_listing_id(item: dict) -> str:
    """The same canonical item URL the alert path uses as its id, so one item
    seen by both sources is one listing in dedupe and one row in the corpus.
    Browse's own itemId ("v1|1234|0") would fork both."""
    legacy = str(item.get("legacyItemId") or "")
    if legacy.isdigit():
        return ebay_email_alerts._item_url(legacy)
    return (
        ebay_email_alerts._find_item_url(item.get("itemWebUrl") or "")
        or item.get("itemId")
        or item.get("itemWebUrl")
        or item.get("title", "")
    )


#: GitHub warns on a pushed file over 50MB and refuses one over 100MB. The
#: corpus is committed after every run, so the warning fires well before
#: either: a refused push means the day's observations are lost.
CORPUS_WARN_MB = 40


def corpus_size_warning(path: Path) -> Optional[str]:
    """A footer warning when the committed corpus is getting too big for
    GitHub, else None. The alert path alone grew it about 80KB a day; the
    Browse API can add that many rows in one run."""
    try:
        size_mb = path.stat().st_size / 1_000_000
    except OSError:
        return None
    if size_mb < CORPUS_WARN_MB:
        return None
    return (
        "The comp corpus ({}) is {:.0f}MB. GitHub refuses a pushed file over 100MB, after which "
        "the daily save fails and each day's observations are lost. Lower "
        "ebay_alerts.price_history_max_age_days or ebay.newest_pages_per_player in "
        "config/settings.json.".format(path.name, size_mb)
    )


def merge_sources(api_listings, alert_listings) -> tuple[list, str]:
    """(one list with each item once, a footer line saying where they came
    from). An item both sources saw keeps the API copy: its title is whole,
    so its grade and set can be trusted, and its buying format is stated
    rather than guessed from email markup."""
    api_ids = {listing.id for listing in api_listings}
    both = sum(1 for listing in alert_listings if listing.id in api_ids)
    merged = list(api_listings) + [listing for listing in alert_listings if listing.id not in api_ids]
    summary = (
        "Sources: {} from the eBay API, {} from alert emails, {} in both "
        "(the API copy is kept)".format(len(api_listings), len(alert_listings), both)
    )
    return merged, summary


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

    if items:
        # A price is the one thing every downstream stage needs: no price,
        # no valuation, no cheap find, no auction, nothing in the email but a
        # quiet "no opportunities today". That is exactly what a live run
        # sent for four days straight while the parser was reading the photo
        # link and finding the price beside the title link -- a failure that
        # looked identical to a slow market. It is measured now, and a total
        # loss is an alarm rather than a silence.
        priced = sum(1 for item in items if item.get("price") is not None)
        logger.info(
            "Prices: %d of %d listing(s) had a readable price (%.0f%%)",
            priced, len(items), 100.0 * priced / len(items),
        )
        if priced <= len(items) // 10:
            stats.warn(
                "Only {} of {} listing(s) in eBay's alert emails had a readable price. "
                "Nothing without one can be valued, so today's email is near-empty "
                "because the parser stopped reading eBay's template, not because the "
                "market was quiet.".format(priced, len(items)),
                broken=True,
            )

    if counters.get("fetch_failures"):
        stats.warn(
            "{} alert email(s) could not be read from the mailbox and were skipped, so "
            "the scanned count below understates what eBay actually sent.".format(
                counters["fetch_failures"]
            ),
            broken=True,
        )

    if counters.get("recommendations") or counters.get("probable_opening_bids"):
        logger.info(
            "Alert listings: %d of %d were eBay recommendations rather than search matches; "
            "%d unreadable-format listing(s) at $%.2f or less treated as opening bids",
            counters.get("recommendations", 0), len(items),
            counters.get("probable_opening_bids", 0), ebay_email_alerts.PROBABLE_OPENING_BID_MAX,
        )

    capped = counters.get("capped_searches")
    if capped:
        worst = sorted(capped.items(), key=lambda kv: -kv[1][0])
        listed = "; ".join(
            '"{}" ({:,} new, {} shown)'.format(
                # The exclusions are the same on every search; the terms
                # are what tell you which search it is.
                " ".join(word for word in query.split() if not word.startswith("-")),
                reported, shown,
            )
            for query, (reported, shown) in worst[:5]
        )
        more = " and {} more".format(len(worst) - 5) if len(worst) > 5 else ""
        stats.warn(
            "{} saved search(es) found more new listings than eBay's alert shows: {}{}. "
            "CardPro only ever sees what the email shows, so narrow these (add a set, "
            "grade or print run) or delete them.".format(len(worst), listed, more)
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
            search_query=item.get("search_query"),
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
        if listing.player_from_search:
            # Thirty characters of title with the player's name cut off: the
            # grade, set and parallel are mostly unreadable, and a "psa"
            # search's match whose title lost "PSA" would be written into the
            # RAW bucket at a slab's price. Shown in the report, never a comp.
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
            is_autograph=identity.is_autograph.value if identity else None,
            relic=_relic_of(identity),
            is_serial_numbered=identity.is_serial_numbered.value if identity else None,
            title_truncated=listing.title_truncated,
            listing_type=listing.listing_type,
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


def _total_cost_with_tax(cfg_listing, cfg) -> Optional[float]:
    """What actually leaves your account: price + known shipping + sales tax.

    One function so the three places that ask the question -- the deal gate
    (via economics.Acquisition), the focus ceiling and the target bands --
    cannot drift into three answers. None means the price could not be read,
    which is not the same as free.
    """
    if cfg_listing.price is None:
        return None
    return economics.Acquisition(
        price=cfg_listing.price,
        shipping=cfg_listing.shipping_price,
        sales_tax_pct=cfg.sales_tax_pct,
    ).total_cost


def _relic_of(identity) -> Optional[str]:
    """"patch" / "relic" / None -- the memorabilia half of the comp variant.

    A multi-colour patch is a different product from a jersey swatch, and
    both are different products from the base card, so they key different
    comp buckets. Kept next to the lookup that uses it rather than on
    CardIdentity, because it is a comp-engine vocabulary, not a fact about
    the card.
    """
    if identity is None:
        return None
    if identity.is_patch.value:
        return "patch"
    if identity.is_memorabilia.value:
        return "relic"
    return None


def _fee_model(cfg):
    return economics.FeeModel(
        marketplace_fee_pct=cfg.fee_marketplace_pct,
        marketplace_fixed_fee=cfg.fee_marketplace_fixed,
        payment_fee_pct=cfg.fee_payment_pct,
        outbound_shipping=cfg.outbound_shipping,
        supplies=cfg.supplies_cost,
        sales_tax_pct=cfg.sales_tax_pct,
        small_order_fixed_fee=cfg.fee_small_order_fixed,
        small_order_max=cfg.fee_small_order_max,
        envelope_shipping=cfg.outbound_shipping_envelope,
        envelope_max_sale=cfg.envelope_max_sale,
        graded_supplies=cfg.supplies_graded_cost,
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
            # The tax-inclusive cost, because a target threshold is a price
            # YOU set for what you are willing to pay, and what you pay
            # includes the tax. Listing.total_cost is the pre-tax figure the
            # report prints beside the item price; at the shipped
            # sales_tax_pct of 10.25 they differ by that much.
            total_cost=_total_cost_with_tax(listing, cfg),
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
            # An autograph, a patch and a print run change what the card IS,
            # not just what it is worth, and none of them appear anywhere in
            # the fields above -- see comps.printing_variant.
            #
            # `title_truncated` is deliberately NOT forwarded, and the
            # asymmetry is the point. Truncation is refused on the
            # OBSERVATION side (record_observations below stores it, and
            # comps.variant_of_observation turns it into "level does not
            # apply"), because a hidden "Auto /150" landing in the bucket of
            # base copies raises that bucket's median and makes every real
            # base card read as a deal -- the expensive direction. Read from
            # the other side it is the cheap direction: a listing whose cut
            # hid an autograph is compared against base copies, so its
            # market value is UNDERSTATED and any discount it shows is a
            # floor under the real one. The report already prints "eBay
            # truncated the title" as a risk on those. Refusing here as well
            # would cost a third of all valuations to prevent an error that
            # only ever errs in the user's favour.
            is_autograph=identity.is_autograph.value if identity else False,
            relic=_relic_of(identity),
            print_run=identity.print_run.value if identity else None,
            # Still refuses the level when the title says "numbered" and the
            # cut took the number: "one of something" is not comparable.
            is_serial_numbered=identity.is_serial_numbered.value if identity else False,
            # Lets a base card with a card number key the exact level as
            # parallel "Base" -- see comps._base_parallel.
            is_base=identity.is_base.value if identity else None,
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
        # An asking-price median overstates what the card sells for, so it
        # takes the bigger haircut; see settings.json economics._haircut_comment.
        haircut_pct = cfg.resale_haircut_asking_pct if listing.comp_is_fallback else cfg.resale_haircut_pct
        graded = listing.card_type == "graded"
        listing.economics = economics.evaluate(
            economics.Acquisition(
                price=listing.price,
                shipping=listing.shipping_price,
                sales_tax_pct=cfg.sales_tax_pct,
                # Unknown shipping costs something; $0 here inflated profit.
                assumed_shipping=cfg.assumed_inbound_shipping,
            ),
            match.stats.median,
            fees,
            resale_haircut_pct=haircut_pct,
            graded=graded,
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

        # The most you can pay for the ITEM and still net min_net_profit at the
        # haircut sale price, after fees, postage, supplies, tax and inbound
        # shipping. One number for every priced listing: the bid ceiling on
        # an auction, the max offer/buy on fixed price. It is the deal gate's
        # own floor solved for price, so a flagged card is always at or under
        # it. (It replaced a 30%-gross offer "maximum" that lost money on raw
        # cards, and a 25%-of-value auction margin; auctions.required_margin_pct
        # survives as an optional extra floor, shipped at 0.)
        #
        # ONLY off a comp CardPro will stand behind. A max bid is the one
        # number in this report that tells you to spend money -- "the most
        # you can pay. Above this, stop." -- and it was being computed from
        # whatever median was available, including a price-bracket bucket.
        # On the live corpus that put a $729.31 ceiling on a $125 Ernie Banks
        # card, two lines under a Market line reading "not established ...
        # CardPro has no valuation for this card". A number nobody may act on
        # must not come back wearing the one label that says act on it.
        #
        # It cuts the other way too: focus._has_bidding_room drops an
        # auction whose current bid is over this ceiling, so a context-only
        # median was silently removing real auctions from the email. With no
        # ceiling there is nothing to be over, and the report says plainly
        # that none could be computed.
        listing.max_rational_bid = economics.max_rational_bid(
            match.stats.median,
            required_margin_pct=cfg.auction_required_margin_pct if listing.is_auction else 0.0,
            min_profit_dollars=cfg.min_net_profit,
            # The same assumed postage evaluate() uses: treating unknown
            # as $0 overstates the ceiling by exactly that postage.
            shipping_in=(
                listing.shipping_price
                if listing.shipping_price is not None
                else cfg.assumed_inbound_shipping
            ),
            fees=fees,
            # The same haircut evaluate() applies. Without it the two
            # disagree about the same card, and the ceiling comes out
            # high -- which is the expensive direction to be wrong in.
            resale_haircut_pct=haircut_pct,
            graded=graded,
        ) if match.flag_eligible else None
        # Unknown shipping makes the ceiling an upper bound, not a
        # figure. The report has to be able to say so; the bare float
        # cannot carry that.
        listing.max_rational_bid_shipping_known = listing.shipping_price is not None

        if listing.is_auction:
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
        # The gates above are on GROSS discount, which ignores what fees,
        # postage, supplies and tax take out of a flip. Corpus replay on the
        # current fee model (measured 2026-10-16): 6 flags cleared them, 4 of
        # which netted under $15; with this check, 2 flags, 0 under. A
        # discount you cannot bank is not a deal.
        if listing.economics.expected_profit < cfg.min_net_profit:
            listing.rejection_reason = reasons.Reason.BELOW_MIN_NET_PROFIT
            stats.rejections.record(reasons.Reason.BELOW_MIN_NET_PROFIT, listing.id)
            continue

        listing.is_opportunity = True
        listing.deal_tier = economics.deal_tier(
            flag_eligible=match.flag_eligible,
            basis=match.stats.basis,
            sample_size=match.stats.sample_size,
            expected_profit=listing.economics.expected_profit,
            min_net_profit=cfg.min_net_profit,
        )
        listing.profit_per_hour = (
            listing.economics.expected_profit / (cfg.minutes_per_flip / 60.0)
            if cfg.minutes_per_flip > 0 else None
        )
        # The gate and the MAX use the median; the summary also shows what
        # selling at the cheap end of the comps would net, through the same
        # fee model, so "+$15" is never read as a floor it is not.
        listing.net_profit_low = economics.evaluate(
            economics.Acquisition(
                price=listing.price, shipping=listing.shipping_price,
                sales_tax_pct=cfg.sales_tax_pct, assumed_shipping=cfg.assumed_inbound_shipping,
            ),
            match.stats.minimum, fees, resale_haircut_pct=haircut_pct, graded=graded,
        ).expected_profit
        stats.opportunities_reported += 1


#: A card that is not a deal comes back only on a real price drop.
SHOWN_REPEAT_MIN_DROP_PCT = 5.0


def apply_dedupe(listings, seen, today_str, stats) -> list:
    """Suppress opportunities already reported at the same or a higher price,
    and mark genuine price drops.

    Every other card printed on an earlier day (browse sections, auctions,
    needs-review) is left out of today's report too unless its price fell
    by SHOWN_REPEAT_MIN_DROP_PCT: the same cool cards and cheap finds
    every morning buried the one new line worth reading. Target hits are
    always shown -- you named those cards. Left-out cards are counted in the
    footer, not dropped silently. What counts as "printed" is recorded after
    the report is built (record_shown).
    """
    kept = []
    for listing in listings:
        if not listing.is_opportunity:
            if (
                listing.target_hit is not None
                or listing.price is None
                or dedupe.is_new_or_price_drop(
                    listing.id, listing.price, seen, min_drop_pct=SHOWN_REPEAT_MIN_DROP_PCT
                )
            ):
                kept.append(listing)
            else:
                stats.previously_shown_suppressed += 1
            continue

        prior = seen.get(listing.id)
        if not dedupe.was_reported_as_deal(prior):
            # Never seen, or only ever shown as a browse card: a new deal.
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


def annotate_listing_age(listings, history, today) -> None:
    """age_days on every listing that can have one. A deal that has sat for
    three weeks is a different bet from one listed this morning: either
    nobody wants it at that price or the comps are wrong. eBay's own
    creation date when Browse gave one; otherwise the corpus's first-seen
    date (a row keeps its first-sighting date), which is a floor on the age.
    """
    first_seen = {row.get("id"): row.get("date") for rows in history.values() for row in rows}
    for listing in listings:
        listed = seen = None
        if listing.listed_at:
            try:
                listed = (today - datetime.fromisoformat(
                    str(listing.listed_at).replace("Z", "+00:00")).date()).days
            except ValueError:
                pass
        if first_seen.get(listing.id):
            try:
                seen = (today - date.fromisoformat(first_seen[listing.id])).days
            except ValueError:
                pass
        # The older of the two: a relist can carry a fresh eBay date for a
        # card the corpus has watched sit unsold for weeks.
        if seen is not None and (listed is None or seen > listed):
            listing.age_days, listing.age_basis = max(0, seen), "first seen"
        elif listed is not None:
            listing.age_days, listing.age_basis = max(0, listed), "listed"


def record_shown(listings, shown_ids, seen, today_str) -> None:
    """Mark every card the report actually printed as seen, at today's price.
    A card that was not a deal is marked shown-only, so it can still be
    reported as a deal later -- see dedupe.record_flagged."""
    for listing in listings:
        if listing.id in shown_ids and listing.price is not None:
            dedupe.record_flagged(
                listing.id, listing.price, seen, today_str, shown_only=not listing.is_opportunity
            )


def build_craigslist_links(cfg, players) -> dict:
    return {
        player: craigslist_links.search_url(f"{player} card", cfg.craigslist_site, cfg.craigslist_category)
        for player in players
    }


def build_search_suggestions(cfg, listings) -> dict:
    """Saved searches worth adding, for the players where today's data shows
    no sign of coverage. See src/search_terms.py for why this matters:
    graded cards are about 12% of everything observed so far, and the field
    that gates the only comp level allowed to declare a deal -- the card
    number -- resolves for 45%.

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

    api_listings, alert_listings, api_sold = [], [], []
    if ebay_api_enabled:
        try:
            token = ebay_client.get_app_token(cfg.ebay_client_id, cfg.ebay_client_secret)
            logger.info("Fetching eBay active listings for %d players", len(cfg.players))
            api_listings = fetch_ebay_active(cfg, cfg.players, token, stats)
            logger.info("Matched %d listing(s) from the eBay API", len(api_listings))
            if cfg.ebay_marketplace_insights_enabled:
                logger.info("Fetching eBay sold comps for %d players", len(cfg.players))
                api_sold = fetch_ebay_sold_observations(cfg, cfg.players, token)
                logger.info("%d real sold observation(s) available", len(api_sold))
        except requests.RequestException as exc:
            # Running beside the alerts, an API outage must not take them
            # down too: the run carries on with whatever the inbox holds.
            if not cfg.ebay_alerts_enabled:
                raise
            logger.exception("eBay API failed; continuing with alert emails only")
            api_listings, api_sold = [], []
            stats.warn(
                "The eBay API failed this run ({}), so today's listings come from the "
                "alert emails alone.".format(exc.__class__.__name__),
                broken=True,
            )

    if cfg.ebay_alerts_enabled:
        listings_from = "alongside the eBay API" if ebay_api_enabled else "(eBay API not configured)"
        logger.info("Reading eBay saved-search alert emails (IMAP) %s", listings_from)
        alert_listings = fetch_ebay_alert_active(cfg, stats)
        logger.info("Matched %d listing(s) from eBay alert emails", len(alert_listings))
        # Flag truncated titles FIRST -- a grade we can't trust has to be
        # known before valuation, not after. Alert titles only: Browse
        # titles are whole, and one that happens to end in "..." is not cut.
        mark_truncated_titles(alert_listings)

    if ebay_api_enabled and cfg.ebay_alerts_enabled:
        listings, stats.source_summary = merge_sources(api_listings, alert_listings)
        logger.info("%s", stats.source_summary)
    else:
        listings = api_listings or alert_listings

    if ebay_data_available:
        # One corpus for both sources. The API path used to value against
        # today's listings alone and keep nothing, so turning it on would
        # have thrown away the months of asking prices the alert path built.
        history = price_history.load(cfg.ebay_alert_price_history_path)
        corpus_warning = corpus_size_warning(cfg.ebay_alert_price_history_path)
        if corpus_warning:
            stats.warn(corpus_warning)
        recorded = record_observations(listings, history, today_str)
        annotate_listing_age(listings, history, today.date())
        logger.info("Recorded %d asking-price observation(s) (auctions and blocked listings excluded)", recorded)
        history = price_history.prune_old(history, cfg.ebay_alert_price_history_max_age_days, today)
        history, opening_bids = price_history.drop_probable_opening_bids(
            history, ebay_email_alerts.PROBABLE_OPENING_BID_MAX
        )
        if opening_bids:
            logger.info("Dropped %d stored row(s) that were probably opening bids", opening_bids)
        # Read before this run records anything, so today is not counted as
        # one of the days that went unreported. See observed_dates: a gap in
        # the run marker means "no email went out", which is not the same as
        # "nothing was scanned" now that the corpus is saved before the send.
        stats.scanned_but_unreported_days = _scanned_but_unreported(
            history, cfg.last_run_path, today_str
        )
        observations = price_history.deduped_observations(history) + api_sold

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
    # Your own sold flips (data/flips.csv) are sold comps too -- see src/flips.py.
    flip_rows = flips.load(FLIPS_PATH)
    sold = sold_comps.load(cfg.sold_comps_path) + flips.sold_observations(flip_rows, cfg.players)
    stats.flips_summary = flips.summary(flip_rows) or ""
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

    format_warning = buying_format_warning(stats)
    if format_warning:
        # Not marked broken=True: the run's other numbers are still sound,
        # and an auction read as fixed-price is a corpus problem that shows
        # up over weeks rather than a report that is wrong this morning.
        stats.warn(format_warning)

    seen = dedupe.load_seen(cfg.seen_listings_path)
    flips.mark_seen(flip_rows, seen, today_str)
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
            listings, sold, min_comps_required=cfg.valuation_min_comps_required,
            max_price=lambda median, graded: economics.max_rational_bid(
                median, required_margin_pct=0.0, min_profit_dollars=cfg.min_net_profit,
                shipping_in=cfg.assumed_inbound_shipping, fees=_fee_model(cfg),
                resale_haircut_pct=cfg.resale_haircut_asking_pct, graded=graded,
            ),
        ),
        unidentified_listings=comp_requests.unidentified_count(listings),
        cheap_find_ceiling=cfg.cheap_find_ceiling,
        cheap_auction_floor=cfg.cheap_auction_floor,
        cheap_auction_ceiling=cfg.cheap_auction_ceiling,
    )
    alert_only = getattr(args, "alert_only", False)
    if not alert_only:
        record_shown(listings, model.shown_ids, seen, today_str)
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

    if alert_only:
        # An intra-day poll: the corpus above is always kept, but email goes
        # out only for an ACT NOW, and only the ACT NOW cards are marked seen
        # -- read fresh, because apply_dedupe has already marked every new
        # opportunity in `seen`, and those were not sent. No run marker: the
        # daily digest and its --skip-if-ran-today backup key off it.
        if not model.act_now_ids:
            logger.info("Alert-only poll: nothing in ACT NOW -- no email")
            return
        if not subject.startswith(cfg.email_subject_prefix + " ACT NOW"):
            subject = "{} ACT NOW: {}".format(cfg.email_subject_prefix, model.subject)
        # Only the ACT NOW section: everything else stays for the digest,
        # which is the only place the browse sections get marked as seen.
        alert_model = dataclasses.replace(
            model, lede=None, focus_line=None,
            sections=OrderedDict((key, cards) for key, cards in model.sections.items()
                                 if key == report.SECTION_ACT_NOW),
        )
        emailer.send_email(subject, report.render_text(alert_model), cfg.gmail_address,
                           cfg.gmail_app_password, cfg.email_to, html_body=report_html.render(alert_model))
        sent = dedupe.load_seen(cfg.seen_listings_path)
        record_shown(listings, model.act_now_ids, sent, today_str)
        dedupe.save_seen(cfg.seen_listings_path, sent)
        logger.info("Alert-only poll: emailed %d ACT NOW card(s)", len(model.act_now_ids))
        return

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
    # answer "did today's scan complete", and the 19:00 backup run keys off
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
    parser.add_argument(
        "--alert-only",
        action="store_true",
        help="Intra-day poll: save the corpus, email only if ACT NOW is non-empty, mark only "
        "those cards seen, and leave the run marker alone so the daily digest still runs.",
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
