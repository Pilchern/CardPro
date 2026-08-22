"""Replay the stored comp corpus through the CardPro 2.0 valuation engine
and print what would happen -- the before/after evidence for the audit.

This is the closest thing to a backtest the current data supports. It does
NOT hit the network, does not send email, and does not write anything. It
treats every observation in data/ebay_alert_price_history.json as though it
were a live listing, values it against the rest of the corpus (excluding
itself, as the real pipeline does), and reports the distribution of comp
levels, confidences, and flag decisions.

Honest about what it is not: these are asking-price observations, not sold
prices, and the corpus is only as deep as the alert emails that produced it.
This measures whether the ENGINE behaves correctly, not whether the market
values are right -- no data source available to this project can tell us
that yet.

    python -m scripts.replay_corpus [--legacy] [--min-comps N]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import comps, price_history  # noqa: E402

CORPUS = Path(__file__).resolve().parent.parent / "data" / "ebay_alert_price_history.json"


def replay_new(observations, min_comps, today):
    engine = comps.CompEngine(observations, min_comps_required=min_comps, today=today)
    levels = collections.Counter()
    confidences = collections.Counter()
    blocked = collections.Counter()
    flagged = []

    for obs in observations:
        match = engine.lookup(
            player=obs["player"],
            card_type=obs["card_type"],
            price=obs["price"],
            grader=obs.get("grader"),
            grade=obs.get("grade"),
            qualifier=obs.get("qualifier"),
            year=obs.get("year"),
            set_name=obs.get("set_name"),
            parallel=obs.get("parallel"),
            card_number=obs.get("card_number"),
            exclude_id=obs.get("id"),
        )
        if match is None:
            levels["(no comp)"] += 1
            continue
        levels[match.level] += 1
        confidences[match.confidence] += 1
        for reason in match.blocked_reasons:
            blocked[reason] += 1
        if not match.flag_eligible:
            continue
        savings = match.stats.median - obs["price"]
        pct = savings / match.stats.median * 100 if match.stats.median else 0
        if pct >= 30 and savings >= 10:
            flagged.append((savings, pct, match, obs))

    return engine, levels, confidences, blocked, flagged


def replay_legacy(observations, min_comps):
    """What the v1 engine did with the same corpus, for comparison."""
    table = comps.build_hierarchical_comp_table(observations, min_comps)
    levels = collections.Counter()
    flagged = []
    for obs in observations:
        result = comps.lookup_hierarchical_comp(
            table,
            player=obs["player"],
            card_type=obs["card_type"],
            price=obs["price"],
            grader=obs.get("grader"),
            grade=obs.get("grade"),
            year=obs.get("year"),
            set_name=obs.get("set_name"),
            parallel=obs.get("parallel"),
            card_number=obs.get("card_number"),
        )
        if result is None:
            levels["(no comp)"] += 1
            continue
        stats, level = result
        levels[level] += 1
        savings = stats.median - obs["price"]
        pct = savings / stats.median * 100 if stats.median else 0
        if pct >= 30 and savings >= 10:
            flagged.append((savings, pct, level, stats, obs))
    return table, levels, flagged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-comps", type=int, default=3)
    parser.add_argument("--legacy", action="store_true", help="also show what the v1 engine did")
    args = parser.parse_args()

    if not CORPUS.exists():
        print(f"No corpus at {CORPUS} -- nothing to replay.")
        return

    with open(CORPUS) as f:
        history = json.load(f)
    observations = price_history.deduped_observations(history)
    today = datetime.now(timezone.utc)

    print(f"Corpus: {CORPUS}")
    print(f"  distinct listings: {len(observations)}")
    dates = sorted({o.get("date") for o in observations if o.get("date")})
    print(f"  date range: {dates[0]} .. {dates[-1]}" if dates else "  date range: unknown")
    basis = collections.Counter(o.get("basis", "asking") for o in observations)
    print(f"  basis: {dict(basis)}")
    print()

    if args.legacy:
        table, levels, flagged = replay_legacy(observations, args.min_comps)
        print("--- v1 engine (hierarchical, price-tier fallback) ---")
        print("  buckets:", {lvl: len(table[lvl]) for lvl in comps.COMP_LEVELS})
        print("  level used:", dict(levels))
        print(f"  WOULD FLAG: {len(flagged)}")
        for savings, pct, level, stats, obs in sorted(flagged, reverse=True, key=lambda f: f[0])[:8]:
            print(
                "    ${:>8.2f} ({:>3.0f}%) via {:<11s} median ${:<8.2f} n={:<3d} <- ${:.2f} {} {}".format(
                    savings, pct, level, stats.median, stats.sample_size, obs["price"], obs["player"],
                    obs.get("set_name") or "",
                )
            )
        print()

    engine, levels, confidences, blocked, flagged = replay_new(observations, args.min_comps, today)
    print("--- CardPro 2.0 engine ---")
    print("  buckets:", engine.coverage())
    print("  level used:", dict(levels))
    print("  confidence:", dict(confidences))
    print("  quality gates hit:", dict(blocked))
    print("  skipped observations (unusable):", engine.skipped_observations())
    print(f"  WOULD FLAG: {len(flagged)}")
    for savings, pct, match, obs in sorted(flagged, reverse=True, key=lambda f: f[0])[:8]:
        print(
            "    ${:>8.2f} ({:>3.0f}%) via {:<11s} median ${:<8.2f} n={:<3d} conf={:<6s} <- ${:.2f} {}".format(
                savings, pct, match.level, match.stats.median, match.stats.sample_size,
                match.confidence, obs["price"], obs["player"],
            )
        )
    if not flagged:
        print("    (nothing -- no card in this corpus can be honestly called underpriced)")


if __name__ == "__main__":
    main()
