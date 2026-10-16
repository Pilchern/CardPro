# Improvement backlog, 2026-10-16

Merged and deduped from the six Phase 1 audits (IDs: ID identity, PR pricing, DS deal scope, LR latency/reliability, WF workflow, MR market research). Full audit files are in the session scratchpad and summarised in REPORT-2026-10-16.md.

Score = (expected net-profit-per-flip gain x confidence) / (effort + complexity added), each on 1-5. Money-math bugs and identity unlocks rank first. Excluded outright: anything needing scraping, paid data, credentials, production workflow changes, or destructive ops (those are proposals in the report).

| Rank | Item | Sources | Gain | Conf | Effort+Cx | Score | Status |
|---|---|---|---|---|---|---|---|
| 1 | Fee model to current rates and owner params: FVF 13.25% on tax-inclusive total (14.25% effective), per-order $0.40 (>$10) / $0.30, ship-out by method ($0.82 Standard Envelope for raw sale <$20, $6.00 tracked otherwise), supplies $0.50 raw / $1 slab, unknown inbound shipping assumed $4.50 not $0, buyer tax 10.25% (Chicago, ASSUMPTION), haircut 20% on ASKING comps / 5% on SOLD | PR-2, DS-3, MR-1..7 | 5 | 5 | 2 | 12.5 | todo |
| 2 | Deal gate on net profit >= $15 (in addition to existing gates); same floor drives ACT NOW | PR-1, DS-1, DS-6, LR-12 | 5 | 5 | 1 | 25 | todo |
| 3 | Max offer/bid solved for the $15 net floor (fixed-price + Best Offer and auctions), replacing the 30%-gross offer trio and the 25%-of-value margin | WF-1, PR-8, DS-13 | 4 | 4 | 2 | 8 | todo |
| 4 | Deal tier (Strong/Maybe) + one-line phone summary per flag: tier, identity confidence, price+ship, MAX, sale range, ASKING/SOLD n, net, $/hr | DS tiers, WF-4, WF-5, PR-10 | 4 | 4 | 3 | 5.3 | todo |
| 5 | Focus: net-gated opportunities bypass the $50 ceiling up to a $150 capital cap | DS-2, PR-9 | 3 | 4 | 1 | 12 | todo |
| 6 | Compound parallels collapsed (Fast Break <color>, Red Seismic, Reactive Blue, multi-colour) and products read as parallels (Prizm Black, Chrome Black) -> mixed flag-eligible buckets | eval mismatches, ID-* | 3 | 4 | 2 | 6 | todo |
| 7 | Card-number normalisation (BD-72 = BD72 = bd72) so buckets and sold comps don't fragment | PR-7 | 3 | 4 | 1 | 12 | todo |
| 8 | Sold comps take over a bucket when >= 2 sold points exist (median + SOLD label from sold subset) | PR-3, PR-4 | 4 | 3 | 2 | 6 | todo |
| 9 | Base-card keying (parallel "Base" from a guarded is_base) | PR-6, ID-* | 4 | 2 | 3 | 2.7 | pending identity audit |
| 10 | Browse API newest pass: min price filter so the 200-item page isn't 77% sub-$20 cards; also cuts corpus growth ~75% | DS-7, LR-5 | 3 | 3 | 1 | 9 | todo |
| 11 | Browse API non-200 responses reach the email as a warning | LR-6 | 1 | 5 | 1 | 5 | todo |
| 12 | Dedupe every printed card, not only opportunities | WF-2 | 2 | 4 | 1 | 8 | todo |
| 13 | Corpus retention 90 -> 45 days (matches stale_after_days; keeps file under GitHub's 100 MB) | LR-5 | 1 | 4 | 1 | 4 | todo |
| 14 | pytest upper bound in requirements-dev (prod gate) | LR-11 | 1 | 5 | 1 | 5 | todo |
| 15 | Flip record (`data/flips.csv`) where each sale becomes a sold comp | WF-8 | 4 | 3 | 3 | 4 | todo |
| 16 | Listing age on flags (Browse `itemCreationDate`, else first-seen) | WF-10, DS-11 | 2 | 2 | 2 | 2 | todo |
| 17 | Cut email noise: CHEAP FINDS / CHEAP AUCTIONS / Craigslist links / long footer; short no-deal email | WF-3, WF-6, WF-7 | 2 | 3 | 3 | 2 | todo |
| 18 | Sold-comp schema: auto / relic / print_run fields | PR-5 | 2 | 3 | 2 | 3 | todo |
| 19 | comp_requests ranking by value x recency, exact identity | PR-11 | 2 | 3 | 2 | 3 | todo |
| 20 | Grading config block (off) with current PSA/SGC/BGS/CGC prices; no grading branch | MR-8, PR-12, DS-12 | 1 | 4 | 1 | 4 | todo |

## Proposals only (not executable here)

- Workflow schedule: crons `47 11` + `47 15`, backup keyed by `case` (LR-2, LR-4, LR-7). Then a 3-hourly Browse-only poll (Phase 2) once corpus growth is controlled. Diff in the report.
- Sold data: PriceCharting Legendary $49/mo (API, guide prices, internal use) as a 2027 option; Terapeak manual lookups now. Marketplace Insights application (low odds).
- `git gc` to clear stray temp objects from an interrupted fetch (harmless; not run).
