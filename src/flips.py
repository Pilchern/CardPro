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
    text = str(value if value is not None else "").replace("$", "").replace(",", "").strip()
    try:
        return float(text) if text else 0.0
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
            # Canonical values from here on: "2026-1-20" / "2026-10-12 " are
            # accepted above and would crash anything that parses them raw.
            row["date"] = sold_comps._valid_date(row["date"])
            row["item_id"] = row["item_id"].strip()
            rows.append(row)
    return rows


def _pairs(rows, warn: bool = False) -> list:
    """(buy, sell) per item_id that has both. One sale per item: a repeated
    sell row (an accidental duplicate, or a correction) -- the last wins."""
    buys = {r["item_id"]: r for r in rows if r["action"] == "buy"}
    sells = {r["item_id"]: r for r in rows if r["action"] == "sell"}
    if warn:
        for item_id in sells.keys() - buys.keys():
            logger.warning("flips.csv: sell of %s has no buy row, so it is not a sold comp", item_id)
    return [(buys[item_id], sell) for item_id, sell in sells.items() if item_id in buys]


def sold_observations(rows, players) -> list:
    """Each sold flip as a CompEngine observation, or nothing if it would not validate."""
    observations = []
    for buy, sell in _pairs(rows, warn=True):
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
            "note": "flip of " + sell["item_id"],
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


def without_duplicates(own: list, hand_entered: list) -> list:
    """Own sales not already typed into config/sold_comps.json: the same sale
    entered both ways must count once (same card, price and date)."""
    def key(obs):
        return tuple(obs.get(k) for k in (
            "player", "year", "set_name", "parallel", "card_number", "card_type",
            "grader", "grade", "price", "date"))
    typed = {key(obs) for obs in hand_entered}
    return [obs for obs in own if key(obs) not in typed]


def summary(rows) -> Optional[str]:
    """One footer line: flips logged, realised net, median days to sell.
    A pair that cannot be read is logged and skipped -- a bad row in a
    hand-edited file must never stop the scan."""
    if not rows:
        return None
    net, days, pairs = 0.0, [], 0
    for b, s in _pairs(rows):
        try:
            flip_net = (_money(s["price"]) - _money(s["fees"]) - _money(s["ship"])
                        - _money(b["price"]) - _money(b["ship"]) - _money(b["fees"]))
            held = (date.fromisoformat(s["date"]) - date.fromisoformat(b["date"])).days
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("flips.csv: flip of %s skipped in the summary (%s)", s.get("item_id"), exc)
            continue
        if flip_net != flip_net:  # NaN: an unreadable money field
            logger.warning("flips.csv: flip of %s has an unreadable amount, skipped", s.get("item_id"))
            continue
        net, pairs = net + flip_net, pairs + 1
        days.append(held)
    line = "Flips: {} logged ({} sold), realised net ${:,.2f}".format(len(rows), pairs, net)
    if days:
        line += ", median {:.0f} days to sell".format(statistics.median(days))
    return line + "."
