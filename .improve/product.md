# CardPro: product

## Who it serves
One owner: Nick. Experienced, time-poor, flips sports cards on eBay as a side income. Reads the daily email on his phone.

## Core job
Surface underpriced eBay listings (watchlist players) that net >= max($15, 10% of value) after all fees, both shipping legs, supplies, and buy tax, fast enough to buy before someone else does. Zero false flags beats more flags.

## What "better" means (in priority order)
1. Trustworthy valuations: real SOLD evidence under flag-eligible buckets (today: zero sold comps; asking comps take a 20% haircut, which kills most flags).
2. More real flags per week at the same precision (flag yield).
3. Freshness: report lands within ~2 h of listings appearing.
4. Less Nick time: no manual chores the app could do; a short phone-readable email.
5. Reliability: no silent failures, corpus under GitHub's 100 MB file limit.

## Success metrics and baselines
Measured 2026-10-16 (REPORT-2026-10-16.md, fact unless noted). Post-merge baseline: see runs/2026-10-20.md.

| Metric | Baseline (10-16 final) | Target |
|---|---|---|
| Corpus flags / 52 days (replay) | 3 (0.06/day) | up, with 0 netting < $15 |
| Tune flag precision (Maybe+) | 100% (n=1) | hold |
| Identity accuracy, resolved keys (tune / holdout) | 80.5% / 80.0% | up |
| Live flag-eligible keys | 47.6% | up |
| Valued at flag-eligible level (corpus) | 11.3% | up |
| Sold comps in store | 0 | > 0 under top buckets, automatically |
| Report latency after alert burst | median 5.3 h | < 2 h |
| Tests | 1,628 pass | all green |

## Constraints (standing)
- Default branch `claude/sports-card-deal-scraper-6imq2v` (no main). Only Nick merges.
- Excluded: scraping eBay/130point/Card Ladder HTML, paid data, signups or chores needing Nick's action, touching `.env` or secrets.
- Eval: `python -m eval.run_eval` (tune/holdout); corpus replay `python -m scripts.replay_corpus --reextract`.
