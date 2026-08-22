# CardPro 2.0 — Full Product, Architecture, and Deal-Finding Audit

_Audit date: August 22, 2026. Audited commit: `11fb3cd`. Branch: `claude/cardpro-2-audit-wc7dzd`._

This is the Part 44 deliverable: the honest read of CardPro before any
major code was written in this pass. Every claim below is either read
directly out of the source or **measured against the real production
corpus** (`data/ebay_alert_price_history.json`, 563 observations /
280 distinct listings / 2 days of live running, and
`data/seen_listings.json`, 15 real flagged "deals"). Where a number is
measured, the measurement is shown. Where something is unverifiable from
this sandbox, it says so.

---

## 0. The one-paragraph version

CardPro is a well-engineered, honest, well-tested pipeline **wrapped
around a valuation engine that does not work.** The engineering is real:
168 passing tests, atomic state writes, a scheduled cloud run, a
template-change canary, failure emails, explicit "unknown never means
guess" discipline, and a report with no black-box score. The valuation is
not real: there are **zero sold comps**, the comp corpus is asking prices
of *newly listed* items scraped from eBay's own alert emails, and **76% of
comps resolve to a `price_tier` bucket that is defined by price itself** —
a circular comparison that mathematically guarantees the cheap end of
every bucket looks like a 30–50% discount. All 15 deals CardPro has ever
flagged in production came from that circularity or from equally broken
broader levels. The product currently answers "which of this player's
cards is cheapest right now" and reports the answer as "underpriced."

---

## 1. Executive Assessment

### 1.1 Scores

| Dimension | Score | One-line justification (measured where possible) |
|---|---:|---|
| Listing discovery | **25** | One source. eBay's saved-search alert digest = *newly listed only*, ≤ once/day, seller-titled. No recall measurement exists. Graded cards are **0.7% of the corpus** (4 of 563 observations) — the liquid, high-value half of the market is effectively invisible. |
| Card identification | **28** | Structured `CardIdentity` with per-field confidence is the right shape, but fill rates are near-useless: parallel **3%** (18/563), set **12%** (66/563), card # **29%**, grade **0.7%**. And what it *does* extract is partly wrong — see Failure Mode #2 (team names parsed as parallels). |
| Comp quality | **10** | Zero sold prices. Asking prices only, biased to new listings. `min_comps_required = 3`. No outlier rejection, no recency weighting, no dispersion check, no sale-frequency data. A listing is included in **its own comp median**. |
| Valuation accuracy | **8** | Follows from the above, and is entirely unvalidated — there is no ground truth, no backtest, no measured error anywhere in the repo. |
| Deal detection | **12** | The dominant comp level is circular (§2.2). Measured: replaying the real corpus through the current engine reproduces the same 15 "deals", of which **15/15 are artifacts** of comparing a card to a price bucket it defines, or to a family median that mixes parallels, grades, and raw/graded together. |
| Deal ranking | **35** | Ranking by dollar savings is the *correct* primary key and was a good call. But it ranks on an unreliable value estimate, and there are no economics (fees, shipping out, tax, grading) behind "savings." |
| Search coverage | **20** | One implicit query per player (whatever saved search you set up manually on eBay). No set/parallel/grade/misspelling query expansion, no coverage or overlap measurement, no way to know what was missed. |
| Auction handling | **5** | None. A current bid is indistinguishable from an asking price in the pipeline. This directly violates non-negotiable principle #5. |
| Seller-risk analysis | **0** | Not built. Not captured. Not measured as fetchable. |
| Reporting | **45** | Genuinely honest and traceable — no black box, shipping never assumed $0, comp level shown. But it is a flat ranked list with no thesis, no comp range, no recency, no risks, no sections, no "why is this cheap." |
| Reliability | **62** | Cloud schedule, atomic temp-file writes, corrupt-JSON tolerance, failure email, canary, rotating logs. Deductions: state is persisted by **committing to git from CI** (fragile, race-prone), no retry/backoff anywhere, no IMAP timeout, no concurrent-run guard. |
| Observability | **28** | Log lines and one canary. No data-quality metrics, no rejection reasons, no KPIs, no way to answer "did the pipeline produce garbage today?" |
| Extensibility | **42** | Clean small modules, config-driven watchlist/thresholds, `Field(value, confidence, source)` is a good primitive. But: no marketplace interface, no persisted `Listing`/`Valuation`/`Opportunity` entities, no stable card ID, flat JSON, two divergent flagging code paths. |

### **Overall: 27 / 100**

