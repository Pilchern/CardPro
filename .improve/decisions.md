# Decisions

## Shipped
| Date | Item | Branch / PR | Evidence |
|---|---|---|---|
| 2026-10-20 | B4 actionable auctions: Browse itemEndDate 3-27 h window, MAX-first ranking, no-MAX cap 3, absolute end time, subject counts MAX auctions only | auto-improve/b4-actionable-auctions / PR #31 (merged by PM on green CI) | R1 research; review SHIP after base merge + DST-fallback removal |
| 2026-10-20 | B12 dedupe records only printed cards; opportunities exempt from browse cap | auto-improve/b12-dedupe-after-trim / PR #30 (merged by PM, 2d8ccdf) | reviewer repro: 3 of 8 cut deals lost forever; SHIP |
| 2026-10-20 | B2 email trim: browse cap 5, CHEAP FINDS off, 'No deals' subject; WATCH + MAX auctions exempt | auto-improve/b2-email-trim / PR #29 (merged by PM, 0d95b77) | 40 -> ~19-23 cards; 2 reviews (caught WATCH/dedupe loss) |
| 2026-10-20 | B11 ARCH-NOTES matches code after #25-#27 | auto-improve/b11-arch-notes / PR #28 (merged by PM, 51064c0) | spot-checked vs settings.json + main.py |
| 2026-10-20 | B1 schedule 12:47 UTC primary, both crons skip-if-ran-today | auto-improve/b1-earlier-schedule / PR #26 (merged by PM on Nick's instruction, 03b01c5) | 52-run lateness data; 2 reviews |
| 2026-10-20 | B3 replay headline = production gate (2 not 40); shared eval helpers | auto-improve/b3-replay-net-gate / PR #27 (merged by PM, 67f94f7) | eval byte-identical; 2 reviews |
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
- Anything that needs Nick to sign up, enter data by hand, or run a chore as its main value path (see taste).

## Nick's taste (patterns, updated each run)
- 2026-10-20: Merged the whole 10-16 PR within a day, including +1.2k LOC. Accepts large, well-evidenced code changes.
- 2026-10-20: Did NOT do the manual follow-ups from that report in 4 days: 0 sold comps entered, 0 flips logged, schedule diff not applied. Inference: anything that needs his hands stalls. Ship it as a mergeable PR or automate it; never as a to-do for him.
- 2026-10-20: Waved off the eBay API license risk ("they're not worried about me"). Don't escalate ToS/compliance again unless a change goes well beyond personal-scale use.
- 2026-10-20: Said "merge when done". The PM may merge reviewed, CI-green PRs itself; the WIP gate is less binding while that standing permission holds.
- 2026-10-20: Delegates taste calls ("do whatever you think is best"). Escalate less: decide, ship, and note the call. Escalate only irreversible actions or real money.
