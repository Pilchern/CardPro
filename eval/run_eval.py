"""CardPro eval: the yardstick every change is measured against.

    python -m eval.run_eval                # both splits + corpus-wide metrics
    python -m eval.run_eval --split tune   # tuning split only (use while iterating)
    python -m eval.run_eval --json out.json

What it does, offline and read-only:

1. Rebuilds the comp corpus from the stored titles through TODAY's code
   (main._build_listing -> main.record_observations), so a parser or
   negative-signal change flows into the comps exactly as it would in
   production. Rows stored without a title are kept as stored.
2. Runs every eval candidate (eval/candidates.json) through the real
   main.evaluate_listings against that corpus (the listing excludes itself).
3. Scores the system against the reconciled labels (eval/labels.json).
   Labels marked UNVERIFIED are excluded from scoring.

Honest limits, printed with the numbers: every label sale range is
ASKING-sourced (no sold data exists), so pricing error vs SOLD is not
measurable; listing age is not stored anywhere, so age-at-flag is not
measurable; flags/day is an in-sample corpus replay, not a forward test.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import comps, main, observability, price_history  # noqa: E402
from src.config import load_config  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
CANDIDATES = EVAL_DIR / "candidates.json"
LABELS = EVAL_DIR / "labels.json"
TODAY = datetime(2026, 10, 16, tzinfo=timezone.utc)
KEY_FIELDS = ("year", "set_name", "parallel")


def _tier(listing) -> str:
    """The system's tier for a listing. Uses listing.deal_tier when the code
    provides one; before tiers existed, an opportunity counted as Maybe."""
    tier = getattr(listing, "deal_tier", None)
    if tier:
        return tier
    return "Maybe" if listing.is_opportunity else "Pass"


def offline_config():
    """The shipped config for an offline replay: placeholder mail credentials
    (nothing is sent) and no eBay credentials (nothing is fetched). Shared
    with scripts/replay_corpus.py."""
    os.environ.setdefault("GMAIL_ADDRESS", "eval@example.com")
    os.environ.setdefault("GMAIL_APP_PASSWORD", "not-a-real-password")
    os.environ.pop("EBAY_CLIENT_ID", None)
    os.environ.pop("EBAY_CLIENT_SECRET", None)
    return load_config()


def listing_from_row(cfg, row, player, listing_type=None):
    """A stored row (corpus or candidate) rebuilt as the Listing production
    builds from its title: main._build_listing with the row's own player, as
    the Browse path passes it. None when the title does not name the player.
    Shared with scripts/replay_corpus.py."""
    listing = main._build_listing(
        cfg, listing_id=row["id"], source="ebay-alert", title=row["title"],
        price=row["price"], url=row["id"], players=[player],
        listing_type=listing_type or row.get("listing_type") or "unknown",
    )
    if listing is not None:
        listing.title_truncated = bool(row.get("title_truncated"))
    return listing


def rebuild_corpus(cfg, stored: dict) -> tuple[dict, list]:
    """Re-record every titled row through today's code, on its own date."""
    history: dict = {}
    listings_by_row = []
    by_date = collections.defaultdict(list)
    untitled = []
    for key, rows in stored.items():
        player, _, card_type = key.partition("|")
        for row in rows:
            if not row.get("title"):
                untitled.append((key, row))
                continue
            listing = listing_from_row(cfg, row, player)
            if listing is None:
                continue
            by_date[row["date"]].append(listing)
            listings_by_row.append(listing)
    for date in sorted(by_date):
        main.record_observations(by_date[date], history, date)
    for key, row in untitled:
        history.setdefault(key, []).append(dict(row))
    return history, listings_by_row


def build_engine(cfg, observations, today=TODAY):
    """The comp engine exactly as main.run builds it (src/main.py), from the
    config's valuation settings. Shared with scripts/replay_corpus.py."""
    return comps.CompEngine(
        observations,
        min_comps_required=cfg.valuation_min_comps_required,
        today=today,
        half_life_days=cfg.valuation_half_life_days,
        stale_after_days=cfg.valuation_stale_after_days,
        max_dispersion=cfg.valuation_max_dispersion,
        mad_threshold=cfg.valuation_mad_threshold,
        min_distinct_comp_dates=cfg.valuation_min_distinct_comp_dates,
        min_comp_span_days=cfg.valuation_min_comp_span_days,
    )