That number is deliberately harsh and deliberately *not* an average of the
engineering quality. As a piece of software CardPro would score in the
60s. As a system that answers "what can I buy right now for meaningfully
less than it's worth," it scores in the 20s, because the answer it
produces today is not trustworthy. The brief's standard is an elite dealer
scanning the market; CardPro is currently a competent robot that has never
seen a sold price.

### 1.2 The biggest bottleneck

**There is no ground truth for market value, and the fallback invented to
cover for that is mathematically circular.**

Everything else — identity extraction, ranking, reporting, search
coverage, auctions — is downstream of this. Improving any of them while
the valuation is circular just produces more confident nonsense. Concretely:

1. **No sold data.** Every comp in production is an *asking price* pulled
   from an eBay "here are new listings" email. Asking prices are the
   seller's opinion; sold prices are the market's. Worse, alert emails are
   biased to *brand-new* listings, which skew high (sellers start high and
   reduce) and skew toward whatever is being freshly dumped.
2. **The fallback is circular.** With almost no identity data (parallel
   3%, set 12%), the hierarchy collapses to its last rung:
   `(player, card_type, price_tier)` where `price_tier` is a bracket of
   *price*. The median of everything priced $25–$100 is ~$44. Anything
   priced $25–$31 is therefore *automatically* ≥30% "under market." That is
   not a valuation; it is a restatement of the price.
3. **Self-inclusion.** `main.run()` records today's listings into
   `price_history`, then builds the comp table from that same history, then
   evaluates today's listings against it. At `n=3`, a listing contributes
   ~33% of the median it is being judged against.

Fix valuation first. Everything else compounds off it.

---

## 2. Current Architecture Map

### 2.1 The live path (eBay saved-search email alerts)

```
eBay saved search (configured by hand on ebay.com, one per player)
        │  eBay emails a digest of NEW matching listings, ≤1×/day
        ▼
Gmail  ──IMAP (read-only, "[Gmail]/All Mail", SINCE lookback_days=2)──►
        ebay_email_alerts.fetch_alert_messages()
        ▼
get_html_body() → extract_listings_from_html()      [BeautifulSoup]
        │   anchor tags matching /itm/<id>; price + shipping found by
        │   "nearest sibling / narrowest ancestor without another item link"
        ▼  [{title, url, price, shipping_price}]
matcher.match_player()        all name parts as whole words, first match wins
matcher.detect_grading()      /\b(PSA|BGS|SGC|CSG)\s*(\d{1,2}(\.5)?)\b/
matcher.detect_rookie_card()  /\bRC\b|\bROOKIE\b/
card_identity.extract_card_identity()   year, mfr, set, parallel, card#,
                                        serial, auto, mem, lot  (regex/keyword)
        │  lots excluded entirely here
        ▼
Listing dataclass
        ▼
price_history.record(...)     ← EVERY matched listing appended to the corpus
price_history.prune_old(180d)
price_history.deduped_observations()   ← one row per listing id, latest date
        ▼
comps.build_hierarchical_comp_table(observations, min_comps_required=3)
        │   builds 4 independent bucket sets:
        │     exact      (player, year, set, parallel, card#, grader, grade)
        │     near_exact (player, year, set, parallel, card_type)
        │     family     (player, year, set)
        │     price_tier (player, card_type, price_tier(price))
        ▼
main.flag_deals_hierarchical()
        │   lookup exact → near_exact → family → price_tier, first hit wins
        │   savings = comp_median − total_cost
        │   FLAG iff pct_under ≥ 30 AND savings ≥ $10
        ▼
enrich_truncated_grades()   ← re-fetch real title for flagged truncated ones
                              (AFTER comping — see Failure Mode #5)
        ▼
dedupe.is_new_or_price_drop()  vs data/seen_listings.json
        ▼
report.build_report()  → rank by $ saved → plain text
        ▼
emailer.send_email()   Gmail SMTP (App Password)
        ▼
GitHub Actions commits data/*.json back to the repo (state persistence)
```

### 2.2 What the pipeline actually did, measured

Replaying the live corpus through the current engine:

```
deduped observations ......... 280
comp buckets built:  exact 0 │ near_exact 1 │ family 1 │ price_tier 37
comp level used:     none 58 (21%) │ price_tier 213 (76%) │ near_exact 8 (3%) │ family 1 (0.4%)
listings flagged as deals .... 15
```

**Zero exact comps have ever existed in production.** Three-quarters of
all valuations are the circular price-tier rung. One-fifth of listings get
no valuation at all and are silently dropped — not reported, not counted,
not explained.

Sample of what got flagged (real production output):

