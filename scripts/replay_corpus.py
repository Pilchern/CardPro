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

The headline "WOULD FLAG" count is the PRODUCTION deal gate: every titled
observation is rebuilt as a Listing (main._build_listing) and run through
main.evaluate_listings with the shipped config, so it applies the same gates
in the same order as the daily run -- gross discount and min savings, then the
net-profit floor (main.required_net_profit: max($15, 10% of value) as
shipped), after fees, both shipping legs, supplies and buy tax. The old
gross-discount-only count (30% and $10 off the median, the pre-10-16 rule) is
printed on a secondary line so earlier numbers stay comparable.

    python -m scripts.replay_corpus [--legacy] [--min-comps N] [--reextract]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import card_identity, comps, matcher, observability, price_history  # noqa: E402
from src import main as pipeline  # noqa: E402

CORPUS = Path(__file__).resolve().parent.parent / "data" / "ebay_alert_price_history.json"


def replay_new(observations, min_comps, today):
    """Comp-level statistics, plus the GROSS-discount-only would-flag list
    (30% and $10 off the median, nothing netted out). That list is the
    pre-10-16 gate, kept for comparability and because eval/build_candidates
    draws its "engine_flag" stratum from it; the headline count is
    replay_production_gate below."""
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
            # The printing variant, as main.evaluate_listings passes it.
            # Without it every lookup asked for the plain card, so the replay
            # could not see an autograph or print-run fix at all.
            is_autograph=obs.get("is_autograph") or False,
            relic=obs.get("relic"),
            print_run=obs.get("print_run"),
            is_serial_numbered=obs.get("is_serial_numbered") or False,
            title_truncated=bool(obs.get("title_truncated")),
            is_base=obs.get("is_base"),
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


def listings_from_observations(cfg, observations):
    """Rebuild each titled observation as the Listing production would have
    built from that title, the way eval/run_eval.py does: main._build_listing
    with the row's own player (as the Browse path passes it), its stored
    listing type and truncation flag. Shipping is not stored, so it is
    unknown here, exactly as on an alert listing, and economics.evaluate
    charges the configured assumed inbound shipping for it.

    Returns (listings, skipped): rows without a title, or whose title no
    longer names the player, cannot be rebuilt and are counted, not gated.
    """
    listings, skipped = [], 0
    for obs in observations:
        title = obs.get("title")
        listing = pipeline._build_listing(
            cfg, listing_id=obs.get("id"), source="ebay-alert", title=title,
            price=obs["price"], url=obs.get("id"), players=[obs["player"]],
            listing_type=obs.get("listing_type") or "unknown",
        ) if title else None
        if listing is None:
            skipped += 1
            continue
        listing.title_truncated = bool(obs.get("title_truncated"))
        listings.append(listing)
    return listings, skipped


def replay_production_gate(observations, engine, cfg):
    """The production deal decision over the corpus.

    Not a re-implementation: it hands the rebuilt listings to
    main.evaluate_listings, so every gate the daily run applies is applied
    here, in the same order -- no price, negative signals, multi-player,
    truncated grade, cheap commodity, comp lookup, economics, auctions
    diverted, context-only level, gross discount, min savings, and the
    net-profit floor (main.required_net_profit). What the replay cannot
    reproduce is its INPUT, not the gate: shipping is unknown on every row
    (assumed_inbound_shipping is charged), and each listing is valued against
    the whole corpus rather than the corpus as it stood on its own day.

    Returns (flagged, stats, skipped) where flagged is
    [(listing, required_net)], most profitable first.
    """
    listings, skipped = listings_from_observations(cfg, observations)
    stats = observability.RunStats()
    pipeline.evaluate_listings(listings, engine, cfg, stats)
    flagged = [
        (listing, pipeline.required_net_profit(cfg, listing.comp_median))
        for listing in listings
        if listing.is_opportunity
    ]
    flagged.sort(key=lambda item: item[0].economics.expected_profit, reverse=True)
    return flagged, stats, skipped


