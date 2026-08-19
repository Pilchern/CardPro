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


def record(history: dict, player: str, card_type: str, price: float, today: str) -> None:
    key = f"{player}|{card_type}"
    history.setdefault(key, []).append({"price": price, "date": today})


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
    """Converts the {"Player|card_type": [{"price":.., "date":..}]} storage
    format into the {(player, card_type, price_tier): [prices]} shape
    comps.py expects. Tiering happens here (at read time) rather than at
    storage time, so the on-disk history stays a simple chronological log
    -- each price is tiered by its own value when the comp table is built.
    """
    buckets: dict[tuple[str, str, str], list[float]] = {}
    for key, observations in history.items():
        player, _, card_type = key.partition("|")
        for obs in observations:
            price = obs["price"]
            tier_key = (player, card_type, comps.price_tier(price))
            buckets.setdefault(tier_key, []).append(price)
    return buckets