def key_resolution(listings) -> dict:
    """same_card-complete rate and per-field resolution over listings."""
    n = len(listings)
    out = {"n": n}
    if not n:
        return out
    for field in KEY_FIELDS + ("card_number",):
        out[field] = sum(1 for l in listings if getattr(l.card_identity, field).value is not None) / n
    out["grader_if_graded"] = (
        sum(1 for l in listings if l.card_type == "graded" and l.grader) /
        max(1, sum(1 for l in listings if l.card_type == "graded"))
    )
    out["same_card_key"] = sum(
        1 for l in listings if all(getattr(l.card_identity, f).value is not None for f in KEY_FIELDS)
    ) / n
    out["exact_key"] = sum(
        1 for l in listings
        if all(getattr(l.card_identity, f).value is not None for f in KEY_FIELDS + ("card_number",))
    ) / n
    return out


#: Naming aliases that key consistently and so are not identity errors:
#: "Silver Prizm" vs "Silver", "Topps Series 2" vs "Topps", "Holo Foil" vs
#: "Holo". Only spelling is forgiven here -- a dropped colour or qualifier
#: ("Fast Break" for "Fast Break Purple") still counts as wrong.
_ALIAS_WORDS = {"panini", "prizm", "prizms", "foil", "series", "1", "2", "prospects", "chrome"}


def _norm(value):
    if value is None:
        return None
    words = str(value).strip().lower().replace("-", " ").replace("&", " ").split()
    kept = [w for w in words if w not in _ALIAS_WORDS]
    return " ".join(kept or words)


def _system_parallel(listing):
    ident = listing.card_identity
    if ident.parallel.value is not None:
        return ident.parallel.value
    if ident.is_base.value:
        return "Base"
    return None


def score(items, labels) -> dict:
    """items: [(candidate, listing)] already evaluated."""
    res = collections.Counter()
    field_hits = collections.Counter()
    field_total = collections.Counter()
    tier_pairs = []
    for cand, listing in items:
        label = labels.get(cand["eval_id"])
        if not label or label.get("status") == "UNVERIFIED":
            res["excluded_unverified"] += 1
            continue
        res["scored"] += 1
        blocked = listing is None or listing.rejection_reason in main.SIGNAL_TO_REASON.values()
        if not label["is_single"]:
            res["noise"] += 1
            res["noise_rejected"] += int(blocked)
        else:
            res["singles"] += 1
            res["singles_wrongly_blocked"] += int(blocked)
            if listing is not None:
                ident = listing.card_identity
                resolved = all(getattr(ident, f).value is not None for f in KEY_FIELDS)
                res["singles_resolved"] += int(resolved)
                # Version-true: what the engine itself valued at exact/same_card.
                res["singles_valued_flag_level"] += int(listing.comp_level_matched in ("exact", "same_card"))
                lid = label["identity"]
                if resolved:
                    checks = {
                        "year": (ident.year.value, lid.get("year")),
                        "set_name": (ident.set_name.value, lid.get("set_name")),
                        "parallel": (_system_parallel(listing), lid.get("parallel")),
                        "card_number": (ident.card_number.value, lid.get("card_number")),
                    }
                    all_ok = True
                    for f, (sys_v, lab_v) in checks.items():
                        if sys_v is None or lab_v is None:
                            continue
                        field_total[f] += 1
                        ok = _norm(sys_v) == _norm(lab_v)
                        field_hits[f] += int(ok)
                        all_ok &= ok
                    res["resolved_fully_correct"] += int(all_ok)
                if label["identity"].get("grader"):
                    field_total["grade"] += 1
                    field_hits["grade"] += int(
                        (listing.grader or "").upper() == str(label["identity"]["grader"]).upper()
                        and str(listing.grade) == str(label["identity"].get("grade"))
                    )
        sys_tier = "Pass" if listing is None else _tier(listing)
        tier_pairs.append((sys_tier, label["deal_tier"] if label["is_single"] else "Pass"))

    out = dict(res)
    out["key_resolution_singles"] = res["singles_resolved"] / max(1, res["singles"])
    out["valued_at_flag_level_singles"] = res["singles_valued_flag_level"] / max(1, res["singles"])
    out["identity_accuracy_resolved"] = res["resolved_fully_correct"] / max(1, res["singles_resolved"])
    out["field_accuracy"] = {f: round(field_hits[f] / field_total[f], 3) for f in field_total}
    out["noise_rejection_rate"] = res["noise_rejected"] / max(1, res["noise"])
    out["singles_false_block_rate"] = res["singles_wrongly_blocked"] / max(1, res["singles"])
    for tier in ("Strong", "Maybe"):
        flagged = [p for p in tier_pairs if p[0] in (("Strong",) if tier == "Strong" else ("Strong", "Maybe"))]
        truth = [p for p in tier_pairs if p[1] in (("Strong",) if tier == "Strong" else ("Strong", "Maybe"))]
        tp = [p for p in flagged if p in truth]
        out[f"{tier}+_flagged"] = len(flagged)
        out[f"{tier}+_labelled"] = len(truth)
        out[f"{tier}+_precision"] = (len(tp) / len(flagged)) if flagged else None
        out[f"{tier}+_recall"] = (len(tp) / len(truth)) if truth else None
    flagged_any = [p for p in tier_pairs if p[0] != "Pass"]
    out["false_positive_rate"] = (
        sum(1 for p in flagged_any if p[1] == "Pass") / len(flagged_any) if flagged_any else None
    )
    return out