def _load_config():
    """The shipped config, without needing mail credentials: the replay sends
    nothing, but load_config insists on them (eval/run_eval.py does the same)."""
    from src.config import load_config

    os.environ.setdefault("GMAIL_ADDRESS", "replay@example.com")
    os.environ.setdefault("GMAIL_APP_PASSWORD", "not-a-real-password")
    return load_config()


def report_production_gate(flagged, stats, skipped, gross_count, days) -> None:
    print(f"  WOULD FLAG: {len(flagged)}  (production gate: main.evaluate_listings, "
          f"net >= required net; {days} corpus day(s))")
    print(f"  gross-discount only (pre-10-16 gate, 30% and $10 off median): {gross_count}")
    if skipped:
        print(f"  not gated (no title, or title no longer names the player): {skipped}")
    rejected = {
        reason: n for reason, n in stats.rejections.counts().items()
        if reason in ("below_discount_threshold", "below_min_savings", "below_min_net_profit")
    }
    if rejected:
        print("  flag-eligible but rejected by:", rejected)
    for listing, required in flagged:
        e = listing.economics
        print(
            "    net ${:>7.2f} (needs ${:>6.2f})  gross ${:>7.2f} ({:>3.0f}%) via {:<9s} "
            "median ${:<8.2f} n={:<3d} conf={:<6s} <- ${:.2f} {} | {}".format(
                e.expected_profit, required, listing.dollar_savings, listing.pct_under_market,
                listing.comp_level_matched, listing.comp_median, listing.comp_sample_size,
                listing.comp_confidence, listing.price, listing.player, listing.title,
            )
        )
    if not flagged:
        print("    (nothing -- no card in this corpus clears the production deal gate)")


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



#: The fields each flag-eligible comp level requires, narrowest first. Kept
#: in the same order as comps._key_exact / comps._key_same_card so this stays
#: a description of the real engine rather than a second opinion about it.
IDENTITY_FIELDS = ("year", "set_name", "parallel", "card_number")
LEVEL_REQUIREMENTS = {
    "exact": ("year", "set_name", "parallel", "card_number"),
    "same_card": ("year", "set_name", "parallel"),
    "same_set": ("year", "set_name"),
}


def _resolved(obs, field):
    value = obs.get(field)
    return value is not None and str(value).strip() != ""


def _first_missing(obs, fields):
    """The narrowest field that stops this observation forming a key.

    Reported one-per-observation rather than as independent per-field rates,
    because the fields are not independent: a listing missing both set and
    parallel is one problem to fix, not two, and fixing parallel alone would
    move it nowhere. Attributing each blocked listing to its FIRST blocker
    is what makes this list a work queue instead of a tally.
    """
    for field in fields:
        if not _resolved(obs, field):
            return field
    return None


def reextract(observations) -> list:
    """Re-derive every identity field from the stored title.

    The corpus stores what the extractor produced, not what it was given, so
    until titles were recorded a change to card_identity.py could not be
    measured against anything except invented examples. With them, `--reextract`
    replays the real inputs through the current parser and the identity KPI
    below reports the parser as it is today rather than as it was on the day
    each row was written.

    Rows recorded before titles were stored have none and are passed through
    untouched -- they keep the fields they were written with, which is the
    honest thing to do with a row whose input is gone.
    """
    updated = []
    for obs in observations:
        title = obs.get("title")
        if not title:
            updated.append(obs)
            continue
        identity = card_identity.extract_card_identity(title)
        grade_info = matcher.detect_grade_details(title)
        fresh = dict(obs)
        fresh.update(
            year=identity.year.value,
            set_name=identity.set_name.value,
            parallel=identity.parallel.value,
            card_number=identity.card_number.value,
            manufacturer=identity.manufacturer.value,
            is_base=identity.is_base.value,
            card_type=grade_info.card_type,
            grader=grade_info.grader,
            grade=grade_info.grade,
            qualifier=grade_info.qualifier,
            is_autograph=identity.is_autograph.value,
            relic="patch" if identity.is_patch.value else ("relic" if identity.is_memorabilia.value else None),
            print_run=identity.print_run.value,
            is_serial_numbered=identity.is_serial_numbered.value,
        )
        updated.append(fresh)
    return updated