| Flagged listing | Comp used | Level | Reality |
|---|---|---|---|
| Ernie Banks 1971, $25.00 | median $44.50, n=11 | price_tier | Bucket = "all raw Ernie Banks priced $25–100", spanning 1958–1971 and every set. The comp is "other Banks cards cost more." |
| Ernie Banks 1968, $25.00 | median $44.50, n=11 | price_tier | Same bucket, same median, different card. Two different cards given the identical "market value." |
| Kyle Teel 2026 Topps Chrome, $1.25 | median $26.99, n=9 | family | Family key ignores `card_type` **and** parallel — a $1.25 base card compared against a bucket containing refractors and numbered parallels. Reported as **95% under market**. |
| Kyle Teel 2026 Topps Chrome, $2.69 | median $27.99, n=8 | near_exact | `parallel=None` on both sides, so "unknown parallel" was treated as a *matching* attribute. Reported as 90% under market. |
| Munetaka Murakami, $199.99 | median $403.80, n=3 | price_tier | n=3 asking prices, no outlier control, `100_plus` tier is unbounded — one $2,499 listing exists in the corpus. |

That last row is the clearest illustration: the top-of-report "biggest
deal in the system, $203 saved" is a $200 card compared against three
asking prices in an unbounded bucket.

### 2.3 The dormant path (eBay API)

`ebay_client.py` implements Browse (active) + Marketplace Insights (sold).
It is correct code and correctly tested, but dead: the developer account
was declined. `main.flag_deals()` (the non-hierarchical variant) exists
only to serve it. Two parallel flagging implementations that must be kept
in sync is a maintenance liability — see Opportunity #14.

---

## 3. Top 10 Failure Modes

Ranked by *how likely they are to make you spend money wrongly*.

**#1 — Circular price-tier valuation (LIVE, affects 76% of valuations).**
`price_tier` buckets are defined by price, so being at the bottom of your
own bucket *is* the deal signal. There is no card in existence that this
can't flag if it's priced below its neighbours. Every "% under market" it
produces is meaningless. *Money impact: this is the primary source of
false buys.*

**#2 — Team names extracted as card parallels (LIVE).** Verified by
running the extractor:

```
"2024 Topps Chrome Kyle Teel Chicago White Sox RC #150"  → parallel = "White"
"1993 Upper Deck Blue Jays Team Card Joe Carter"          → parallel = "Blue"
"2023 Bowman Red Sox Prospect Auto"                       → parallel = "Red"
"Michael Jordan Gold Glove Award ... "                    → parallel = "Gold"
```

`parallel` is a comp bucket key. This does two kinds of damage: it makes
CardPro *believe* it knows a parallel when it doesn't (violating the "never
guess" principle at the exact place it matters most), and it groups a
White Sox base card with a genuine White Sparkle parallel. With White Sox,
Red Sox, Red Wings, Blue Jays, Green Bay, Golden State, Blackhawks and
"Gold Glove" all in play, this is not an edge case for this watchlist.

**#3 — Different grades pooled into one market (LIVE).** The `near_exact`
key is `(player, year, set, parallel, card_type)` — `card_type` is just
`"graded"`. **A PSA 10 and a PSA 8 of the same card share a comp bucket.**
The brief's non-negotiable #2 forbids exactly this. A PSA 8 priced below
the PSA-10-inflated median reads as a steal.

**#4 — A listing is part of its own comp (LIVE).** Today's listings are
written into `price_history` before the comp table is built from that
history. At `min_comps_required = 3`, a listing supplies up to a third of
the median used to judge it. A single lowball listing partially drags its
own "market value" down toward itself, *reducing* the measured discount —
and symmetrically, a mispriced-high listing inflates its own comp.

**#5 — Truncated grades are comped before they're recovered (LIVE).**
eBay truncates long titles. `"...PSA 1…"` parses as **PSA 1**. That value
is used for the comp lookup and the deal gate. Only *afterwards* does
`enrich_truncated_grades()` fetch the real title. A PSA 10 gets valued as a
PSA 1 (or vice versa) and, either way, the flag decision was already made
on the wrong number.

**#6 — Reprints, replicas, customs and digital cards are invisible.**
`"1986 Fleer Michael Jordan #57 REPRINT"` extracts as a 1986 Fleer Jordan
#57 with no negative signal at all. There is no `REPRINT` / `RP` /
`REPLICA` / `CUSTOM` / `ART CARD` / `DIGITAL` / `FACSIMILE` detection
anywhere in the codebase. A $20 reprint next to $6,000 comps is the single
most expensive-looking false positive this system can produce.

**#7 — Auctions are treated as prices (LIVE, structural).** Nothing
distinguishes a $40 opening bid on a 7-day auction from a $40 Buy It Now.
Both flow through the same gate and can be reported as a confirmed
discount. Principle #5 violated by omission.

