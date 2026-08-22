# CardPro v2 — Audit & Roadmap

> **Superseded, kept for history.** This is the v2 audit. The current audit
> is **[CARDPRO_2_AUDIT.md](CARDPRO_2_AUDIT.md)**, which re-examined every
> conclusion below against measured production data and found that several
> of the "shipped" fixes in §9 and §11 did not actually solve the problem
> they claimed to. Read that first; read this for how CardPro got here.
>
> What this document got right: the diagnosis in §2 row 5 — that the comp
> bucket key was the single biggest accuracy ceiling in the system.
>
> What it got wrong: the fix. Adding identity-aware levels *above* the
> price-tier bucket left the price-tier bucket in place as the fallback, and
> because identity extraction was filling `parallel` on only 3% of listings,
> **76% of real valuations still came out of that circular fallback.** The
> hierarchy degraded gracefully into exactly the behaviour it was built to
> replace. Marking it "shipped" in §11 was premature — the code shipped, the
> outcome didn't.
>
> Also wrong: `near_exact` was specified as `(player, year, set, parallel,
> card_type)`, which pools a PSA 9 with a PSA 10, violating the project's own
> "never compare different grades" rule; and treating `parallel=None` on both
> sides as a match let unknown-matches-unknown produce a real false positive
> in production.

## 1. Current Architecture

**Lifecycle (email-alerts path, the one actually in production):**

```
eBay saved-search alert email
  → Gmail IMAP fetch (ebay_email_alerts.fetch_alert_messages)
  → HTML parse per email (extract_listings_from_html) → [{title, url, price}]
  → player match (matcher.match_player: all name-parts present as whole words)
  → grading detect (matcher.detect_grading: PSA/BGS/SGC/CSG + number regex)
  → rookie detect (matcher.detect_rookie_card: RC/ROOKIE regex)
  → Listing built (player_tier looked up from config, is_rookie_card set)
  → every matched listing's price appended to price_history (player, card_type, date, [now] id)
  → price_history.as_buckets() → {(player, card_type, price_tier): [prices]} → comps.build_comp_table() → median per bucket
  → flag_deals(): listing flagged iff pct_under_market ≥ threshold AND dollar_savings ≥ min_savings_dollars
  → enrich_truncated_grades(): for flagged+graded+truncated titles only, re-fetch real title from item page
  → dedupe.is_new_or_price_drop() against data/seen_listings.json
  → report.build_report() → ranked by $ saved → plain-text email
```

There is a second, currently-unused path (`ebay_api_enabled` branch) that
calls the real eBay Browse/Marketplace-Insights API — dead in practice
since the developer account was declined, but the code and its tests are
still live and correct for when/if that access is granted.

Craigslist is link-only by design (confirmed anti-automation fingerprinting
— not attempted). Facebook Marketplace is out of scope, never built.

**Modules:** `main.py` (orchestration), `ebay_email_alerts.py` (IMAP +
HTML extraction), `matcher.py` (player/grade/rookie keyword matching),
`comps.py` (price-tier bucketing + median), `price_history.py`
(self-building comp store for the alerts path), `dedupe.py` (seen-listings
store), `report.py` (email body), `config.py` (loads `.env` +
`config/*.json`), `models.py` (the `Listing` dataclass), `ebay_client.py`
+ `craigslist_links.py` (the unused-for-now API path / link builder).

**Storage:** two flat JSON files (`data/seen_listings.json`,
`data/ebay_alert_price_history.json`), atomic-write-via-tempfile, pruned by
age on every run. No database.

**Infra:** Python 3.9/3.12 CI via pytest (109 tests as of this audit), Mac
cron at ~8am, `.env` for secrets, failure-notification email on unhandled
exceptions, rotating log file.

## 2. Major Weaknesses — Classified