def evaluate_candidates(cfg, engine, candidates):
    items = []
    for cand in candidates:
        listing = listing_from_row(cfg, cand, cand["player"], listing_type="fixed_price")
        if listing is not None:
            main.evaluate_listings([listing], engine, cfg, observability.RunStats())
        items.append((cand, listing))
    return items


def corpus_flags(cfg, engine, listings) -> dict:
    """In-sample replay: every corpus listing valued against the rest."""
    stats = observability.RunStats()
    fixed = [l for l in listings if not l.is_auction]
    main.evaluate_listings(fixed, engine, cfg, stats)
    tiers = collections.Counter(_tier(l) for l in fixed)
    flagged = [l for l in fixed if _tier(l) != "Pass"]
    days = len({row_date for row_date in getattr(cfg, "_eval_dates", [])}) or 1
    profits = [l.economics.expected_profit for l in flagged if l.economics is not None]
    return {
        "listings": len(fixed),
        "valued_at_flag_level": round(
            sum(1 for l in fixed if l.comp_level_matched in ("exact", "same_card")) / max(1, len(fixed)), 4),
        "tiers": dict(tiers),
        "flags_total": len(flagged),
        "flags_net_profit_lt_15": sum(1 for p in profits if p < 15),
        "median_flag_net_profit": round(statistics.median(profits), 2) if profits else None,
        "flags_per_day": round(len(flagged) / days, 2),
        "days": days,
    }


def main_(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("tune", "holdout", "both"), default="both")
    parser.add_argument("--json")
    parser.add_argument("--no-corpus", action="store_true", help="skip the corpus-wide replay")
    args = parser.parse_args(argv)

    cfg = offline_config()
    stored = json.loads((ROOT / "data" / "ebay_alert_price_history.json").read_text())
    cfg._eval_dates = sorted({r["date"] for rows in stored.values() for r in rows})
    history, corpus_listings = rebuild_corpus(cfg, stored)
    engine = build_engine(cfg, price_history.deduped_observations(history))

    candidates = json.loads(CANDIDATES.read_text())
    labels = {l["eval_id"]: l for l in json.loads(LABELS.read_text())} if LABELS.exists() else {}
    items = evaluate_candidates(cfg, engine, candidates)

    report = {"labels_loaded": len(labels)}
    report["corpus_key_resolution"] = {k: round(v, 4) if isinstance(v, float) else v
                                       for k, v in key_resolution(corpus_listings).items()}
    splits = ("tune", "holdout") if args.split == "both" else (args.split,)
    for split in splits:
        subset = [(c, l) for c, l in items if c["split"] == split]
        report[split] = score(subset, labels)
    if not args.no_corpus:
        report["corpus_replay"] = corpus_flags(cfg, engine, corpus_listings)
    report["not_measurable"] = {
        "pricing_mdape_vs_sold": "no SOLD-sourced labels exist",
        "median_listing_age_at_flag": "listing start time is not stored",
    }
    text = json.dumps(report, indent=1, default=str)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
