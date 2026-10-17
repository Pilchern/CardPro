# CardPro baseline, 2026-10-16

Commit `aefb60d` code (eval harness added on top, no pipeline change). Reproduce:
`python -m eval.run_eval` (eval set: `eval/candidates.json`, labels: `eval/labels.json`).

## Eval set

- 155 listings drawn from the stored corpus, stratified (graded 20, numbered 20, noise-looking titles 22, key-complete 20, missing set 20, missing parallel 20, missing year 10, cheap <$15 8, engine would-flag 15). Seed fixed (`eval/build_candidates.py`).
- Split: 118 tune / 37 holdout (every 4th item per stratum -> holdout).
- Two independent labelers (no access to the parser). Reconciled (`eval/reconcile.py`): **121 VERIFIED, 34 UNVERIFIED** (excluded). Disagreements: parallel 17, set_name 15, card_number 3, year 2.
- Label sale ranges are 100% ASKING-sourced (no sold data exists). Labelers agreed on only 2 Maybe-tier items (E153, E154) and 0 Strong.
- Limitation (fact): the sample holds only 2 true non-singles (1 lot, 1 reprint), so noise rejection is measured on n=2.

## Metrics (all fact, measured 2026-10-16)

| Metric | Tune | Holdout |
|---|---|---|
| Scored (verified) | 93 | 28 |
| Key resolution, singles (same_card key) | 44.6% | 40.7% |
| Identity accuracy on resolved keys (all of year/set/parallel/card#) | 63.4% | 72.7% |
| Field accuracy: year / set / parallel / card # / grade | 100 / 87.8 / 70.7 / 97.2 / 100% | 100 / 72.7 / 72.7 / 100 / 100% |
| Lot/noise rejection | 0/1 | 0/1 |
| Singles wrongly blocked | 0% | 0% |
| Flags (Maybe+) | 7 | 2 |
| Flag precision (Maybe+) | 14.3% (1/7) | 0% (0/2) |
| Flag recall (Maybe+) | 100% (1/1) | n/a (0 labelled) |
| False-positive rate | 85.7% | 100% |
| Pricing MdAPE vs SOLD | not measurable (no SOLD labels) | same |
| Median listing age at flag | not measurable (start time not stored) | same |

Corpus-wide (10,353 titled listings, rebuilt through today's code):

| Metric | Value |
|---|---|
| same_card-complete key | 30.1% |
| exact-complete key | 22.2% |
| year / set / parallel / card # resolved | 93.7 / 68.8 / 33.8 / 59.8% |
| In-sample flags (52 days) | 22 (0.42/day) |
| Flags netting < $15 (app's own fee model, no time cost) | 16 of 22 |
| Median flag net profit | $1.89 |

## Reliability / latency (fact, from 100 GitHub runs via API)

- 15:00 UTC cron fired 22/22 days (09-24..10-15), 3.21-7.34 h late (median 4.72 h). No dropped runs.
- 19:00 backup: skipped 19/22 days; 2 redundant post-midnight scans sent a second email.
- Report lands 3.8-8.4 h after eBay's alert burst (median 5.3 h).
- Repo is public: Actions minutes unlimited. Full Browse+alerts run 113 s.
- Tests: 1,332 pass.

## What the baseline says

- The deal gate's false positives are money-math, not identity: of the 9 eval flags, 6 net between $0.13 and $4.43 under the app's own fees (E142-E146, E152). A net-profit gate removes them.
- The real identity errors on resolved keys are compound parallels collapsed to a shorter name (Fast Break Purple -> Fast Break, Red Seismic -> Red, Reactive Blue -> Blue, Green White & Purple -> Purple Prizm) and products read as parallels (Prizm Black, Topps Chrome Black -> "Black"). Those put different cards in one flag-eligible bucket.
