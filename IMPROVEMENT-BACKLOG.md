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
| 9 | Base-card keying at the EXACT level only (is_base guard + card # required + extended NOT_BASE tokens; pre-1980 raw excluded). Unlocks 2,168 rows (flag-eligible keys 30% -> 51%). Ships after 2, 21, 22, 23 | ID-1, ID-12, PR-6 | 5 | 3 | 3 | 5 | todo |
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

### Identity items added from the identity audit (ranked into the order below)

| Rank | Item | Sources | Gain | Conf | Effort+Cx | Score | Status |
|---|---|---|---|---|---|---|---|
| 21 | "Signature Class" set read as an autograph (45 rows; produces a false flag: $1.49 base vs $57.99 auto median) -> mask the phrase | ID-2 | 3 | 5 | 1 | 15 | todo |
| 22 | Autograph words missed: Autographs, AUTOS, (AU), RPA (~160 rows, median ask $55) | ID-3 | 3 | 4 | 1 | 12 | todo |
| 23 | Print runs missed: "#/50", "# /75", "/150 2025" (152 rows, median $50) | ID-4 | 3 | 4 | 1 | 12 | todo |
| 24 | "Green Wave" Tulane mask deletes a real Prizm parallel (23 rows) | ID-6 | 2 | 5 | 1 | 10 | todo |
| 25 | Slabs read as raw: SGC GEM 10, CGC 10GM, PSA-10, BVG, ISA, GMA, dual grades (31 fixed) | ID-5 | 3 | 4 | 2 | 6 | todo |
| 26 | Set vocabulary: BBM, Fleer Ultra, Goodwin, Flair, Fleer Metal, Pro Debut; split Bowman University Chrome and OPC Platinum out of their parents | ID-7 | 2 | 4 | 2 | 4 | todo |
| 27 | Parallel plurals/abbreviations (133 rows) | ID-8 | 1 | 3 | 1 | 3 | todo |
| 28 | Multi-player autos with a non-watchlist co-player; quantity/card-set lot patterns | ID-9, ID-10 | 1 | 3 | 2 | 1.5 | todo |
| 30 | Print-run follow-ups: "1/1" exempt from the date guard; bare-run match must skip the denominator of a rejected date ("PSA 9 / 10 2024") (review of 7ef15b9) | review3 | 2 | 4 | 1 | 8 | todo |
| 29 | Stored corpus variant fields go stale after parser fixes (variant_of_observation prefers stored fields; replay_corpus ignores variant) | ID-11 | 2 | 4 | 2 | 4 | todo |

Execution order (money math first, then identity fixes that must precede base keying): 1, 2, 3, 5, 7, 21, 22, 23, 24, 6, 9, 4, 12, 10, 11, 25, 26, 8, 13, 14, 29, then the rest.

## Proposals only (not executable here)

- Workflow schedule: crons `47 11` + `47 15`, backup keyed by `case` (LR-2, LR-4, LR-7). Then a 3-hourly Browse-only poll (Phase 2) once corpus growth is controlled. Diff in the report.
- Sold data: PriceCharting Legendary $49/mo (API, guide prices, internal use) as a 2027 option; Terapeak manual lookups now. Marketplace Insights application (low odds).
- `git gc` to clear stray temp objects from an interrupted fetch (harmless; not run).
