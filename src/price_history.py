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

Each observation also carries whatever card_identity fields were known for
it (year/set_name/parallel/card_number/grader/grade -- see card_identity.py),
so comps.build_hierarchical_comp_table() can group observations by actual
card identity instead of only price tier. Fields that weren't extracted are
stored as None/absent, same "unknown, not guessed" rule as card_identity.py
itself; older observations recorded before these fields existed simply
don't have them, and fall back to matching only at the price_tier level.

Plain JSON on disk, same pattern as dedupe.py -- easy to open and inspect.

Reading a corpus that exists but cannot be parsed RAISES rather than starting
fresh -- see CorruptCorpus. That is the difference between losing a day and
losing six months.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src import comps

logger = logging.getLogger(__name__)


class CorruptCorpus(Exception):
    """The corpus file exists but could not be read.

    Raised rather than swallowed, and this is the whole point. The previous
    behaviour returned {} with a warning saying "old file left in place" --
    which was true for about thirty seconds, until save() atomically replaced
    the unreadable file with the day's ~400 observations and the workflow
    committed the wipe to the repo. Months of corpus, gone, with the only
    evidence a log line on a runner GitHub deletes minutes later.

    Failing loudly instead means the run aborts, main's handler emails the
    traceback, and the workflow's `git diff --cached --quiet` finds nothing
    to commit. The file survives to be repaired by hand.
    """


def load(path: Path) -> dict:
    """The stored corpus. A missing file is the normal first run and gives {}.

    An UNREADABLE file is not normal and raises -- see CorruptCorpus.
    """
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise CorruptCorpus(
            "{} exists but is not valid JSON ({}). Refusing to continue, because "
            "the next save would overwrite it with today's data alone. Repair or "
            "move the file, then re-run.".format(path, exc)
        ) from exc


def _observation_count(history: dict) -> int:
    return sum(len(entries) for entries in history.values() if isinstance(entries, list))


def save(path: Path, history: dict) -> None:
    """Write the corpus, unless doing so would empty a corpus that has data.

    Nothing in the pipeline has a legitimate reason to reduce the corpus to
    nothing: record() only appends, and prune_old() drops observations past
    the retention window, which cannot empty a corpus that is being written
    to daily. So an empty document arriving here means something upstream
    lost the data, and writing it would turn a recoverable in-memory bug into
    an unrecoverable on-disk one.

    A shrink that is not a wipe is allowed through deliberately: that is what
    pruning looks like, and second-guessing it here would mean encoding the
    retention policy in two places.
    """
    if not _observation_count(history) and path.exists():
        existing = _observation_count(load(path))
        if existing:
            raise CorruptCorpus(
                "Refusing to overwrite {} ({} observation(s)) with an empty corpus. "
                "Nothing in the pipeline empties the corpus, so this is a bug "
                "upstream, and writing it would make the loss permanent.".format(
                    path, existing
                )
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(history, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def record(
    history: dict,
    player: str,
    card_type: str,
    price: float,
    today: str,
    listing_id: str = "",
    year: Optional[int] = None,
    set_name: Optional[str] = None,
    parallel: Optional[str] = None,
    card_number: Optional[str] = None,
    grader: Optional[str] = None,
    grade: Optional[str] = None,
    qualifier: Optional[str] = None,
    print_run: Optional[int] = None,
    basis: str = "asking",
) -> None:
    """Appends one price observation to the corpus.

    `basis` records WHAT KIND of price this is: "asking" (a seller's opinion,
    which is all this data path can see) or "sold" (an actual transaction,
    available only via imported data). The comp engine caps confidence for
    asking-basis comps, so this must be recorded honestly -- mislabelling an
    asking price as sold would let a seller's wishful number masquerade as
    market truth.

    `qualifier` is a grade qualifier such as "OC" (off-centre). A qualified
    slab is a materially different market from an unqualified one at the same
    numeric grade, so it is stored separately rather than folded in.

    Auctions must NOT be recorded here: a current bid is not a price. The
    caller is responsible for that filter (see main.record_observations),
    since only the caller knows the listing type.
    """
    key = f"{player}|{card_type}"
    history.setdefault(key, []).append(
        {
            "price": price,
            "date": today,
            "id": listing_id,
            "year": year,
            "set_name": set_name,
            "parallel": parallel,
            "card_number": card_number,
            "grader": grader,
            "grade": grade,
            "qualifier": qualifier,
            "print_run": print_run,
            "basis": basis,
        }
    )


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


def deduped_observations(history: dict) -> list[dict]:
    """One observation per unique listing id (the latest by date), each
    tagged with its "player" and "card_type" (parsed out of the storage
    key) -- the shared corpus used both by as_buckets() (price-tier only)
    and comps.build_hierarchical_comp_table() (identity-aware levels).

    Observations sharing the same listing "id" are collapsed to just the
    most recent one, so one listing seen on multiple days (overlapping
    lookback windows, more than one matching saved search, etc.)
    contributes a single price to the median instead of one per sighting --
    see module docstring. Observations with no "id" (pre-dating that field)
    can't be deduped and are kept as-is.
    """
    observations: list[dict] = []
    for key, entries in history.items():
        player, _, card_type = key.partition("|")
        latest_by_id: dict[str, dict] = {}
        unidentified: list[dict] = []
        for obs in entries:
            listing_id = obs.get("id")
            if not listing_id:
                unidentified.append(obs)
                continue
            prior = latest_by_id.get(listing_id)
            if prior is None or obs.get("date", "") >= prior.get("date", ""):
                latest_by_id[listing_id] = obs
        for obs in list(latest_by_id.values()) + unidentified:
            merged = dict(obs)
            merged["player"] = player
            merged["card_type"] = card_type
            observations.append(merged)
    return observations


def as_buckets(history: dict) -> dict[tuple[str, str, str], list[float]]:
    """Converts the stored history into the {(player, card_type,
    price_tier): [prices]} shape comps.py's price-tier-only fallback
    expects. Tiering happens here (at read time) rather than at storage
    time, so the on-disk history stays a simple chronological log -- each
    price is tiered by its own value when the comp table is built.
    """
    buckets: dict[tuple[str, str, str], list[float]] = {}
    for obs in deduped_observations(history):
        tier_key = (obs["player"], obs["card_type"], comps.price_tier(obs["price"]))
        buckets.setdefault(tier_key, []).append(obs["price"])
    return buckets
