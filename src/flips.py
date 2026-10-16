"""Your own flips, from data/flips.csv: what you passed on, bought and sold.

Why this exists: every sale you make is a real sold price for a card you
identified yourself -- exactly the data this project cannot get any other
way -- and every card you already bought or passed on is one the email
should stop showing you. One CSV row per action, appended by hand:

    date,item_id,action,price,ship,fees,grader,grade,title,note

action is pass | buy | sell. item_id is the eBay item number (or its URL).
A sell row is joined to the buy row with the same item_id; the card is read
from the BUY row's title with the same parser the daily scan uses, and the
sale becomes a sold comp (source "own-sale") through sold_comps' own
validation, so a row that would key the wrong bucket is refused, not banked.
"""
from __future__ import annotations

import csv
import logging
import statistics
from datetime import date
from pathlib import Path
from typing import Optional

from src import card_identity, matcher, sold_comps

logger = logging.getLogger(__name__)

COLUMNS = ("date", "item_id", "action", "price", "ship", "fees", "grader", "grade", "title", "note")
ACTIONS = ("pass", "buy", "sell")


def listing_id(item_id: str) -> str:
    """The id a listing carries in the pipeline (main._browse_listing_id)."""
    item_id = item_id.strip()
    return item_id if item_id.startswith("http") else "https://www.ebay.com/itm/" + item_id


def _money(value) -> float:
    try:
        return float(value) if str(value).strip() else 0.0
    except ValueError:
        return float("nan")


def load(path: Path) -> list:
    """Valid rows, oldest first. A bad row is skipped with a warning naming it."""
    if not path.exists():
        return []
    rows = []
    with open(path, newline="") as f:
        for number, row in enumerate(csv.DictReader(f), start=2):
            ok = (
                row.get("action") in ACTIONS and (row.get("item_id") or "").strip()
                and sold_comps._valid_date(row.get("date")) and _money(row.get("price")) > 0
            )
            if not ok:
                logger.warning("flips.csv line %d skipped: needs a date, item_id, pass|buy|sell and a price", number)
                continue
            rows.append(row)
    return rows


def _pairs(rows) -> list:
    """(buy, sell) per item_id that has both."""
    buys = {r["item_id"].strip(): r for r in rows if r["action"] == "buy"}
    return [(buys[r["item_id"].strip()], r) for r in rows
            if r["action"] == "sell" and r["item_id"].strip() in buys]


def sold_observations(rows, players) -> list:
    """Each sold flip as a CompEngine observation, or nothing if it would not validate."""
    observations = []
    for buy, sell in _pairs(rows):
        title = buy.get("title") or ""
        identity = card_identity.extract_card_identity(title)
        grade = matcher.detect_grade_details(title)
        matched = matcher.match_players(title, players)
        sale = {
            "player": matched[0] if matched else None, "year": identity.year.value,
            "set_name": identity.set_name.value, "parallel": identity.parallel.value,
            "card_number": identity.card_number.value,
            "grader": (sell.get("grader") or grade.grader), "grade": (sell.get("grade") or grade.grade),
            "is_autograph": bool(identity.is_autograph.value),
            "relic": "patch" if identity.is_patch.value else ("relic" if identity.is_memorabilia.value else None),
            "print_run": identity.print_run.value,
            "price": _money(sell["price"]), "date": sell["date"], "source": "own-sale",
            "note": "flip of " + sell["item_id"].strip(),
        }
        observation = sold_comps.to_observation({k: v for k, v in sale.items() if v is not None})
        if observation is None:
            logger.warning("flip %s not used as a sold comp: %s", sell["item_id"],
                           sold_comps.validation_error({k: v for k, v in sale.items() if v is not None}))
            continue
        observations.append(observation)
    return observations


def mark_seen(rows, seen: dict, today: str) -> None:
    """Cards you bought or passed on are already decided: record them as
    seen at that price, so the email drops them unless the price falls."""
    for row in rows:
        if row["action"] in ("pass", "buy"):
            key = listing_id(row["item_id"])
            prior = seen.get(key)
            if prior is None or (isinstance(prior, dict) and _money(prior.get("price")) > _money(row["price"])):
                seen[key] = {"price": _money(row["price"]), "first_seen": row["date"], "last_flagged": today}


def summary(rows) -> Optional[str]:
    """One footer line: flips logged, realised net, median days to sell."""
    pairs = _pairs(rows)
    if not rows:
        return None
    net = sum(
        _money(s["price"]) - _money(s["fees"]) - _money(s["ship"])
        - _money(b["price"]) - _money(b["ship"]) - _money(b["fees"])
        for b, s in pairs
    )
    days = [(date.fromisoformat(s["date"]) - date.fromisoformat(b["date"])).days for b, s in pairs]
    line = "Flips: {} logged ({} sold), realised net ${:,.2f}".format(len(rows), len(pairs), net)
    if days:
        line += ", median {:.0f} days to sell".format(statistics.median(days))
    return line + "."
