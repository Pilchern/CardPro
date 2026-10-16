# CHANGELOG-AUTO (2026-10-16 unattended pass)

Every attempted change: what, why, tune-eval delta vs the previous kept commit, net LOC (code / tests), risk, reviewer verdict, commit. Baseline numbers in BASELINE.md. Holdout is run only after every 5 shipped changes.

Tune baseline: Maybe+ flagged 7, precision 0.143, recall 1.0, FP rate 0.857. Corpus replay: 22 flags / 52 days, 16 net < $15, median net $1.89.

| # | Change | Why | Tune delta | Corpus delta | Net LOC code / tests | Risk | Review | Commit |
|---|---|---|---|---|---|---|---|---|
| 1 | Fee model to current eBay rates + owner params (FVF 13.25% on tax-incl. total = 14.25% eff., $0.40/$0.30 per-order, $0.82 envelope raw <$20 else $6 tracked, supplies $0.50/$1, buyer tax 10.25%, unknown inbound $4.50, haircut 20% ASKING / 5% SOLD) | Money math overstated net by $2-4 per flip; flat $5 ship-out wrong both ways (MR-1..7, PR-2, DS-3) | Maybe+ flagged 7 -> 2; precision 0.143 -> 0.5; FP 0.857 -> 0.5; recall 1.0 | flags 22 -> 6; net<$15 16 -> 4; median net $1.89 -> $9.65 | +112 / +149 | Low: arithmetic independently re-derived by reviewer; gross gate stricter because cost now includes tax + assumed shipping | SHIP | 46b6dc0 |
| 2 | Deal gate on net profit >= $15; ACT NOW needs its $ bar on net | 16 of 22 baseline flags netted < $15 (PR-1, DS-1) | Maybe+ flagged 2 -> 1; precision 0.5 -> 1.0; FP 0.5 -> 0.0; recall 1.0 | flags 6 -> 2; net<$15 4 -> 0; median net $30.80 | +29 / +50 | Low; ends fixed-price cheap-card and collector-buy opportunities (intended) | REVISE (tests asserted unreachable states, stale comment) -> fix queued | 464767c |