def report_reextraction_delta(stored, refreshed) -> None:
    """Stored fields against what today's parser makes of the same titles.

    This is the measurement the extraction work has been missing. Until
    titles were recorded, a change to card_identity.py could be argued for
    and never shown to help; with them, the two columns below are the
    before and after of every change since a row was written.

    Only rows that actually carry a title are counted. Including the older
    ones would put the parser's improvement in a denominator full of rows it
    was never given a chance at, which understates it for no reason.
    """
    pairs = [
        (old, new)
        for old, new in zip(stored, refreshed)
        if old.get("title")
    ]
    if not pairs:
        print("  (no stored titles yet -- nothing to re-extract against)")
        return

    total = len(pairs)
    print("  re-extracted from {} stored title(s):".format(total))
    print("    {:<12} {:>10} {:>10} {:>9}".format("field", "as stored", "today", "change"))
    for field in IDENTITY_FIELDS + ("grader",):
        was = sum(1 for old, _ in pairs if _resolved(old, field))
        now = sum(1 for _, new in pairs if _resolved(new, field))
        arrow = "  --" if now == was else "{:+d}".format(now - was)
        print(
            "    {:<12} {:>9.1%} {:>10.1%} {:>9}".format(
                field, was / total, now / total, arrow
            )
        )

    # A field that changed VALUE matters as much as one that appeared:
    # "Chrome" becoming "Topps Chrome" moves a listing between buckets
    # without moving a coverage percentage at all.
    changed = sum(
        1
        for old, new in pairs
        if any(old.get(f) != new.get(f) for f in IDENTITY_FIELDS)
    )
    print(
        "    {} row(s) ({:.1%}) would be keyed differently today".format(changed, changed / total)
    )


def report_title_coverage(observations) -> None:
    with_titles = sum(1 for obs in observations if obs.get("title"))
    total = len(observations) or 1
    print("  stored titles: {} ({:.1%}) -- --reextract replays these through today's parser".format(
        with_titles, with_titles / total
    ))


def report_base_ceiling(observations, min_comps):
    """What treating an unread parallel as "base" would be worth.

    ``parallel=None`` currently means both "this is a base card" and "there
    may be a parallel we could not read", and the engine refuses to match on
    it -- correctly, because it cannot tell them apart. The cost is that base
    cards, which are the bulk of any alert feed, can never reach a level that
    declares a deal. card_identity now records an `is_base` field under a
    closed-world guard, but nothing keys on it yet.

    This sizes that decision on real data instead of arguing it. It is an
    UPPER BOUND and deliberately optimistic: it assumes every observation
    with a known set and no readable parallel really is a base card, which
    the guard would not. If the number here is small, the question is settled
    and the field should stay out of the bucket key.
    """
    fields = LEVEL_REQUIREMENTS["same_card"]
    buckets = collections.Counter()
    keyed = []
    candidates = 0
    for obs in observations:
        parallel = obs.get("parallel")
        assumed_base = parallel is None or str(parallel).strip() == ""
        if assumed_base:
            candidates += 1
        if _first_missing(obs, ("year", "set_name")) is not None:
            continue
        market = comps.market_key(
            obs.get("card_type"), obs.get("grader"), obs.get("grade"), obs.get("qualifier")
        )
        if market is None:
            continue
        key = (obs["player"], market, str(obs["year"]), str(obs["set_name"]),
               "BASE" if assumed_base else str(parallel))
        buckets[key] += 1
        keyed.append(key)
    usable = sum(1 for key in keyed if buckets[key] > min_comps)
    total = len(observations) or 1
    print("--- upper bound: what asserting \"base\" would unlock ---")
    print("  observations with no readable parallel: {} ({:.1%})".format(candidates, candidates / total))
    print(
        "  same_card if every one of those were base: {} distinct keys, "
        "{} observations ({:.1%}) in a bucket with >{} members".format(
            len(buckets), usable, usable / total, min_comps
        )
    )
    print("  (optimistic by construction -- the real guard asserts base far less often)")


