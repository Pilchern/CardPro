"""Self-building comp history for the eBay-email-alerts path.

Without eBay API access there's no sold-comps feed and no live
active-listings snapshot to derive a fallback median from (see
ebay_email_alerts.py). Instead, every price observed in an eBay
saved-search alert email gets appended here, and the accumulated history
per (player, card_type) bucket becomes the comp baseline once there's
enough of it (same min_comps_required gate as everywhere else).

Same honest caveat as the old active-listing fallback: these are asking
prices for newly-listed items, not confirmed sold prices -- weaker signal
than real comps, and still labeled as such (via comps.CompStats.is_fallback)
in the report. It just gets better over time as more alerts arrive, with
zero extra API access required.

Each observation is tagged with the listing's id (its URL) so the same
listing seen more than once -- e.g. because ebay_alerts_lookback_days
overlaps two consecutive daily runs, or the same item matches more than
one saved search -- only counts once toward the comp median (the latest
price observed for it), not once per sighting. Without this, every price
observed under a 2-day lookback window would get counted roughly twice,
systematically skewing every median toward whatever's currently listed
rather than a real distribution of distinct cards. Observations from
before this field existed have no "id" and are kept as-is (can't be
deduped retroactively); they age out naturally via prune_old.

Plain JSON on disk, same pattern as dedupe.py -- easy to open and inspect.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import comps

logger = logging.getLogger(__name__)


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.warning("%s is corrupt, starting fresh (old file left in place)", path)
        return {}


def save(path: Path, history: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(history, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def record(history: dict, player: str, card_type: str, price: float, today: str, listing_id: str = "") -> None:
    key = f"{player}|{card_type}"
    history.setdefault(key, []).append({"price": price, "date": today, "id": listing_id})


def prune_old(history: dict, max_age_days: int, today: datetime) -> dict:
    cutoff = today - timedelta(days=max_age_days)
    pruned: dict = {}
    for key, observations in history.items():
        kept = []
        for obs in observations:
            try:
                obs_date = datetime.strptime(obs["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if obs_date >= cutoff:
                kept.append(obs)
        if kept:
            pruned[key] = kept
    return pruned


def as_buckets(history: dict) -> dict[tuple[str, str, str], list[float]]:
    """Converts the {"Player|card_type": [{"price":.., "date":.., "id":..}]}
    storage format into the {(player, card_type, price_tier): [prices]}
    shape comps.py expects. Tiering happens here (at read time) rather than
    at storage time, so the on-disk history stays a simple chronological log
    -- each price is tiered by its own value when the comp table is built.

    Observations sharing the same listing "id" are collapsed to just the
    most recent one before bucketing, so one listing seen on multiple days
    (overlapping lookback windows, more than one matching saved search,
    etc.) contributes a single price to the median instead of one per
    sighting -- see module docstring. Observations with no "id" (pre-dating
    this field) can't be deduped and are kept as-is.
    """
    buckets: dict[tuple[str, str, str], list[float]] = {}
    for key, observations in history.items():
        player, _, card_type = key.partition("|")
        latest_by_id: dict[str, dict] = {}
        unidentified: list[dict] = []
        for obs in observations:
            listing_id = obs.get("id")
            if not listing_id:
                unidentified.append(obs)
                continue
            prior = latest_by_id.get(listing_id)
            if prior is None or obs.get("date", "") >= prior.get("date", ""):
                latest_by_id[listing_id] = obs
        for obs in list(latest_by_id.values()) + unidentified:
            price = obs["price"]
            tier_key = (player, card_type, comps.price_tier(price))
            buckets.setdefault(tier_key, []).append(price)
    return buckets