| # | Concern | Classification |
|---|---|---|
| 1 | Transparent, non-black-box design (every tag/number traceable) | **Correct, worth keeping** |
| 2 | "Send something, never go silent" (nothing-today + failure emails) | **Correct, worth keeping** |
| 3 | Refusal to fight anti-bot defenses (Craigslist/eBay) | **Correct, worth keeping** (explicit project constraint) |
| 4 | Price-tier bucketing to stop cheap/expensive contamination | **Correct so far, but too coarse** — see #5 |
| 5 | Comp bucket key is only `(player, card_type, price_tier)` — no set/year/parallel/serial# | **Fundamentally flawed.** This is the single biggest accuracy ceiling in the system. A 2024 base Prizm and a 2024 Prizm Silver /99 of the same player, both graded, both landing in the `100_plus` tier, are currently treated as comparable. Price-tiering only limits *how wrong* a bad comp can be — it doesn't make the comp *right*. |
| 6 | Duplicate listing observations inflate comps | **Fundamentally flawed — now fixed in this pass.** `ebay_alerts.lookback_days` defaults to 2, so a listing seen in yesterday's alert email is very often re-fetched and re-recorded today (overlapping IMAP search windows), and the same item can also match more than one saved search. Every such duplicate counted as an independent comp, systematically pulling every median toward whatever's currently listed. Fixed by tagging each observation with its listing id and collapsing repeat sightings to the latest price before bucketing (see `price_history.as_buckets`). |
| 7 | No distinction between auction (open, current bid) and fixed-price listings | **Missing capability, real false-positive risk.** A $40 current bid on an auction ending in 6 days is not a $40 price — it's very likely to rise. Flagging it as "60% under market" is misleading. Whether eBay's alert-email HTML reliably exposes listing type/bid count/end time is unverified — needs real sample inspection before building detection. |
| 8 | No shipping / total-cost modeling | **Missing capability.** `$40 item + $18 shipping` is reported as a $40 opportunity today. |
| 9 | No card-identity extraction (year/set/parallel/serial/auto/memorabilia) | **Missing capability** — prerequisite for #5, #7 (auction vs BIN sometimes correlates with set popularity), and most of Phases 3/7/8/10 in the brief. |
| 10 | `min_comps_required` gate exists, but no visible confidence tier (HIGH/MED/LOW) or sample recency in the report | **Needs improvement.** The report shows `n=X` but doesn't say whether X is 3 stale observations from 5 months ago or 40 from this week — both currently read the same to a human skimming the email. |
| 11 | Outlier handling is implicit (tiering only) — no detection of $1 typos, $9999 aspirational listings, or lot-of-cards titles | **Needs improvement.** |
| 12 | Observability: failure email exists for crashes, but a *silent* degradation (eBay changes their alert-email template and `extract_listings_from_html` starts returning 0 results while emails keep arriving) looks identical to "eBay legitimately sent nothing new" | **Needs improvement — this is the "no deals vs. system broke" gap the brief calls out explicitly.** |
| 13 | Player/card tests are solid for the matching logic that exists, but there is no fixture coverage for reprints, facsimile autos, lots, multi-player title collisions, wrong-year/wrong-parallel false comps | **Needs improvement.** |
| 14 | JSON flat-file storage | **Correct for current scale, revisit if the data model below gets adopted** — see §6. |

## 3. False Positive Risks (things CardPro is most likely to wrongly flag as deals today)

- **Wrong-card comps within a price tier.** A cheap reprint/college-uniform/lower-desirability version of a card sitting in the same price tier as a legitimately scarce parallel — median gets pulled toward whichever is more common, making the rare one look "expensive" and the common one occasionally look like a steal if a mispriced legitimate copy shows up.
- **Live auctions with a low current bid**, reported as if the current price were final.
- **Duplicate-listing comp inflation** (now fixed, see §2 row 6) — was making medians drift toward whatever happened to be currently listed rather than a real market distribution.
- **Truncation collateral damage beyond the grade digit.** `enrich_truncated_grades` only re-verifies the grade number; a truncated title could also be hiding "REPRINT", "LOT OF 5", "DIGITAL", or a different player entirely if the match happened to land on a substring before the cutoff. Not yet observed in production data, but not guarded against either.
- **Thin/stale comp samples treated the same as strong ones** — 3 old observations clear `min_comps_required` exactly the same as 40 fresh ones; nothing in the flagging math or report distinguishes them.