**#8 — Thin, stale, unbounded, outlier-contaminated comps.** `n ≥ 3`
asking prices is the entire quality bar. No MAD/IQR trim, no dispersion
check, no recency weighting, no minimum age spread, no cap on the
`100_plus` tier (which contains everything from $100 to $2,499). Three
observations from five months ago read identically to forty from this
week, in both the math and the report.

**#9 — 21% of listings are silently dropped.** When no comp level has
enough samples, `lookup_hierarchical_comp` returns `None` and the listing
vanishes — not flagged, not reported, not logged, not counted. The rarest
and most interesting cards (the ones with no comparable history) are
precisely the ones most likely to disappear this way. You cannot see what
you didn't see.

**#10 — Discovery is structurally near-blind to the real market.**
Measured: **99.3% of the corpus is raw**, **67% of observations are under
$25**, median observation **$10.64**. CardPro is currently a $1-common
detector. This is a direct consequence of one-query-per-player saved
searches: eBay's alert digest returns whatever is newest, and what is
newest is overwhelmingly cheap raw filler.

**Honourable mentions (real, lower money-impact):** `#1 Draft Pick` parses
as card number `1`; print-run-only notation (`/99` with no numerator) is
missed entirely; `2023-24` season becomes year `2023`; dedupe records
`price` not `total_cost`, so a shipping-only increase looks like no change;
`fetch_ebay_alert_active` takes a `today_str` argument it never uses;
GitHub Actions persists state by committing to the default branch on every
run, which will conflict the moment two runs overlap or you push while a
scan is running.

---

## 4. Top 20 Opportunities (ranked by impact × feasibility × confidence)

| # | Opportunity | Impact | Feasibility | Confidence | Priority |
|---|---|---|---|---|---|
| 1 | **Stop flagging on circular comps.** Demote `price_tier` (and unqualified `family`) to *context only* — never eligible to declare a deal. | Very high | Trivial | Certain | **P0** |
| 2 | **Grade-aware comp keys.** Never pool different grader/grade. Raw, PSA 9, PSA 10 are three markets. | Very high | Trivial | Certain | **P0** |
| 3 | **Exclude a listing from its own comp** (by listing id). | High | Trivial | Certain | **P0** |
| 4 | **Negative-signal detection** — reprint / replica / custom / digital / facsimile / sealed box / art card / lot → never a deal, always visible. | Very high | Easy | Certain | **P0** |
| 5 | **Team-name and false-parallel guard** — mask team/league/award phrases before parallel matching; require a known parallel vocabulary. | High | Easy | Certain | **P0** |
| 6 | **Recover truncated titles before comping**, not after. | High | Trivial | Certain | **P0** |
| 7 | **Auction detection + separate auction engine** — current bid never treated as a price; compute max rational bid. | High | Medium (needs real email samples) | High | **P0/P1** |
| 8 | **Rejection reasons for every listing** — nothing is ever silently dropped; every skip has a machine-readable reason, counted in the report. | High | Easy | Certain | **P0** |
| 9 | **Robust comp statistics** — MAD outlier trim, dispersion gate, time-decay weighting, expose n / range / median / newest / oldest. | High | Easy | High | **P1** |
| 10 | **Get real sold comps.** In descending order of realism: 130point manual spot-check workflow → Card Ladder/Market Movers subscription with manual CSV import → paid price API. See §6. | **Highest ceiling** | Medium–hard | High | **P1** |
| 11 | **Deal economics** — fees (~13.25% eBay + $0.30), outbound shipping, supplies, tax; expected net proceeds and ROI, with assumptions shown. | High | Easy | High | **P1** |
| 12 | **Search-term generator + coverage measurement** — per-player query families (rookie / auto / numbered / PSA 10 / set names / misspellings), and measure which ones actually produce opportunities. | High | Medium | High | **P1/P2** |
| 13 | **Decision-first sectioned report** with a 10-second thesis per card (comp range, n, recency, trend, risks, why-it-may-be-cheap). | High | Easy | Certain | **P1** |
| 14 | **Collapse the two flagging code paths** into one valuation service behind a `MarketplaceSource` interface. | Medium | Easy | High | **P1** |
| 15 | **SQLite migration** with a real entity model (Listing / Observation / Valuation / Opportunity / Feedback) and listing history over time. | Medium now, **high later** | Medium | High | **P2** |
| 16 | **Target-card watchlist + acquisition thresholds** (buy zone / great buy / immediate alert), config-only. | High *personally* | Easy | Certain | **P1** |
| 17 | **Raw→graded scenario analysis** with breakeven PSA-10 probability (never a grade prediction). | Medium–high | Medium | Medium | **P2** |
| 18 | **Price-drop / listing-lifecycle tracking** — original price, reductions, days listed, disappeared/relisted. Becomes proprietary data. | Medium now, high later | Easy | High | **P2** |
| 19 | **Feedback loop** (bought / passed / bad comp / wrong card) feeding personal relevance and search strategy only — never market value. | Medium | Easy | High | **P2** |
| 20 | **Backtesting harness** — replay stored listings against comps as they existed then; no look-ahead. | Medium | Medium | Medium | **P3** |

