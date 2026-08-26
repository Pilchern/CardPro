"""Tracks which listings we've already flagged, so the daily email only
contains new deals or ones whose price dropped further since last time.

Backing store is one flat JSON file (default: data/seen_listings.json),
keyed by listing id (eBay itemId or Craigslist URL). Kept as plain JSON on
purpose -- easy to open and inspect or hand-edit if something looks off.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class CorruptSeenListings(Exception):
    """seen_listings.json exists but could not be read.

    Same reasoning as price_history.CorruptCorpus, milder consequence:
    starting fresh here re-reports every listing you have already seen as
    though it were new, and then commits that reset over the real file. An
    email full of yesterday's deals is a trust problem, so this fails loudly
    too rather than quietly resetting.
    """


def load_seen(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise CorruptSeenListings(
            "{} exists but is not valid JSON ({}). Refusing to continue, because "
            "the next save would replace it and every listing already reported "
            "would come back as new. Repair or move the file, then re-run.".format(
                path, exc
            )
        ) from exc


def save_seen(path: Path, seen: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(seen, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def is_new_or_price_drop(listing_id: str, price: float, seen: dict) -> bool:
    """True if this listing has never been flagged, or its price dropped
    since the last time it was. Does NOT mutate `seen` -- call
    record_flagged() after you've decided to include it in the report.
    """
    prior = seen.get(listing_id)
    if prior is None:
        return True
    return price < prior["price"]


def record_flagged(listing_id: str, price: float, seen: dict, today: str) -> None:
    prior = seen.get(listing_id)
    seen[listing_id] = {
        "price": price,
        "first_seen": prior["first_seen"] if prior else today,
        "last_flagged": today,
    }


def prune_old(seen: dict, max_age_days: int, today: datetime) -> dict:
    cutoff = today - timedelta(days=max_age_days)
    kept = {}
    for listing_id, record in seen.items():
        try:
            last_flagged = datetime.strptime(record["last_flagged"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if last_flagged >= cutoff:
            kept[listing_id] = record
    return kept