## 4. False Negative Risks (real deals CardPro likely misses)

- **Everything gated behind `min_comps_required` with zero comps in a bucket** — a genuinely rare, valuable card that happens to be the first-ever alert for that (player, card_type, tier) combination gets silently skipped, not flagged, not logged as "interesting but unscored." No signal at all reaches the report.
- **Misdescribed/underpriced listings** (Phase 15 territory) — a numbered rookie auto that the seller titled without the word "numbered" or "auto" won't get any bonus attention; it's scored exactly like a base card, so a real steal on a poorly-titled listing looks unremarkable.
- **Auctions ending soon at attractive current prices** are structurally invisible — with no listing-type detection, they're either flagged misleadingly (as a false positive, above) or filtered out entirely for lacking a stable enough price, depending on how they parse. Either way there's no dedicated "watch this auction" path today.
- **Cross-search-term duplicates**: if the same physical card matches two different saved searches (e.g., a player name appearing in two watchlist entries via a shared last name edge case), it's still only one real market data point but nothing currently recognizes that as the same underlying card vs. two separate ones for desirability signal purposes (comps dedupe now fixed; identity-level dedupe is not).

## 5. Data Limitations (things that cannot be reliably known from current sources)

- **No confirmed sold prices at all** on the active data path — every comp is either an asking price (email alerts) or, on the unused API path, active-listing prices when Marketplace Insights access isn't granted. This is a structural ceiling, not a bug: it can improve (more asking-price history accumulates) but can never become "real sold comps" without eBay API access this account doesn't have.
- **Auction end-state is unknown** — CardPro sees a listing once (when the alert fires), not its final sale price, so it can't verify what an auction actually closed at.
- **Seller reputation/feedback is not present in alert emails** — nothing has been captured or verified about whether this is fetchable from the emails vs. only the listing page.
- **Card identity fields (set, parallel, serial number, autograph, memorabilia) are not currently extracted at all** — everything downstream (comps, desirability, "why is this cheap") is bottlenecked on title-text-only, keyword-only signals.

## 6. Recommended Architecture

Keep the existing pipeline shape (email → extract → match → comp → flag →
report) — it's sound and the brief says not to rewrite working systems
without reason. The real gap is in the *middle*: what a "listing" and a
"comp" actually contain. Recommended evolution, roughly in the brief's own
phase order but re-grouped by what's actually buildable against this data
source:

1. **Card Identity Engine** (Phase 3): a title-parsing layer that extracts
   year / manufacturer / set / parallel / card# / serial / auto / rookie /
   grading into structured fields, each with a `confidence` and `source`,
   sitting between `matcher.py`'s current player/grade detection and
   `Listing` construction. Additive — nothing existing has to change to
   adopt it; `card_type`/`grader`/`grade`/`is_rookie_card` become derived
   fields of the richer structure instead of separate ad hoc regexes.
2. **Hierarchical comp matching** (Phase 4): once identity fields exist,
   replace the single `(player, card_type, price_tier)` bucket key with a
   tiered lookup — try an exact-identity bucket first, fall back to
   progressively broader buckets, and record which level matched as
   `comp_confidence` (HIGH/MEDIUM/LOW/INSUFFICIENT_DATA) on the `Listing`.
   The current price-tier bucketing becomes the bottom fallback rung
   instead of the only rung.
3. **Listing-id-aware price history** (Phase 5) — **done in this pass**,
   see §2 row 6.
