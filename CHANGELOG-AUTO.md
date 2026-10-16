# CHANGELOG-AUTO (2026-10-16 unattended pass)

Every attempted change: what, why, tune-eval delta vs the previous kept commit, net LOC (code / tests), risk, reviewer verdict, commit. Baseline numbers in BASELINE.md. Holdout is run only after every 5 shipped changes.

Tune baseline: Maybe+ flagged 7, precision 0.143, recall 1.0, FP rate 0.857. Corpus replay: 22 flags / 52 days, 16 net < $15, median net $1.89.

| # | Change | Why | Tune delta | Corpus delta | Net LOC code / tests | Risk | Review | Commit |
|---|---|---|---|---|---|---|---|---|
| 1 | Fee model to current eBay rates + owner params (FVF 13.25% on tax-incl. total = 14.25% eff., $0.40/$0.30 per-order, $0.82 envelope raw <$20 else $6 tracked, supplies $0.50/$1, buyer tax 10.25%, unknown inbound $4.50, haircut 20% ASKING / 5% SOLD) | Money math overstated net by $2-4 per flip; flat $5 ship-out wrong both ways (MR-1..7, PR-2, DS-3) | Maybe+ flagged 7 -> 2; precision 0.143 -> 0.5; FP 0.857 -> 0.5; recall 1.0 | flags 22 -> 6; net<$15 16 -> 4; median net $1.89 -> $9.65 | +112 / +149 | Low: arithmetic independently re-derived by reviewer; gross gate stricter because cost now includes tax + assumed shipping | SHIP | 46b6dc0 |
| 2 | Deal gate on net profit >= $15; ACT NOW needs its $ bar on net | 16 of 22 baseline flags netted < $15 (PR-1, DS-1) | Maybe+ flagged 2 -> 1; precision 0.5 -> 1.0; FP 0.5 -> 0.0; recall 1.0 | flags 6 -> 2; net<$15 4 -> 0; median net $30.80 | +29 / +50 | Low; ends fixed-price cheap-card and collector-buy opportunities (intended) | REVISE (tests asserted unreachable states, stale comment) -> fix queued | 464767c |
| 2r | Net-gate review fixes: tests assert shipped $15 floor (cheap/collector-buy now rejected), measured numbers in comment, _watch_why test | Reviewer REVISE on #2 | unchanged | unchanged | +1 / +60 | None | (fix of #2) -> #2 now SHIP | 98393c5 |
| 1t | Fee-model caveat text: discount includes assumed shipping + tax; docstrings; tax comments | Reviewer note on #1 | unchanged | unchanged | +1 / +1 | None | text only | 2a8cca5 |
| 21 | "Signature Class" is a set, not an autograph | 45 base/insert rows carried a false auto -> false flag ($1.49 base vs $57.99 auto median) (ID-2) | unchanged | 48 rows re-keyed (variant) | +4 / +17 | Low | SHIP (48/48 rows checked, 0 FP) | 83db027 |
| 22 | Autograph words: Autographs, AUTOS, Signatures, RPA, AU tag | ~170 real autos keyed as non-auto (ID-3) | unchanged | 181 rows re-keyed, all -> auto | +18 / +37 | Low | SHIP (167 titles read, 0 FP) | 10bc015 |
| 23 | Print runs after "#" and before a year | 150+ numbered cards keyed as unnumbered (ID-4) | unchanged | 164 rows re-keyed, all -> numbered | +15 / +35 | Low; 1/1-before-year still missed (0 corpus rows) | SHIP (159 titles read, 0 FP) | 7ef15b9 |
| 24 | "Green Wave" is a parallel; mask only "Tulane Green Wave" | Real Prizm parallel was deleted by a team mask (ID-6) | key res 0.446 -> 0.457; identity acc 0.634 -> 0.643; parallel acc 0.707 -> 0.714 | 23 rows re-keyed; +18 same_card, +16 exact | +2 / +23 | Low | SHIP (23/23 rows correct) | fd21a6f |
