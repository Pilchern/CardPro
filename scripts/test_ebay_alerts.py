"""Standalone check for the eBay-via-email-alerts path -- doesn't touch
the eBay API or send any email. Reads real eBay saved-search alert emails
out of your Gmail inbox over IMAP and prints what it can extract from
them: title, price, link, matched watchlist player, detected grading.

Prerequisite (one-time, on eBay's site, not in this repo): for each
watchlist player, create a saved search on eBay and turn on email alerts
for it. See README "eBay via saved-search email alerts".

This is the script to run to sanity-check (and if needed, help debug)
extract_listings_from_html() in src/ebay_email_alerts.py against a real
alert email -- that parsing logic is provisional until validated this way.

Usage:
    python -m scripts.test_ebay_alerts
    python -m scripts.test_ebay_alerts --raw       # also dump raw extracted listings before player-matching
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src import card_identity, ebay_email_alerts, matcher
from src.config import CONFIG_DIR, ROOT_DIR


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")

    missing = [name for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing .env values: {', '.join(missing)}. Copy .env.example to .env and fill in Gmail.")

    with open(CONFIG_DIR / "watchlist.json") as f:
        players = json.load(f)["players"]
    with open(CONFIG_DIR / "settings.json") as f:
        alert_settings = json.load(f)["ebay_alerts"]

    show_raw = "--raw" in sys.argv

    mailbox = alert_settings.get("mailbox", ebay_email_alerts.DEFAULT_MAILBOX)
    print(
        f"Checking Gmail ({mailbox}) for eBay alert emails "
        f"(from contains {alert_settings['sender_contains']!r}, last {alert_settings['lookback_days']} day(s))...\n"
    )

    messages = ebay_email_alerts.fetch_alert_messages(
        os.environ["GMAIL_ADDRESS"],
        os.environ["GMAIL_APP_PASSWORD"],
        alert_settings["sender_contains"],
        alert_settings["lookback_days"],
        mailbox,
    )
    print(f"Found {len(messages)} matching email(s).\n")

    if not messages:
        print(
            "No matching emails found. Either no alerts have arrived yet, or "
            "sender_contains in config/settings.json's ebay_alerts section doesn't "
            "match the real sender address -- check what address the alert came "
            "from and adjust if needed."
        )
        return

    all_listings = []
    for i, msg in enumerate(messages, start=1):
        html = ebay_email_alerts.get_html_body(msg)
        if not html:
            print(f"  Email {i}: no HTML body found (subject: {msg.get('Subject', '?')!r}) -- skipped")
            continue
        listings = ebay_email_alerts.extract_listings_from_html(html)
        print(f"  Email {i} (subject: {msg.get('Subject', '?')!r}): extracted {len(listings)} listing(s)")
        all_listings.extend(listings)

    print()
    if show_raw:
        print("--- Raw extracted listings (before player-matching) ---")
        shipping_found = 0
        type_counts = {"auction": 0, "fixed_price": 0, "unknown": 0}
        best_offer_found = 0
        for listing in all_listings:
            price_str = f"${listing['price']:,.2f}" if listing["price"] is not None else "NO PRICE FOUND"
            shipping = listing.get("shipping_price")
            shipping_str = f"${shipping:,.2f} shipping" if shipping is not None else "shipping unknown"
            if shipping is not None:
                shipping_found += 1
            listing_type = listing.get("listing_type", "unknown")
            type_counts[listing_type] = type_counts.get(listing_type, 0) + 1
            if listing.get("has_best_offer"):
                best_offer_found += 1
            bids = listing.get("bid_count")
            type_str = listing_type if bids is None else f"{listing_type} ({bids} bids)"
            print(f"  {price_str:>15}  ({shipping_str})  [{type_str}]  {listing['title']}")
            print(f"                   {listing['url']}")
        total = len(all_listings) or 1
        print(
            f"\nShipping found for {shipping_found}/{len(all_listings)} listing(s) -- "
            f"shipping extraction is unvalidated against real data (unlike price), "
            f"so a low hit rate here is expected/fine, not necessarily a bug."
        )
        print(
            f"Listing type: {type_counts.get('auction', 0)} auction / "
            f"{type_counts.get('fixed_price', 0)} fixed price / "
            f"{type_counts.get('unknown', 0)} unknown "
            f"({type_counts.get('unknown', 0) / total * 100:.0f}% unknown). "
            f"{best_offer_found} with Best Offer."
        )
        print(
            "  Listing-type detection is ALSO unvalidated against real alert emails. "
            "A high unknown rate is safe (the report says 'unknown' rather than assuming "
            "Buy It Now), but it means auctions can't be told apart yet -- if you see "
            "auctions in your inbox showing up as 'unknown' here, that's the thing to fix."
        )
        print()

    matched_count = 0
    print("--- Matched against your watchlist ---")
    for listing in all_listings:
        matched_player = matcher.match_player(listing["title"], players)
        if not matched_player or listing["price"] is None:
            continue
        card_type, grader, grade = matcher.detect_grading(listing["title"])
        grading = f"{grader} {grade}" if card_type == "graded" else "raw"
        matched_count += 1
        print(f"  {matched_player:<20} [{grading:>8}]  ${listing['price']:>10,.2f}  {listing['title']}")
        print(f"  {'':<20}             {listing['url']}")

    print(f"\nTotal matched: {matched_count} / {len(all_listings)} extracted listing(s)")
    _print_identity_coverage(all_listings, players)
    if all_listings and matched_count == 0:
        print(
            "\nExtraction found listings but none matched a watchlist player -- "
            "run with --raw to see the raw titles and check matcher.py's logic "
            "against them."
        )
    if not all_listings and messages:
        print(
            "\nEmails were found but nothing was extracted from them at all -- "
            "this likely means extract_listings_from_html() needs adjusting for "
            "eBay's actual template. See the PROVISIONAL note in "
            "src/ebay_email_alerts.py."
        )


def _print_identity_coverage(all_listings: list, players: list) -> None:
    """Identity fill rates -- the KPI that decides whether CardPro can value
    anything at all.

    `exact` and `same_card` comps both require a KNOWN parallel, so the
    parallel fill rate below is effectively a ceiling on how many listings can
    ever be called a deal. When the audit measured this against production
    data it was 3%, which is why nothing could be honestly flagged. Watch this
    number, not the deal count.
    """
    matched = [item for item in all_listings if matcher.match_player(item["title"], players)]
    if not matched:
        return

    counts = {"year": 0, "set_name": 0, "parallel": 0, "card_number": 0, "graded": 0, "blocked": 0}
    for item in matched:
        identity = card_identity.extract_card_identity(item["title"])
        for field in ("year", "set_name", "parallel", "card_number"):
            if getattr(identity, field).value is not None:
                counts[field] += 1
        if matcher.detect_grade_details(item["title"]).card_type == "graded":
            counts["graded"] += 1
        if card_identity.is_excluded_from_deals(identity):
            counts["blocked"] += 1

    total = len(matched)
    print("\n--- Identity coverage (the ceiling on what can ever be valued) ---")
    for field in ("year", "set_name", "parallel", "card_number"):
        print(f"  {field:<12} {counts[field]:>4}/{total}  ({counts[field] / total * 100:.0f}%)")
    print(f"  {'graded':<12} {counts['graded']:>4}/{total}  ({counts['graded'] / total * 100:.0f}%)")
    print(f"  {'blocked':<12} {counts['blocked']:>4}/{total}  (reprint/lot/sealed/break/etc)")
    print(
        "\n  A deal can only be declared off an exact or same-card comp, and both need a "
        "known parallel -- so the parallel row is the hard ceiling on flaggable listings. "
        "A low graded row means your saved searches aren't reaching the liquid half of the "
        "market; the daily report lists the searches that would fix that."
    )


if __name__ == "__main__":
    main()