Just below the line, deliberately: LLM title parsing (only *after* the
deterministic extractor is trustworthy, and only as a confidence-gated
fallback), image/OCR intelligence, seller-risk scoring (needs data that
may not exist in the emails), collection portfolio tracking, non-card
collectibles.

---

## 5. Ideal End-State Architecture

```
                       ┌──────────────────────── SOURCES ────────────────────────┐
                       │ MarketplaceSource (interface)                            │
                       │   EbayAlertSource   (live: IMAP saved-search digests)    │
                       │   EbayApiSource     (dormant: Browse / Insights)         │
                       │   UserImportSource  (CSV: Card Ladder / 130point / mine) │
                       │   …COMC / MySlabs / Goldin / Heritage  (future)          │
                       └───────────────────────────┬─────────────────────────────┘
                                                   ▼
   ┌───────────────────────────── INGEST & IDENTITY ──────────────────────────────┐
   │ RawListing → normalize → CardIdentity extraction                              │
   │   deterministic dictionaries (sets, parallels, teams, graders, negatives)     │
   │   → confidence-gated LLM fallback for ambiguous titles ONLY                   │
   │   → still-uncertain ⇒ UNRESOLVED (never a guess)                              │
   │ Entity resolution → stable card_key + variant_key + market_key(grade)         │
   │ Negative signals (reprint/custom/digital/lot/sealed) → hard-block             │
   └───────────────────────────────────┬──────────────────────────────────────────┘
                                       ▼
   ┌────────────────────────────── VALUATION ─────────────────────────────────────┐
   │ CompEngine                                                                    │
   │   Tier A  exact card + exact grade, SOLD          → HIGH   (flag eligible)    │
   │   Tier B  exact card + exact grade, ASKING        → MEDIUM (flag eligible)    │
   │   Tier C  exact card, adjacent grade w/ modelled adjustment → MEDIUM          │
   │   Tier D  near comparable (same card, unknown minor attrs)  → LOW  (watch)    │
   │   Tier E  card family                              → CONTEXT ONLY, never flags│
   │   stats: trimmed weighted median (MAD), n, range, newest, oldest,             │
   │          7/30/90d medians, slope, sale frequency, volatility, dispersion gate │
   │ MarketState: rising / falling / stable / thin / volatile                      │
   └───────────────────────────────────┬──────────────────────────────────────────┘
                                       ▼
   ┌──────────────────────────── OPPORTUNITY ─────────────────────────────────────┐
   │ Economics: acquisition (price+ship+tax) → value → gross Δ →                   │
   │            resale (fees, ship out, supplies) → net profit → ROI               │
   │ Separate, never blended:                                                      │
   │   • is_cheap        • is_underpriced   • is_flippable                         │
   │   • collectible_fit (my attribute preferences)                                │
   │   • target_hit      (explicit acquisition target crossed)                     │
   │ Risk flags: seller, thin comps, stale comps, auction, condition, identity     │
   │ Auction engine: max rational bid, time-to-close, watch priority               │
   │ Offer engine: aggressive / fair / max offer for Best Offer listings           │
   │ RejectionReason on EVERYTHING that doesn't make it                            │
   └───────────────────────────────────┬──────────────────────────────────────────┘
                                       ▼
   ┌──────────────── PERSISTENCE (SQLite) ────────────────┐   ┌──── DELIVERY ─────┐
   │ listing, listing_price_event, seller, card, identity │   │ Immediate alert   │
   │ comp_sale, valuation, opportunity, rejection,        │──►│ Morning digest    │
   │ watch_target, search, alert, feedback                │   │ (sectioned)       │
   │ = tomorrow's proprietary dataset                     │   │ Local dashboard¹  │
   └──────────────────────────────────────────────────────┘   └───────────────────┘
                                       ▲
                       ┌───────────────┴────────────────┐
                       │ MEASUREMENT: precision, recall, │
                       │ exact-comp coverage, ID rate,   │
                       │ time-to-detection, FP-by-reason │
                       │ + backtest harness (no lookahead)│
                       └─────────────────────────────────┘

¹ only when email + links genuinely stop being enough — not before.
```

