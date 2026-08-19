"""Daily entry point: scan eBay for the watchlist, flag underpriced
listings against comp medians, dedupe against prior runs, and email a
ranked report (plus ready-to-click Craigslist search links -- see
craigslist_links.py for why Craigslist isn't scraped automatically).

Run manually:   python -m src.main
Dry run (no email sent, no dedupe file written):   python -m src.main --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import comps, craigslist_links, dedupe, ebay_client, ebay_email_alerts, emailer, matcher, price_history, report
from src.config import ROOT_DIR, load_config
from src.models import Listing

LOG_PATH = ROOT_DIR / "logs" / "scraper.log"

# Caps scraper.log at ~2MB, keeping 5 rotated backups (scraper.log.1 .. .5)
# so a script that runs once a day forever doesn't grow the log unbounded.
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 5


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[file_handler, logging.StreamHandler(sys.stdout)],
        force=True,
    )


def fetch_ebay_active(cfg, players: list[str], token: str) -> list[Listing]:
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
            title = item.get("title", "")
            matched_player = matcher.match_player(title, [player])
            if not matched_player:
                continue
            card_type, grader, grade = matcher.detect_grading(title)
            price = ebay_client.extract_price(item)
            if price is None:
                continue
            listings.append(
                Listing(
                    id=item.get("itemId", item.get("itemWebUrl", title)),
                    source="ebay",
                    title=title,
                    price=price,
                    url=item.get("itemWebUrl", ""),
                    player=matched_player,
                    card_type=card_type,
                    grader=grader,
                    grade=grade,
                )
            )
    return listings


def fetch_ebay_sold_buckets(cfg, players: list[str], token: str) -> dict[tuple[str, str], list[float]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
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
            card_type, _, _ = matcher.detect_grading(title)
            price = ebay_client.extract_price(item)
            if price is not None:
                buckets[(player, card_type)].append(price)
    return buckets


def active_price_buckets(active_listings: list[Listing]) -> dict[tuple[str, str], list[float]]:
    """Used only as the comps fallback when real sold comps aren't available."""
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for listing in active_listings:
        if listing.price is not None:
            buckets[(listing.player, listing.card_type)].append(listing.price)
    return buckets


def fetch_ebay_alert_active(cfg, today_str: str) -> list[Listing]:
    """eBay-via-email-alerts path (see ebay_email_alerts.py). Returns
    matched Listings; also records every observed price into the
    self-building comp history as a side effect, since that's the only
    comp signal available on this path.
    """
    items = ebay_email_alerts.fetch_alert_listings(
        cfg.gmail_address, cfg.gmail_app_password, cfg.ebay_alerts_sender_contains, cfg.ebay_alerts_lookback_days
    )

    listings = []
    for item in items:
        matched_player = matcher.match_player(item["title"], cfg.players)
        if not matched_player or item["price"] is None:
            continue
        card_type, grader, grade = matcher.detect_grading(item["title"])
        listings.append(
            Listing(
                id=item["url"],
                source="ebay-alert",
                title=item["title"],
                price=item["price"],
                url=item["url"],
                player=matched_player,
                card_type=card_type,
                grader=grader,
                grade=grade,
            )
        )
    return listings


def enrich_truncated_grades(candidate_deals: list[Listing]) -> None:
    """eBay's alert emails truncate long titles, which can cut a grade
    number mid-digit (a "PSA 10" showing as "PSA 1..."). For the (small)
    set of listings that already cleared the deal threshold, try fetching
    the real title from the item page; if that fails for any reason, mark
    the grade as uncertain instead of showing a possibly-wrong number.
    Mutates candidate_deals in place. No-op for listings that aren't
    graded or don't look truncated -- keeps this occasional, not
    high-volume. See ebay_email_alerts.fetch_full_title's docstring for
    why this may not work at all (untested against eBay's real site).
    """
    logger = logging.getLogger("main")
    checked = fixed = 0
    for deal in candidate_deals:
        if deal.card_type != "graded" or not ebay_email_alerts.looks_truncated(deal.title):
            continue
        checked += 1
        full_title = ebay_email_alerts.fetch_full_title(deal.url)
        if full_title:
            deal.title = full_title
            _, deal.grader, deal.grade = matcher.detect_grading(full_title)
            fixed += 1
        else:
            deal.title_truncated = True
    if checked:
        logger.info("Truncated-grade recovery: %d/%d listing(s) fixed via full-title fetch", fixed, checked)


def build_craigslist_links(cfg, players: list[str]) -> dict[str, str]:
    return {
        player: craigslist_links.search_url(f"{player} card", cfg.craigslist_site, cfg.craigslist_category)
        for player in players
    }


def flag_deals(
    active_listings: list[Listing],
    comp_table: dict[tuple[str, str], comps.CompStats],
    threshold_pct: float,
) -> list[Listing]:
    flagged = []
    for listing in active_listings:
        stats = comp_table.get((listing.player, listing.card_type))
        if stats is None or listing.price is None:
            continue
        listing.comp_median = stats.median
        listing.comp_sample_size = stats.sample_size
        listing.comp_is_fallback = stats.is_fallback
        pct_under = (stats.median - listing.price) / stats.median * 100
        listing.pct_under_market = pct_under
        if pct_under >= threshold_pct:
            flagged.append(listing)
    return flagged


