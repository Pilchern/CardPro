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

from src import ebay_email_alerts, matcher
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

    print(
        f"Checking Gmail inbox for eBay alert emails "
        f"(from contains {alert_settings['sender_contains']!r}, last {alert_settings['lookback_days']} day(s))...\n"
    )

    messages = ebay_email_alerts.fetch_alert_messages(
        os.environ["GMAIL_ADDRESS"],
        os.environ["GMAIL_APP_PASSWORD"],
        alert_settings["sender_contains"],
        alert_settings["lookback_days"],
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
        for listing in all_listings:
            price_str = f"${listing['price']:,.2f}" if listing["price"] is not None else "NO PRICE FOUND"
            shipping = listing.get("shipping_price")
            shipping_str = f"${shipping:,.2f} shipping" if shipping is not None else "shipping unknown"
            if shipping is not None:
                shipping_found += 1
            print(f"  {price_str:>15}  ({shipping_str})  {listing['title']}")
            print(f"                   {listing['url']}")
        print(
            f"\nShipping found for {shipping_found}/{len(all_listings)} listing(s) -- "
            f"shipping extraction is unvalidated against real data (unlike price), "
            f"so a low hit rate here is expected/fine, not necessarily a bug."
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


if __name__ == "__main__":
    main()