4. **Total acquisition cost** (Phase 6): extend `Listing`/extraction with
   `shipping_price: Optional[float]` when the alert email or item page
   exposes it; rank and gate on total cost, not item price alone, and show
   "shipping unknown" plainly rather than assuming $0.
5. **Auction vs. fixed-price detection** (Phase 11): needs real alert-email
   HTML samples to build reliably — flagged as the one item in this list
   that needs a research/verification step before implementation, not just
   engineering.
6. **Desirability + long-term signals as separate visible fields**
   (Phases 7–9): once identity fields exist, this is mostly presentation —
   surface `is_numbered`, `has_autograph`, `parallel_name`, etc. next to
   the existing `player_tier`/`is_rookie_card` tags, same "show, don't
   blend" principle already in place.
7. **"Why is this cheap?" reasoning** (Phase 10): a small rules layer that
   runs *after* a listing clears the deal gate and looks for known
   discount explanations (different grader than the comp set, lower grade,
   reprint keyword, lot-of-N keyword) — surfaced as a `possible_reason`
   field, not a rejection, so weak listings are explained rather than
   silently dropped.
8. **Confidence-aware reporting** (Phase 18/19): reorganize the email into
   the sectioned format from the brief (Top Opportunities / Long-Term
   Targets / Target Card Hits / Watchlist / Manual Search Links) once
   confidence + identity fields exist to sort into them meaningfully.
9. **Observability** (Phase 23): cheap, high-value, no dependency on
   anything else — add a sanity check that logs/alerts when messages were
   found but zero listings were extracted (template-change canary), and
   surface comp-coverage stats (buckets with 0 / thin / healthy sample
   sizes) in the log each run.