**Deliberate non-goals in this architecture:** no cloud infrastructure, no
message queue, no microservices, no ML training pipeline. A personal tool
running once or twice a day against tens-to-hundreds of listings should
stay a Python package plus a SQLite file.

---

## 6. Data Source Matrix

Researched August 2026. Where a price could not be confirmed from this
sandbox (some vendor domains are blocked by the network egress proxy), the
cell says **verify at signup** rather than inventing a number.

| Source | Data available | Sold prices? | Automation method | Official API? | Cost | ToS posture | Reliability | Recommendation |
|---|---|---|---|---|---|---|---|---|
| **eBay saved-search alert emails** *(live today)* | Title, URL, price, sometimes shipping. New listings only. | ❌ | Gmail IMAP (already built) | n/a — it's your own mail | Free | Clean: reading your own inbox | High, but ≤1×/day per search and template-dependent | **KEEP.** It is the discovery backbone. It is *not* a valuation source. |
| **eBay Browse API** | Active listings: price, condition, buying options, seller, images, item specifics | ❌ active only | REST | ✅ free dev keyset | Free | Clean | High | **RE-APPLY / RETRY.** Even without sold data this is a massive upgrade over email: real auction/BIN flags, bid counts, end times, seller feedback, shipping, item specifics (set/parallel/grade as *structured fields*, not title guesses). Biggest single feasible win. |
| **eBay Marketplace Insights API** | Last **90 days** of sold prices | ✅ | REST | ✅ but *Limited Release* | Free if granted | eBay docs state it is restricted and **not open to new users**; community reports access is limited to major partners | n/a | **SKIP** as a plan. Apply once, expect no. Do not architect around it. |
| **eBay Terapeak (Product Research)** | ~3 years of eBay sold data, in the Seller Hub UI | ✅ | **Manual only** (UI + export) | ❌ | Bundled with an eBay Store subscription (~$22–28/mo, verify at signup) | Manual use fine; automating the logged-in UI is not | High data quality | **CONSIDER** if you'll actually do periodic manual exports into `UserImportSource`. Otherwise skip. |
| **130point.com** | eBay + Goldin sold results **including accepted Best Offer prices** — the thing eBay's own UI hides | ✅ | **Manual only** | ❌ | Free | No API; don't scrape | High | **USE MANUALLY.** Best free sold-comp spot-check in the hobby. Build the *workflow*: report links straight to a pre-filled 130point search per card. |
| **Card Ladder** | Very large sold-comp database, per-card price history, indices, population | ✅ | Manual; CSV **import** documented, no documented public API | ❌ (no public API found) | **$20/mo or $200/yr** (confirmed) | Personal-use subscription; no scraping | High | **CONSIDER → likely BUY** if you want real valuation. Realistic integration: manual export/entry into `UserImportSource` for your ~20 watchlist players' key cards, not full automation. |
| **Market Movers (Sports Card Investor)** | Sold comps, trends, pop, "movers" screens | ✅ | Manual; higher tiers advertise API | Partial | Tiered ~$10–$50/mo (verify) | Subscription | High | **CONSIDER** — evaluate only against Card Ladder; don't buy both. |
| **SportsCardsPro / PriceCharting** | Price-guide values **by grade** (ungraded / PSA 9 / PSA 10 …), card catalogue | ✅ derived | REST + CSV | ✅ documented, token-auth | Paid subscription; API is a premium tier (verify — vendor domain blocked from this sandbox) | Clean, documented | High | **STRONGEST AUTOMATION CANDIDATE.** It is the only source in this table with a documented, permitted, programmatic price-by-grade feed at hobbyist pricing. If exactly one paid dependency is allowed, this is the one to price out first. |
| **PSA** | Cert verification (cert # → card + grade); Pop Report on the web | ❌ | REST | ✅ but **as of mid-2026 the free/anon tier is ~1 call/day** — practical use needs a paid plan | Paid (contact Collectors) | Clean | High | **SKIP for now.** Slab validation is valuable but not at the top of the list; revisit when graded coverage exists at all (today: 0.7%). Pop report stays a manual link. |
| **BGS / SGC** | Cert lookup pages | ❌ | Manual | ❌ | Free | No API | Medium | **LINK ONLY.** |
| **Goldin / Heritage / Fanatics Collect** | Auction results, high-end sold prices | ✅ | Manual browse | Partner-only | Free to view | Scraping restricted | High quality, low overlap with your $10–$400 range | **MANUAL / LATER.** Matters for vintage Jordan/Payton, not for Teel commons. |
| **COMC** | Fixed-price inventory | ❌ | Manual | ❌ | Free to view | No permission to automate | Medium | **SKIP** (largely cross-listed to eBay anyway). |
| **MySlabs / ALT / PWCC-historical** | Marketplace inventory / historical index data | Partial | Manual | ❌ | — | Restricted or closed | — | **SKIP.** |
| **Craigslist / Facebook Marketplace** | Local listings | ❌ | — | ❌ | — | **Confirmed active anti-automation** | — | **LINK ONLY** (current behaviour is correct). |
| **Your own accumulating data** | Asking prices, price changes, listing lifespans, disappearance (a weak sold proxy) | Proxy only | Already yours | n/a | Free | Yours | Improves with time | **INVEST.** With listing-lifecycle tracking (Opportunity #18), "listed at $X, disappeared after 3 days" becomes a genuine, defensible sold-price *estimator* nobody else has for your exact watchlist. |

### Paid-dependency recommendation

| Option | Monthly | Annual | What you actually gain | Verdict |
|---|---:|---:|---|---|
| SportsCardsPro/PriceCharting API | verify (hobbyist tier) | — | The only *automatable* by-grade market value. Turns valuation from circular to real overnight for catalogued cards. | **PRICE IT OUT FIRST — likely BUY** |
| Card Ladder Pro | $20 | $200 | Best human-facing sold-comp research; manual import for key cards | **CONSIDER** (buy if you'll do the manual loop) |
| eBay Store (Terapeak) | ~$22–28 | ~$264+ | 3 years of eBay sold data, manual export only | **CONSIDER**, third in line |
| Market Movers | ~$10–50 | — | Overlaps Card Ladder | **SKIP unless it beats Card Ladder on your players** |
| PSA paid API | quote | — | Cert validation + specID | **SKIP for now** |
| **Total recommended today** | **$0–$20** | | Start free: 130point workflow + eBay Browse retry + own-data lifecycle tracking | |

Rationale for spending ~nothing right now: with 99.3% of observed
inventory raw and under $25, a $20–$30/mo data bill would exceed the total
value of the deals CardPro is currently capable of finding. **Fix the free
structural defects first, expand coverage into the graded market, then buy
data once the system can actually act on it.** That ordering is the whole
point of principle "don't spend $200/month to find $50/month of bargains."

---

## 7. Recommended Roadmap (P0 → P5)

Format: **Problem → Solution → Impact / Difficulty / Dependencies / Ongoing cost / Risk / Build now?**

### P0 — TRUST (stop it from recommending bad buys)

| Item | Problem → Solution | Impact | Diff. | Deps | Cost | Risk | Now? |
|---|---|---|---|---|---|---|---|
| **0.1 De-circularise valuation** | Price-tier/family comps are definitionally circular → make them *context only*; only identity+grade-matched levels may declare a deal | Very high | Low | — | none | Deal count drops to near zero at first — **that is the correct outcome**, and is the honest baseline | **YES** |
| **0.2 Grade-segmented markets** | PSA 9 / PSA 10 / raw pooled → grade is part of every comp key | Very high | Low | — | none | none | **YES** |
| **0.3 No self-comparison** | Listing included in own median → exclude by listing id | High | Low | — | none | none | **YES** |
| **0.4 Negative signals** | Reprints/customs/digital/sealed invisible → detect and hard-block from deals, always shown | Very high | Low | — | none | Over-blocking a legitimate title containing "reprint" — acceptable, and visible | **YES** |
| **0.5 Identity guards** | Team names → parallels; `#1 Draft Pick` → card #1; `/99` missed → vocabulary + masking + guards | High | Low | — | none | none | **YES** |
| **0.6 Truncation before comping** | Wrong grade used in the comp lookup → reorder | High | Low | — | none | Extra item-page fetches (still only for candidates) | **YES** |
| **0.7 Rejection reasons + data-quality counters** | 21% silently dropped → structured reason on every listing, counted in the report | High | Low | — | none | none | **YES** |
| **0.8 Auction safety** | Current bid treated as price → detect and route to a separate section; unknown type stated plainly | High | Med | Real email samples | none | Detection may not be possible from email HTML — then it must *say so*, not assume BIN | **YES (conservative version)** |

### P1 — DEAL QUALITY

| Item | Problem → Solution | Impact | Diff. | Deps | Cost | Risk | Now? |
|---|---|---|---|---|---|---|---|
| 1.1 Robust comp stats (MAD trim, dispersion gate, time decay, range/recency exposed) | High | Med | P0.1–0.3 | none | Over-engineering; keep each addition justified | Yes |
| 1.2 Real sold comps via `UserImportSource` (130point / Card Ladder CSV) | **Very high** | Med | schema | $0–$20/mo | Manual effort decays over time | Yes |
| 1.3 Deal economics (fees / shipping / tax / net / ROI, assumptions shown) | High | Low | — | none | False precision — mitigate by showing assumptions | Yes |
| 1.4 Target-card watchlist + acquisition thresholds (config only) | High | Low | identity | none | none | Yes |
| 1.5 Decision-first report with 10-second thesis | High | Low | 0.7 | none | none | Yes |
| 1.6 Single valuation service; collapse duplicate flag paths | Med | Low | — | none | Regression — covered by tests | Yes |
| 1.7 eBay Browse API retry (structured item specifics, auction flags, seller data) | Very high **if granted** | Low | eBay approval | free | May be declined again | Apply |

### P2 — COVERAGE

Search-term generator (per-player query families) + coverage measurement ·
graded-market-targeted searches (the missing 99%) · SQLite migration +
entity model · listing lifecycle & price-drop tracking · raw→graded
scenario analysis with breakeven probability · Best Offer suggestion
engine · feedback loop · seller-risk capture *if* the data proves fetchable.

### P3 — SPEED

Intraday runs for high-conviction targets · immediate-alert tier with
conservative thresholds · time-to-detection measurement · eBay
Browse-based polling (only with API access; never by scraping).

### P4 — INTELLIGENCE

Confidence-gated LLM fallback for genuinely ambiguous titles (structured
output only, cached, deterministic-first, never authoritative on facts) ·
market trend/momentum modelling · anomaly and mis-listing discovery ·
backtesting harness · image/OCR slab reading *if* images prove legally and
reliably accessible.

### P5 — EXPANSION

Generalise `MarketplaceSource` / `Valuation` / `Opportunity` to other
collectibles (memorabilia, TCG, comics, coins) — **only once sports cards
demonstrably work**, measured by the KPIs in §8.

---

## 8. How we'll know it's working (KPIs to build *before* claiming improvement)

| KPI | Definition | Today | Target |
|---|---|---:|---|
| Exact-comp coverage | % of analysed listings valued at a grade-matched, identity-matched level | **0%** | >40% |
| Card identification rate | % of listings with year+set+parallel+number resolved | **~3%** (parallel-limited) | >60% |
| Silent-drop rate | % of listings with no valuation *and* no recorded reason | **21%** | 0% |
| Graded-market share of corpus | % of observations that are graded cards | **0.7%** | >25% |
| High-confidence precision | Of `ACT NOW` items, % genuinely below true market on manual review | unmeasured | >90% |
| False positives by reason | Counted per structured rejection/downgrade reason | not tracked | tracked |
| Stale-comp rate | % of comps whose newest observation is >45 days old | not tracked | <20% |
| Unknown-shipping rate | % of flagged deals with unknown shipping | not tracked | <30% |
| Auction misclassification | Auctions treated as fixed price | unknown, currently 100% | 0% |
| Time-to-detection | Listing time → alert time | unmeasured (≤24h by design) | measured |

---

## 9. Immediate Build Plan

**Phase 3 of this pass implements P0 in full**, because P0 is the set of
changes that stop CardPro from telling you to spend money on the wrong
thing, and every one of them is verifiable offline against the real corpus
with no new data source, no new dependency, and no cost.

Concretely, in order:

1. **`comps.py` rebuild** — grade-segmented, self-excluding, outlier-
   trimmed, recency-aware comp levels, with an explicit `flag_eligible`
   property per level. Context-only levels can inform the report but can
   never declare a deal.
2. **`card_identity.py` hardening** — team/award masking, parallel
   vocabulary with compound names, print-run parsing, card-number guards,
   season normalization, and a `negative_signals` set (reprint, replica,
   custom, digital, sealed, facsimile, lot).
3. **`main.py` reordering** — truncation recovery *before* valuation;
   structured `RejectionReason` on every listing that doesn't make the
   report; auction detection routed to its own path.
4. **`report.py` redesign** — decision-first sections with a per-card
   thesis (comp range, n, newest/oldest, confidence, risks, why-it-may-be-
   cheap) and a data-quality footer.
5. **Regression tests** for every failure mode in §3, replayed against the
   real production corpus to show before/after.

Explicitly **not** in this pass: SQLite migration, LLM layer, image
intelligence, paid data, dashboard, portfolio tracking, other collectible
categories. Each is either downstream of trustworthy valuation or is
spending before the system can use it.

The honest expectation to set up front: **after P0, CardPro will report
far fewer deals — quite possibly zero on some days.** That is the point.
Today's 15 "deals" are 15 artifacts. A system that says "I found nothing I
can stand behind" is strictly more valuable than one that says "95% under
market" about a $1.25 base card.
