"""Standalone Craigslist sanity check -- doesn't touch eBay or email, so
it works even before your eBay dev account is approved.

Runs the real RSS search for every watchlist player against your
configured Craigslist site and prints what would be matched: title, parsed
price, detected grading, and link. Nothing is emailed or written to the
dedupe file.

Usage:
    python -m scripts.test_craigslist
    python -m scripts.test_craigslist "Michael Jordan"     # just one player
    python -m scripts.test_craigslist --show                # visible browser window, for debugging
    python -m scripts.test_craigslist --show "Michael Jordan"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import craigslist_client, matcher

ROOT_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    with open(ROOT_DIR / "config" / "watchlist.json") as f:
        players = json.load(f)["players"]
    with open(ROOT_DIR / "config" / "settings.json") as f:
        cl_settings = json.load(f)["craigslist"]

    args = sys.argv[1:]
    show = "--show" in args
    args = [a for a in args if a != "--show"]

    if args:
        players = [" ".join(args)]

    site, category = cl_settings["site"], cl_settings["category"]
    headless = not show and bool(cl_settings.get("headless", True))
    print(
        f"Searching {site}.craigslist.org (category={category}) for {len(players)} "
        f"player(s), headless={headless}...\n"
    )

    total = 0
    with craigslist_client.CraigslistSession(headless=headless) as session:
        for player in players:
            term = f"{player} card"
            results = session.search(term, site, category)
            matched = [r for r in results if matcher.match_player(r["title"], [player]) and r["price"] is not None]
            total += len(matched)

            print(f"--- {player} ({len(results)} raw hits, {len(matched)} matched with a price) ---")
            for r in matched:
                card_type, grader, grade = matcher.detect_grading(r["title"])
                grading = f"{grader} {grade}" if card_type == "graded" else "raw"
                print(f"  ${r['price']:>8.2f}  [{grading:>8}]  {r['title']}")
                print(f"             {r['link']}")
            print()

    print(f"Total matched listings across all players: {total}")


if __name__ == "__main__":
    main()
