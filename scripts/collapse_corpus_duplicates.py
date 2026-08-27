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

A third harm, found later and fixed the same way: a listing whose card_type
read differently on two days -- `raw` on the first look, `graded` once a
fuller title showed the slab -- was stored under both keys and counted in
both markets, one of them wrong, for the whole retention window. That is one
listing read two ways, not two listings, so collapsing now spans a player's
card_types too. It does NOT merge the markets: the row lands in the
card_type of its latest reading and raw and graded stay separate buckets
(principle #6). One such pair is in the corpus this script serves.

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
    """(rows, distinct listings, id-less rows).

    A listing is counted per PLAYER, not per storage key. Per key would make
    the raw/graded pair above look like two listings and turn its collapse
    into an apparent loss; globally would hide a real loss, because the same
    listing under two players is a multi-player card genuinely in both
    players' markets and both rows must survive.
    """
    rows = sum(len(entries) for entries in history.values())
    listings = {
        (key.partition("|")[0], obs.get("id"))
        for key, entries in history.items()
        for obs in entries
        if obs.get("id")
    }
    idless = sum(
        1 for entries in history.values() for obs in entries if not obs.get("id")
    )
    return rows, len(listings), idless


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    history = price_history.load(args.path)
    before = _counts(history)
    collapsed = price_history.collapse_duplicates(history)
    after = _counts(collapsed)

    print("rows {} -> {}   distinct listings {} -> {}   id-less rows {} -> {}".format(
        before[0], after[0], before[1], after[1], before[2], after[2]
    ))

    # The invariant worth checking out loud: collapsing must not lose a
    # listing. Rows go down; distinct listings and id-less rows must not.
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