**Explicitly deferred, not because they're bad ideas but because they need
either real sample data or a product decision first:** target-card
watchlist (Phase 14, straightforward but net-new scope — good P1
candidate once identity extraction exists to match against it), seller
risk (Phase 12, unclear if the data is even in the emails), mispriced-
listing detection (Phase 15, brief itself flags this as "design but don't
overclaim confidence"), AI layer (Phase 20 — genuinely useful for messy
title parsing eventually, but the deterministic identity engine should
exist and be trustworthy first, per the brief's own "AI only where
ambiguity benefits from it" principle).

## 7. Proposed Data Model

Extending `Listing` (not replacing) rather than a database migration —
JSON flat files remain adequate at this volume (tens of listings/day); a
SQLite migration is worth revisiting only if/when the identity-engine +
history size make ad hoc querying genuinely painful, not preemptively.

```
Listing (extends current dataclass):
  # existing: id, source, title, price, url, player, card_type, grader,
  # grade, title_truncated, player_tier, is_rookie_card,
  # comp_median, comp_sample_size, comp_is_fallback, pct_under_market,
  # dollar_savings
  + shipping_price: Optional[float]
  + total_cost: Optional[float]              # price + shipping, when known
  + listing_type: Optional[str]               # "fixed_price" | "auction" | "best_offer" | "unknown"
  + card_identity: CardIdentity                # see below
  + comp_confidence: str                       # "high" | "medium" | "low" | "insufficient_data"
  + comp_level_matched: str                    # "exact" | "near_exact" | "family" | "price_tier"
  + possible_discount_reason: Optional[str]
  + negative_signals: list[str]                # e.g. ["reprint", "unlicensed"]
  + positive_signals: list[str]                # e.g. ["numbered", "on_card_auto"]

CardIdentity (each field individually optional, with confidence):
  year: Field[int]
  manufacturer: Field[str]        # Panini, Topps, etc.
  set_name: Field[str]            # Prizm, Chrome, Donruss Optic, ...
  parallel: Field[str]            # Silver, Gold, Kaboom, Downtown, ...
  card_number: Field[str]
  serial_number: Field[str]       # "23/99" style, parsed to numerator/denominator when possible
  is_autograph: Field[bool]
  is_memorabilia: Field[bool]
  is_rookie: Field[bool]          # supersedes matcher.detect_rookie_card, same regex as one signal among others
  is_lot: Field[bool]             # "lot of N" detection -- these should generally be excluded from comps, not just flagged

Field[T]:
  value: Optional[T]
  confidence: "high" | "medium" | "low"
  source: "title" | "item_page" | "inferred"

PriceObservation (extends current price_history entry):
  price: float
  date: str
  id: str                          # listing id -- already added in this pass
  # future, once fetch_full_title-style enrichment is extended:
  shipping_price: Optional[float]
  listing_type: Optional[str]

CompBucketKey (replaces the current 3-tuple, tried in order):
  Level 1 exact:      (player, year, set_name, parallel, card_number, grader, grade)
  Level 2 near-exact:  (player, year, set_name, parallel, card_type)      # raw vs graded, grader-agnostic
  Level 3 family:      (player, year, set_name)
  Level 4 fallback:    (player, card_type, price_tier)                    # today's only level
```

## 8. Proposed Deal Evaluation Flow

```
New Listing (from alert email)
  ↓
Normalize (title cleanup, price parse)
  ↓
Player Match  (existing matcher.match_player)
  ↓
Card Identity Extraction  (NEW -- year/set/parallel/serial/auto/etc, each with confidence)
  ↓
Listing Type Detection  (NEW, best-effort -- auction / fixed price / best offer)
  ↓
Duplicate/Repeat-Sighting Detection  (DONE -- price_history dedupes by listing id)
  ↓
Comp Candidate Retrieval  (NEW -- try Level 1 → 2 → 3 → 4, first level with enough samples wins)
  ↓
Comp Confidence Assignment  (NEW -- HIGH/MEDIUM/LOW/INSUFFICIENT_DATA, from which level matched + sample size/age)
  ↓
Market Estimate  (existing median calc, now against the matched level's bucket)
  ↓
Total Cost Calculation  (NEW -- price + shipping when known, gate/rank on this instead of price alone)
  ↓
Deal Gate  (existing: pct_under_market >= threshold AND dollar_savings >= min_savings_dollars, now against total cost)
  ↓
Desirability Signals  (NEW -- surfaced separately, not blended: rookie/numbered/auto/parallel/etc)
  ↓
"Why Cheap?" / Risk Detection  (NEW -- grader mismatch, lower grade than comps, reprint keyword, thin sample, auction-not-final)
  ↓
Ranking  (existing: $ saved descending -- unchanged, still the right primary sort)
  ↓
Sectioned Report  (NEW structure: Top Opportunities / Long-Term Targets / Target Card Hits / Watchlist / Auctions Ending Soon / Manual Search Links)
```

## 9. Prioritized Backlog

**P0 — accuracy bugs, fix before anything else**
- [x] Duplicate listing observations double-counted in comp medians — **fixed** (`price_history.py`, listing-id-aware `as_buckets`).
- [x] Template-change canary: log a clear warning when alert emails were found but zero listings were extracted from them (distinguishes "eBay sent nothing new" from "the parser silently broke") — **fixed** (`ebay_email_alerts.fetch_alert_listings`).
- [x] Lot listings ("lot of 5", "10 card lot") excluded entirely from matching/comps/flagging — **fixed** (`card_identity.py` + both `main.py` fetch paths). Was a real comp-contamination and false-positive risk.
- [ ] Add regression tests for known-tricky titles still uncovered: reprints, facsimile autographs, same-last-name different-player collisions.

**P1 — major deal-quality improvements**
- [x] Card Identity Engine (year/manufacturer/set/parallel/card#/serial/auto/memorabilia extraction with confidence + source) — **shipped** (`src/card_identity.py`), wired into both fetch paths, surfaced in the report as a "Card: ..." line and `[AUTO]`/`[MEM]` tags.
- [x] Hierarchical comp matching + `comp_confidence`/`comp_level_matched` fields, replacing the single price-tier bucket as the *only* level — **shipped** (`comps.build_hierarchical_comp_table` / `comps.lookup_hierarchical_comp`, `main.flag_deals_hierarchical`, wired into the eBay-alerts path). Tries exact → near_exact → family → price_tier in order; the report now shows e.g. "HIGH confidence -- exact card match" next to the comp median. `price_history` now stores year/set/parallel/card_number/grader/grade per observation so the corpus itself is identity-aware, not just the current listing being evaluated.
- [x] Total acquisition cost (shipping) — **shipped**. `ebay_email_alerts` extracts a nearby "+$X shipping"/"Free shipping" pattern per listing (unvalidated against real data yet, unlike price -- see `scripts/test_ebay_alerts.py --raw`'s new hit-rate line); `Listing.total_cost` is price+shipping when known, else price (never assumes $0); `flag_deals`/`flag_deals_hierarchical` gate and rank on total_cost; the report shows "shipping unknown -- actual cost may be higher" when it wasn't found, or the full price+shipping=total breakdown when it was.
- [ ] Target Card Watchlist (specific year/set/card#/grade targets, coexisting with the player-level watchlist, `[TARGET CARD]` tag).
- [ ] Comp sample recency + count shown plainly in the report (not just `n=X`).

**P2 — decision-support improvements**
- [ ] Desirability signal surfacing (numbered, autograph, premium parallel, reprint, unlicensed, etc.) as visible tags.
- [ ] "Why is this cheap?" reasoning field for flagged deals.
- [ ] Auction vs. fixed-price detection (research alert-email HTML first) + a distinct "watch, don't count as a confirmed deal" auction path.
- [ ] Report redesign into the sectioned format (Top Opportunities / Long-Term Targets / Target Card Hits / Watchlist / Auctions Ending Soon).
- [ ] Data-quality/observability pass: comp-coverage logging, thin-sample warnings.

**P3 — nice-to-have**
- [ ] Seller/listing risk signals (only if the data is actually present in alert emails or item pages — unverified).
- [ ] Mispriced/misdescribed-listing detection (explicitly speculative per the brief — build only with honest low confidence, never a false "found a steal" claim).
- [ ] Optional AI layer for messy title parsing, gated behind deterministic identity extraction being solid first; structured-output-only, cached, with a deterministic fallback.
- [ ] SQLite migration — revisit only if/when JSON flat files genuinely become a bottleneck; not needed at current volume.

## 10. Tests Required (alongside each major change, not after)

- **Identity extraction:** fixtures for base rookie, rookie parallel, numbered rookie, rookie auto, veteran base, vintage graded, reprint, facsimile auto, lot-of-N, unknown/unparseable title (must degrade to `confidence: low` / `value: None`, never guess).
- **Hierarchical comps:** exact match available → used; exact unavailable but family-level available → falls back correctly and confidence drops accordingly; no level has enough samples → no comp at all (never guesses).
- **Duplicate/listing-id dedupe:** covered in this pass (`test_as_buckets_collapses_repeat_sightings_of_the_same_listing`, `test_as_buckets_keeps_latest_price_when_same_listing_price_changes`, `test_as_buckets_keeps_legacy_observations_without_an_id_ungrouped`).
- **Total cost:** shipping present → total used for gating/ranking; shipping absent → shown as unknown, not assumed $0; shipping absent must not silently exclude a listing from the report.
- **Auction detection:** auction with low current bid → never reported as a confirmed deal; fixed-price/Buy-It-Now unaffected; unknown listing type → falls back to current (conservative) behavior, not a new false positive.
- **Report sectioning:** each new section populates only from listings that actually qualify for it; a listing with no target-card match never appears under Target Card Hits, etc.
- **Observability canary:** alert emails found + zero listings extracted → warning logged; zero alert emails found (legitimately nothing new) → no warning, this is the normal case.

## 11. What Shipped

**Pass 1:** listing-id-aware price history, closing the duplicate-
observation comp-inflation bug that was live in production (caused by the
default 2-day alert lookback window overlapping consecutive daily runs).

**Pass 2:** the alert-parsing template-change canary; the Card Identity
Engine (`src/card_identity.py`) wired into both fetch paths and surfaced
in the report; and lot-listing exclusion (a lot's price was being treated
as a single card's price, a real comp-contamination/false-positive risk
that the identity engine made fixable).

**Pass 3:** hierarchical comp matching (`comps.build_hierarchical_comp_table`
/ `lookup_hierarchical_comp`, `main.flag_deals_hierarchical`) — comps on
the alerts path now try exact → near_exact → family → price_tier, in
that order, and the report shows which level matched and how confident it
is. `price_history` observations now carry identity fields so the
self-built corpus can actually be matched at those levels as it grows.
Early on, most matches will still resolve at `family` or `price_tier`
(there simply isn't years of per-parallel history yet) — that's expected
and the whole reason the hierarchy degrades gracefully instead of
requiring an exact match to flag anything at all.

**Pass 4:** delivery reliability -- `.github/workflows/daily-scan.yml` runs
the scan on a schedule in GitHub's cloud instead of depending on a laptop
being on/awake at 8am, committing dedupe/comp-history state back to the
repo each run since the runner itself is thrown away; a compact "TOP
PICKS" summary in the report for quick skimming once there are enough
deals to benefit from one; and total acquisition cost (shipping) --
extracted from alert-email text when present, `Listing.total_cost` used
for gating/ranking, "shipping unknown" shown honestly rather than assumed
$0.

**Pass 5:** action labels (`LOOK NOW` / `WATCH` / `LOW CONFIDENCE`),
derived deterministically from `comp_confidence` (never a separate
judgment call, never a buy recommendation) and shown per-entry and in the
TOP PICKS summary, so a long list surfaces which deals are worth opening
first without changing the underlying $-saved ranking. This is Phase 19
from the original brief ("What should I do?"), minus the absolute
"BUY THIS" language it explicitly said to avoid.

**Pass 6 (CardPro 2.0 — see [CARDPRO_2_AUDIT.md](CARDPRO_2_AUDIT.md)):** the
P0 "trust" pass. Re-audited everything above against live production data,
then rebuilt the parts that measurement showed were not working:

- The price-tier comp level is no longer allowed to declare a deal (it is
  circular by construction), and the grade-blind `family` level was deleted
  outright rather than demoted.
- Every comp level now segments by the full market key, so PSA 9 / PSA 10 /
  raw / qualified slabs are separate markets everywhere.
- A listing is excluded from the comp set used to judge it.
- Outlier trimming, time-decay weighting, staleness and dispersion gates, and
  a confidence ladder in which asking-price comps can never reach "high".
- Team and award names no longer extract as card parallels; reprints,
  replicas, customs, digital cards, sealed product, break slots and
  pick-your-card listings are detected and blocked with a stated reason.
- Truncated titles are repaired *before* valuation, not after.
- Auctions are detected, never treated as a price, never recorded as comps,
  and get a max-rational-bid calculation instead.
- Nothing leaves the pipeline unexplained: one canonical reason per listing,
  counted in the report's data-quality footer.
- Added: deal economics, acquisition targets, saved-search generation, and a
  corpus replay tool for before/after evidence.

**Still open, in priority order per [CARDPRO_2_AUDIT.md](CARDPRO_2_AUDIT.md) §7:**
real sold comps via user import (P1.2 — the single highest-ceiling item),
robust comp statistics tuning, eBay Browse API re-application, search
coverage into the graded market, SQLite migration, listing-lifecycle
history, raw-to-graded scenario analysis, feedback loop, backtesting.
