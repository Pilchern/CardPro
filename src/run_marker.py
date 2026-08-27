"""When the daily scan last completed, and whether it already ran today.

GitHub's scheduled workflows are best-effort. Under load they are delayed by
hours and sometimes dropped entirely, and a dropped run is the one failure
this project could not see: no email, no failure notification, no red job --
you simply do not hear anything, which is indistinguishable from a day you
were not looking. "Never go silent" is a design principle and it was being
enforced everywhere except at the layer that decides whether the code runs
at all.

So the workflow schedules a second, later run, and that run reads this
marker: if the scan already completed today it exits without doing anything.
The backup costs a few seconds of a runner on the days it is not needed and
saves the whole day on the days it is.

The marker is also what lets the report say when the last run was. A gap
matters more than any single day's contents -- a corpus with a hole in it
values things slightly wrong for the next six months -- and until now a gap
was invisible from inside the email.

Deliberately its own tiny file rather than a field on the corpus. The corpus
records observations; a run that legitimately saw nothing records none, so
"the newest observation is from today" is not the same question as "did the
scan run today", and answering the second with the first would make a quiet
day look like a missed one.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load(path: Path) -> dict:
    """The marker, or {} when there is none.

    Unlike the corpus and the dedupe state, an unreadable marker is NOT
    fatal. The worst it can cause is one extra run, and refusing to scan
    because a convenience file is corrupt would be the failure this module
    exists to prevent.
    """
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("%s is unreadable (%s); treating today as not yet run", path, exc)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save(path: Path, today: str, listings_seen: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump({"date": today, "listings_seen": listings_seen}, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def ran_on(path: Path, today: str) -> bool:
    return load(path).get("date") == today


def last_run_date(path: Path) -> Optional[str]:
    date = load(path).get("date")
    return date if isinstance(date, str) and date else None


def gap_days(path: Path, today: str) -> Optional[int]:
    """Whole days between the last completed run and today, or None if
    unknown. 0 means it already ran today, 1 is the ordinary daily cadence,
    and anything above that is a hole in the corpus."""
    from datetime import datetime

    last = last_run_date(path)
    if last is None:
        return None
    try:
        then = datetime.strptime(last, "%Y-%m-%d")
        now = datetime.strptime(today, "%Y-%m-%d")
    except ValueError:
        return None
    return (now - then).days
