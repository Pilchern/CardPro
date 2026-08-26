"""One-shot: collapse a corpus recorded before price_history.record() deduped.

Until record() kept one row per listing, every re-sighting appended a new
row. With a lookback window that overlaps consecutive daily runs, that meant
two to four rows per listing -- 2,099 rows for 906 distinct listings in the
corpus this script was written for.

Two harms, and the second is the one that matters:

  * Size. 57% of the file was duplicates that deduped_observations() threw
    away on every read anyway.
  * A weakened quality gate. Each row carried the date it was written, so one
    morning's batch of listings ended up stored under several different
    dates. comps._is_concentrated refuses a bucket whose observations do not
    span enough distinct days, precisely because six asks captured in one
    morning are one snapshot six listings deep -- and this was manufacturing
    the spread that gate looks for.

Collapsing keeps the earliest date (when the ask entered the market) and the
latest price (what a buyer faces today), the same rule record() now applies.

Idempotent -- running it on an already-collapsed corpus changes nothing.

    python -m scripts.collapse_corpus_duplicates [--path P] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import price_history  # noqa: E402

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "ebay_alert_price_history.json"


def _counts(history: dict) -> tuple:
    """(rows, distinct listing-in-bucket pairs, id-less rows).

    Counted per bucket rather than globally on purpose: a listing can
    legitimately appear in two buckets, and in this corpus one does -- a
    Munetaka Murakami listing whose grade read differently on different days,
    so it sits in both `raw` and `graded`. Collapsing must not merge those;
    they are different markets and the engine treats them as such. (That the
    extraction flip-flopped at all is a separate problem, and not one this
    script should paper over.)
    """
    rows = sum(len(entries) for entries in history.values())
    pairs = {
        (key, obs.get("id"))
        for key, entries in history.items()
        for obs in entries
        if obs.get("id")
    }
    idless = sum(
        1 for entries in history.values() for obs in entries if not obs.get("id")
    )
    return rows, len(pairs), idless


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    history = price_history.load(args.path)
    before = _counts(history)
    collapsed = price_history.collapse_duplicates(history)
    after = _counts(collapsed)

    print("rows {} -> {}   listing/bucket pairs {} -> {}   id-less rows {} -> {}".format(
        before[0], after[0], before[1], after[1], before[2], after[2]
    ))

    # The invariant worth checking out loud: collapsing must not lose a
    # listing. Rows go down; distinct ids and id-less rows must not.
    if after[1] != before[1] or after[2] != before[2]:
        raise SystemExit("REFUSING TO WRITE: collapsing changed the listing count.")
    if after[0] != after[1] + after[2]:
        raise SystemExit("REFUSING TO WRITE: result still has duplicate rows.")

    if args.dry_run:
        print("(dry run -- nothing written)")
        return
    if after[0] == before[0]:
        print("Already collapsed; nothing to write.")
        return
    price_history.save(args.path, collapsed)
    print("Wrote {}".format(args.path))


if __name__ == "__main__":
    main()