def report_identity_coverage(observations, min_comps):
    """The identity KPI from docs/CARDPRO_2_AUDIT.md section 8.

    The engine can only declare a deal at `exact` or `same_card`, and both
    need a complete identity key AND enough other listings sharing it. A
    valuation engine that is perfectly correct still reports nothing if
    identity resolution never gets that far -- which is exactly the state
    this corpus is in. So measure both halves separately: how often the key
    can be BUILT, and how often a built key lands in a bucket deep enough
    to be usable.
    """
    total = len(observations)
    if not total:
        print("--- identity coverage ---")
        print("  (no observations)")
        return

    print("--- identity coverage (KPI: CARDPRO_2_AUDIT.md section 8) ---")
    print(f"  observations: {total}")

    print("  field resolved:")
    for field in IDENTITY_FIELDS + ("grader",):
        n = sum(1 for o in observations if _resolved(o, field))
        print(f"    {field:<12s} {n:>5d}  {n / total:>6.1%}")

    print("  complete key for level:")
    for level, fields in LEVEL_REQUIREMENTS.items():
        n = sum(1 for o in observations if _first_missing(o, fields) is None)
        print(f"    {level:<12s} {n:>5d}  {n / total:>6.1%}")

    # Which single missing field is holding each listing back, at the
    # narrowest level that could still flag a deal. This is the work queue.
    blockers = collections.Counter(
        _first_missing(o, LEVEL_REQUIREMENTS["same_card"]) or "(key complete)"
        for o in observations
    )
    print("  first blocker for a flag-eligible (same_card) key:")
    for field, n in blockers.most_common():
        print(f"    {field:<14s} {n:>5d}  {n / total:>6.1%}")

    # Even a complete key is worthless alone: it needs company. A bucket
    # needs min_comps OTHER listings before the engine will use it, so count
    # observations whose bucket is that deep -- the real achievable ceiling.
    for level, fields in (("same_card", LEVEL_REQUIREMENTS["same_card"]),
                          ("exact", LEVEL_REQUIREMENTS["exact"])):
        buckets = collections.Counter()
        keyed = []
        for obs in observations:
            if _first_missing(obs, fields) is not None:
                continue
            market = comps.market_key(
                obs.get("card_type"), obs.get("grader"), obs.get("grade"), obs.get("qualifier")
            )
            if market is None:
                continue
            key = (obs["player"], market) + tuple(str(obs.get(f)) for f in fields)
            buckets[key] += 1
            keyed.append(key)
        usable = sum(1 for key in keyed if buckets[key] > min_comps)
        print(
            f"  {level}: {len(buckets)} distinct keys, "
            f"{usable} observations ({usable / total:.1%}) in a bucket with "
            f">{min_comps} members"
        )
    report_base_ceiling(observations, min_comps)
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-comps", type=int, default=3)
    parser.add_argument("--legacy", action="store_true", help="also show what the v1 engine did")
    parser.add_argument(
        "--reextract",
        action="store_true",
        help="re-derive identity from the stored titles before replaying, so the KPI "
             "measures the parser as it is now rather than as it was when each row was written",
    )
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
    report_title_coverage(observations)
    if args.reextract:
        refreshed = reextract(observations)
        report_reextraction_delta(observations, refreshed)
        observations = refreshed
    print()

    report_identity_coverage(observations, args.min_comps)

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

    engine, levels, confidences, blocked, gross_flagged = replay_new(observations, args.min_comps, today)
    print("--- CardPro 2.0 engine ---")
    print("  buckets:", engine.coverage())
    print("  level used:", dict(levels))
    print("  confidence:", dict(confidences))
    print("  quality gates hit:", dict(blocked))
    print("  skipped observations (unusable):", engine.skipped_observations())
    flagged, stats, skipped = replay_production_gate(observations, engine, _load_config())
    report_production_gate(flagged, stats, skipped, len(gross_flagged), len(dates))


if __name__ == "__main__":
    main()