def run(args: argparse.Namespace) -> None:
    logger = logging.getLogger("main")
    logger.info("Starting daily card deal scan (dry_run=%s)", args.dry_run)

    cfg = load_config()
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")

    ebay_api_enabled = bool(cfg.ebay_client_id and cfg.ebay_client_secret)
    ebay_data_available = ebay_api_enabled or cfg.ebay_alerts_enabled
    candidate_deals: list[Listing] = []

    if ebay_api_enabled:
        token = ebay_client.get_app_token(cfg.ebay_client_id, cfg.ebay_client_secret)

        logger.info("Fetching eBay active listings for %d players", len(cfg.players))
        ebay_active = fetch_ebay_active(cfg, cfg.players, token)

        logger.info("Fetching eBay sold comps for %d players", len(cfg.players))
        sold_buckets = fetch_ebay_sold_buckets(cfg, cfg.players, token)

        fallback_buckets = active_price_buckets(ebay_active)
        comp_table = comps.build_comp_table(sold_buckets, cfg.ebay_min_comps_required, fallback_buckets)
        logger.info("Built comps for %d (player, card_type) buckets", len(comp_table))

        candidate_deals = flag_deals(ebay_active, comp_table, cfg.discount_threshold_pct)
        logger.info("%d listing(s) clear the %.0f%% discount threshold", len(candidate_deals), cfg.discount_threshold_pct)

    elif cfg.ebay_alerts_enabled:
        logger.info("eBay API unavailable -- using saved-search email alerts (IMAP) instead")
        ebay_active = fetch_ebay_alert_active(cfg, today_str)
        logger.info("Matched %d listing(s) from eBay alert emails", len(ebay_active))

        history = price_history.load(cfg.ebay_alert_price_history_path)
        for listing in ebay_active:
            price_history.record(history, listing.player, listing.card_type, listing.price, today_str)
        history = price_history.prune_old(history, cfg.ebay_alert_price_history_max_age_days, today)

        comp_table = comps.build_comp_table(
            sold_by_bucket={},
            min_comps_required=cfg.ebay_min_comps_required,
            fallback_by_bucket=price_history.as_buckets(history),
        )
        logger.info("Built comps for %d (player, card_type) buckets from accumulated alert history", len(comp_table))

        candidate_deals = flag_deals(ebay_active, comp_table, cfg.discount_threshold_pct)
        logger.info("%d listing(s) clear the %.0f%% discount threshold", len(candidate_deals), cfg.discount_threshold_pct)

        enrich_truncated_grades(candidate_deals)

        if not args.dry_run:
            price_history.save(cfg.ebay_alert_price_history_path, history)

    else:
        logger.warning(
            "Neither the eBay API nor eBay email alerts are configured -- skipping eBay entirely, "
            "sending Craigslist links only"
        )

    cl_links = build_craigslist_links(cfg, cfg.players)

    seen = dedupe.load_seen(cfg.seen_listings_path)
    new_deals = []
    for deal in candidate_deals:
        if dedupe.is_new_or_price_drop(deal.id, deal.price, seen):
            new_deals.append(deal)
            dedupe.record_flagged(deal.id, deal.price, seen, today_str)
    logger.info("%d deal(s) are new or price-dropped since last run", len(new_deals))

    subject, body = report.build_report(new_deals, cfg.discount_threshold_pct, date.today(), cl_links, ebay_data_available)
    subject = f"{cfg.email_subject_prefix} {subject}"

    if args.dry_run:
        print(f"SUBJECT: {subject}\n\n{body}")
        logger.info("Dry run -- not sending email or updating dedupe file")
        return

    emailer.send_email(subject, body, cfg.gmail_address, cfg.gmail_app_password, cfg.email_to)

    seen = dedupe.prune_old(seen, cfg.prune_after_days, today)
    dedupe.save_seen(cfg.seen_listings_path, seen)
    logger.info("Done")


def _notify_failure() -> None:
    """Best-effort failure email so a crashed cron run doesn't fail silently
    -- mirrors the "send something, not silence" rule that already applies
    to the zero-deals case, extended to actual errors.
    """
    logger = logging.getLogger("main")
    try:
        cfg = load_config()
    except Exception:
        logger.error("Can't send a failure email either -- config itself failed to load")
        return
    try:
        emailer.send_email(
            subject=f"{cfg.email_subject_prefix} Scan FAILED -- check logs/scraper.log",
            body=(
                "Today's card deal scan crashed with an unhandled error and did not "
                "complete. See logs/scraper.log on the machine that ran it for the "
                "full traceback."
            ),
            gmail_address=cfg.gmail_address,
            gmail_app_password=cfg.gmail_app_password,
            to_address=cfg.email_to,
        )
    except Exception:
        logger.exception("Failure-notification email itself failed to send")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print the report instead of emailing it; don't touch the dedupe file")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("main")

    try:
        run(args)
    except Exception:
        logger.exception("Card deal scan failed with an unhandled error")
        if not args.dry_run:
            _notify_failure()
        raise


if __name__ == "__main__":
    main()
