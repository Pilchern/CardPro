# Decisions

## Shipped
| Date | Item | Branch / PR | Evidence |
|---|---|---|---|
| 2026-10-17 | 10-16 unattended pass: net-profit gate (max($15,10%)), fee model, MAX per listing, identity fixes (compound parallels, sets, autos, print runs, slabs), base-card exact keying, live re-parse, deal tier + summary line, sold-comp engine rules, flips.csv, dedupe all printed cards, $150 capital cap, Browse $20 floor + failure warning, --alert-only mode, 45-day retention | cardpro-auto-2026-10-16 / PR #25 (merged) | REPORT-2026-10-16.md, CHANGELOG-AUTO.md |

## Rejected
| Date | Item | Branch / PR | Inferred reason |
|---|---|---|---|

## Dropped (by PM)
| Date | Item | Reason |
|---|---|---|
| 2026-10-20 | Sold proxy from corpus disappearance | last_seen only moves when the newest-200 search re-returns an item; rows roll off the page sold or not. ~14 of ~429 eligible buckets would get >= 2 points in 14 days, and none of them separate sold from ended. |
| 2026-10-20 | Schedule primary at 11:47 UTC | Review: min lateness 2.14 h (52 runs) puts the start 3 min after the alert burst; replaced by 12:47. |

## Do not propose
- Scraping eBay sold pages, 130point, Card Ladder, or any HTML scraping (Nick, standing exclusion).
- Paid data subscriptions (PriceCharting etc.) until Nick asks (REPORT-2026-10-16 decision 4).
- Grading branch (keep off; economics don't clear at current PSA/SGC prices, decision 3).
- A <= $50 raw price band (cannot net $15, decision 2).
- Adding more saved searches (Browse supplies ~90% of listings, decision 5).
- New eBay API call types (getItem re-checks, sold tracking) until N1 is answered (API License §9.5(e), §8.1.4.2).
- Anything that needs Nick to sign up, enter data by hand, or run a chore as its main value path (see taste).

## Nick's taste (patterns, updated each run)
- 2026-10-20: Merged the whole 10-16 PR within a day, including +1.2k LOC. Accepts large, well-evidenced code changes.
- 2026-10-20: Did NOT do the manual follow-ups from that report in 4 days: 0 sold comps entered, 0 flips logged, schedule diff not applied. Inference: anything that needs his hands stalls. Ship it as a mergeable PR or automate it; never as a to-do for him.
