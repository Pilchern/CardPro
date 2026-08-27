"""Tracks which listings we've already flagged, so the daily email only
contains new deals or ones whose price dropped further since last time.

Backing store is one flat JSON file (default: data/seen_listings.json),
keyed by listing id (eBay itemId or Craigslist URL). Kept as plain JSON on
purpose -- easy to open and inspect or hand-edit if something looks off.
"""
from __future__ import annotations

import json
import logging
import math
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


def _field(record, key):
    """One field out of a seen-record, tolerating a record that is not an
    object at all -- a hand-edit can leave a bare value where the record was,
    and the readers below should see that as "unknown", not as a crash.
    """
    return record.get(key) if isinstance(record, dict) else None


def _readable_price(record) -> float | None:
    """The price a seen-record claims, or None if it cannot claim one.

    The file is meant to be hand-edited, so records turn up here with the key
    missing, with null, or with the number quoted. A quoted number still names
    a price unambiguously, so it is read; anything else is unknown rather than
    guessed at (principle 4). NaN is unknown too -- it compares False against
    every price, which would read as "already seen at that price".
    """
    price = _field(record, "price")
    if isinstance(price, bool):  # JSON true/false is not a price
        return None
    if isinstance(price, (int, float)):
        value = float(price)
    elif isinstance(price, str):
        try:
            value = float(price)
        except ValueError:
            return None
    else:
        return None
    return None if math.isnan(value) else value


def is_new_or_price_drop(listing_id: str, price: float, seen: dict) -> bool:
    """True if this listing has never been flagged, or its price dropped
    since the last time it was. Does NOT mutate `seen` -- call
    record_flagged() after you've decided to include it in the report.
    """
    prior = seen.get(listing_id)
    if prior is None:
        return True
    prior_price = _readable_price(prior)
    if prior_price is None:
        # A record with no usable price cannot support "you have already been
        # told about this at that price", so it does not get to suppress
        # anything. Falling to "new" costs one duplicate row in one email;
        # falling to "seen" would hide a listing the user may never have been
        # shown, and they would never learn it existed.
        logger.warning(
            "seen record for %s has no usable price (%r) -- reporting the listing "
            "as new rather than suppressing something that may never have been sent",
            listing_id,
            _field(prior, "price"),
        )
        return True
    return price < prior_price


def record_flagged(listing_id: str, price: float, seen: dict, today: str) -> None:
    prior = seen.get(listing_id)
    # A prior record that never had a readable first_seen still gets rewritten
    # rather than crashing the run: today is the honest answer to "when did we
    # first tell you about this", given the file no longer says.
    seen[listing_id] = {
        "price": price,
        "first_seen": _field(prior, "first_seen") or today,
        "last_flagged": today,
    }


def _last_flagged_at(record) -> datetime | None:
    """When this record was last flagged, or None if the date cannot be read."""
    raw = _field(record, "last_flagged")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # Full timestamps ("2026-08-26T09:00:00Z") are accepted as well: they name
    # the same day with no ambiguity, and refusing to read them would pin those
    # records in the file forever, since unreadable dates are kept below.
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def prune_old(seen: dict, max_age_days: int, today: datetime) -> dict:
    cutoff = today - timedelta(days=max_age_days)
    kept = {}
    for listing_id, record in seen.items():
        last_flagged = _last_flagged_at(record)
        if last_flagged is None:
            # An unreadable date is not evidence that the record is stale.
            # Dropping it un-sees the listing and the user gets re-told about
            # it tomorrow, so keep it: the file grows by one line instead.
            logger.warning(
                "seen record for %s has an unreadable last_flagged (%r) -- keeping it, "
                "because dropping it would re-report a listing already sent",
                listing_id,
                _field(record, "last_flagged"),
            )
            kept[listing_id] = record
            continue
        if last_flagged >= cutoff:
            kept[listing_id] = record
    return kept
