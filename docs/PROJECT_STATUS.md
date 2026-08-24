# CardPro — Project Status

_Last updated: August 22, 2026 (CardPro 2.0, P0 "trust" pass)_

A personal, automated sports-card deal-finding system. Runs once a day,
scans your eBay saved-search alerts for the watchlist below, values what it
finds against comparable sales, and emails a decision-first report — every
morning, whether or not it finds anything.

**Status: live and running in production, with a deliberately much stricter
definition of "deal" than it had yesterday.**

---

## 0. Read this first: what changed, and why the deal count dropped

[`docs/CARDPRO_2_AUDIT.md`](CARDPRO_2_AUDIT.md) audited the system against
its own production data and found the valuation engine was not working:

- **76% of all valuations came from a comp bucket defined by price itself.**
  The median of everything priced $25–$100 is about $44, so anything priced
  $25–$31 was automatically "30% under market". That is not a valuation, it
  is a restatement of the price.
- **Zero exact comps had ever existed** in production (0 exact buckets,
  1 near-exact, 1 family, 37 price-tier).
- **All 15 "deals" CardPro had ever flagged were artifacts** of that
  circularity, or of broader levels that mixed parallels, grades and
  raw/graded together. One was a $1.25 base card reported as 95% under
  market against a bucket containing refractors.
- Team names were being extracted as card parallels (`White Sox` →
  parallel `White`), reprints were invisible, truncated grades were used for
  valuation before being repaired, and a listing was included in its own
  comp median.

All of that is fixed. **The consequence is that CardPro now reports far
fewer opportunities — on many days, zero.** Replaying the exact same
production corpus through the new engine flags nothing at all, because
nothing in it can honestly be called underpriced.

That is the system working. A report that says "I found nothing I can stand
behind" is worth more than one that says "95% under market" about a common.

---

## 1. What this actually answers

CardPro deliberately keeps five different questions apart, and never blends
them into a single score:

| Question | What it means |
|---|---|
| **Cheap** | The asking price is objectively low. Says nothing about value. |
| **Underpriced** | Materially below what comparable copies of *that exact card in that exact grade* go for. Requires an identity-and-grade-matched comp; a price-bracket estimate can never establish this. |
| **Flippable** | Enough spread to resell at a worthwhile profit after fees, shipping, supplies and a resale haircut. Shown with its assumptions attached. |
| **Collectible opportunity** | Underpriced *and* carrying attributes that make a copy scarce (rookie, auto, patch, serial numbering, non-base parallel, grade). Tagged separately, never folded into the price maths -- see `src/desirability.py`. |
| **Target acquisition** | A specific card you told CardPro to find below a price you set. A target hit is not a claim that it's underpriced — those are different answers and they get different sections. |

---

## 2. Data sources

| Source | Status | How |
|---|---|---|
| **eBay saved-search email alerts** | **Live, primary discovery source** | eBay's own "save this search + email me" feature, read via Gmail IMAP. No eBay API access required. Provides *newly listed* items only, at most once a day per search. |
| **eBay Browse / Marketplace Insights API** | Built, dormant | Full client exists (`ebay_client.py`) for if/when access is granted; the developer account application was declined. Worth re-applying for Browse (structured item specifics, real auction flags, seller data). Marketplace Insights — the only official sold-price feed — is documented as restricted and not open to new users, so it is not something to plan around. |
| **130point** | Manual workflow | Free, and the best public source of eBay + Goldin sold prices *including accepted Best Offers*. No API; used by hand. |
| **Card Ladder / Market Movers / SportsCardsPro** | Evaluated, not bought | See the data-source matrix and cost analysis in [CARDPRO_2_AUDIT.md](CARDPRO_2_AUDIT.md) §6. Recommendation today is to spend $0 until the system can act on better data. |
| **Craigslist** | Link-only, not scraped | Confirmed anti-automation defenses. The report includes a ready-to-click search link per player instead. |
| **Facebook Marketplace, Sportlots, COMC, MySlabs** | Out of scope / ruled out | ToS or no permitted automation path. |

**Guiding rule, unchanged:** this project does not build tooling to defeat a
site's anti-bot or anti-automation defenses, regardless of how low-stakes
the ask.

**The honest limitation:** every comp CardPro currently has is an *asking
price*, not a sale. The engine knows this — asking-basis comps can never
reach "high" confidence, and the report says so on every card.

---

## 3. Watchlist (`config/watchlist.json`)

**Legends** (established/retired — the deal is just about the deal):
Michael Jordan, Walter Payton, Ernie Banks, Ryne Sandberg, Dick Butkus,
Scottie Pippen, Frank Thomas, Gale Sayers.

**Young Core** (bet on for long-term growth, tagged `[YOUNG CORE]`):

| Team | Players |
|---|---|
| Bears | Caleb Williams, Rome Odunze, Luther Burden, Colston Loveland |
| Bulls | Josh Giddey, Matas Buzelis, Caleb Wilson |
| Blackhawks | Connor Bedard |
| White Sox | Munetaka Murakami, Kyle Teel, Colson Montgomery |
| Cubs | Pete Crow-Armstrong |

**Acquisition targets** (`target_cards`) are new and start empty on purpose —
those are your prices to set. See §9.

---

## 4. Pipeline

```
eBay saved-search alert email
  → Gmail IMAP (All Mail), message count recorded for the health footer
  → HTML extraction: title, price, shipping, listing type, bid count, time left
  → player match (all name parts; every match kept, so dual autos are detected)
  → card identity: year, season, manufacturer, set, parallel, card #, serial,
    print run, auto, memorabilia, patch, lot, negative signals
  → grade details: grader, grade, qualifier (OC/MK/...), authenticity-only slabs
  → TRUNCATED TITLES REPAIRED HERE, before anything is valued
  → asking prices recorded to the comp corpus
      (auctions excluded -- a bid is not a price; blocked listings excluded)
  → comp lookup: exact → same_card → same_set → price_tier
      only exact and same_card may declare a deal
      every level segments by market (raw / grader+grade+qualifier)
      the listing itself is excluded from its own comp
      MAD outlier trim, time-decay weighting, staleness + dispersion gates
  → economics: fees, outbound shipping, supplies, tax, net proceeds, ROI
  → auctions: max rational bid instead of a discount claim
  → acquisition targets matched against your price bands
  → deal gate on TOTAL cost, and it is not one gate but two:
      normal cards  ≥30% under market AND ≥$3 saved
      cheap (<$10)  ≥50% under market AND ≥$3 saved AND at least one attribute
                    that makes a copy scarce -- otherwise rejected as common_card
  → dedupe: new listings and genuine price drops only
  → focus: the email is the cheap end you actually bid at -- cards at or under
      $40 all-in, plus anything exceptional above it (50%+ AND $100+ AND a
      flag-eligible comp); auctions already bid past your max rational bid are
      dropped; the rest is capped at 40 listings, 10 per section. Removes only,
      re-values nothing, and every group it removed is counted in the footer
  → decision-first sectioned report, emailed via Gmail SMTP
```

Every listing exits with either a slot in the report or exactly one recorded
reason, counted in the report footer. Nothing is dropped silently.

---

## 5. Comps: what may and may not declare a deal

| Level | Matches on | May flag a deal? |
|---|---|---|
| `exact` | player + year + set + parallel + card # + market | **Yes** |
| `same_card` | player + year + set + parallel + market | **Yes** |
| `same_set` | player + year + set + market (parallel unknown) | No — context only |
| `price_tier` | player + market + price bracket | No — circular by construction |

`market` is `raw`, or `graded + grader + grade + qualifier`. There is
deliberately **no** grade-blind level: showing a PSA 9 the PSA 10 median
misleads by multiples even when labelled "context only", so a card with no
market-matched comps gets no number at all.

Confidence starts from the level and is downgraded, visibly, for each of:
asking-price basis (always, today), fewer than 5 comps, stale comps, and
wide dispersion. **Asking-price comps can never reach "high".**

### Cheap cards

Sub-$10 cards are eligible. They were not before: a flat `$10` minimum-savings
floor rejected a $4 card worth $12, which is 67% off. The floor was the wrong
tool — it cannot tell "cheap" from "junk", and it was simultaneously too
strict at $4 and meaningless at $500.

Cheap cards now clear a **higher** percentage bar (50% vs 30%) and a lower
dollar bar ($3), and they must have at least one attribute that makes a copy
scarce: rookie, autograph, patch, memorabilia, serial numbering, a non-base
parallel, or a grade. Anything cheap with none of those is rejected as
`common_card` and counted in the footer — a 60%-off base common is still a
base common.

Measured against the stored corpus, where 48% of all observations are under
$10: **the commodity filter rejects 94% of that cheap slice.** What survives
is the handful of cheap cards that are actually distinguishable.

Cheap finds are also marked as collector buys rather than flips. Below roughly
$10, postage and fees exceed the whole spread, so the report says so plainly
instead of printing a negative ROI as though something were wrong.

---

## 6. What the report tells you

Decision-first sections, each omitted when empty: **ACT NOW**, **TOP
OPPORTUNITIES**, **TARGET CARD HITS**, **INVESTMENT WATCHLIST**, **AUCTIONS
ENDING SOON**, **OFFER OPPORTUNITIES**, **WATCH**, **LOW CONFIDENCE / NEEDS
REVIEW**, **PRICE DROPS**, followed by search-coverage suggestions and a
**SYSTEM HEALTH** footer.

Every headline card answers the whole thesis: what it is, total acquisition
cost, estimated market value *with the comp level, sample size, basis, price
range and recency behind it*, the discount, the resale economics and their
assumptions, the confidence and why, the risks that could invalidate it, and
the link.

Auctions get their own block that never calls a current bid a price, and
shows the maximum bid that still preserves your margin.

The report is **focused and capped** (`settings.json` → `focus`). Replaying
a real August corpus produced a 4,255-line email; the same day under focus
is 215 lines, with no valuation changed. Length is not a cosmetic problem --
an email nobody finishes has not delivered its information. What focus
removed is counted in the thresholds footer, each group with the setting
that would bring it back.

---

## 7. Infrastructure

- **Scheduling: GitHub Actions** (`.github/workflows/daily-scan.yml`), daily
  ~8am Central plus manual dispatch. State (`data/seen_listings.json`,
  `data/ebay_alert_price_history.json`) is committed back to the repo after
  each run because runners are ephemeral.
- **Secrets**: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_TO` (plus
  optional eBay keys) as GitHub repo secrets. Never in code, never logged.
- **Testing**: pytest, CI on Python 3.9 and 3.12.
- **Logging**: rotating file log capped at ~2MB × 5, plus a
  failure-notification email on any unhandled exception.
- **Validation tool**: `python -m scripts.replay_corpus --legacy` replays the
  stored corpus through both the old and new engines and prints the
  before/after. This is how the claims in §0 were measured.

---

## 8. Known limitations (open, acknowledged)

- **No confirmed sold prices anywhere.** Structural. The highest-value next
  step is importing real sold comps by hand (130point / Card Ladder) through
  a `UserImportSource`.
- **Graded coverage is almost nil** — 0.7% of everything observed so far. The
  saved-search strategy is the cause; `src/search_terms.py` generates the
  queries that would fix it, but creating them on eBay is a manual step.
- **Auction, shipping and best-offer extraction are unvalidated** against
  real alert-email HTML. They fail safe (unknown, stated as unknown) rather
  than guessing.
- **No seller-risk signals.** Not present in alert emails; unverified whether
  they can be obtained at all.
- **No image intelligence, no LLM layer, no dashboard, no portfolio
  tracking.** All deliberately deferred behind trustworthy valuation.

---

## 9. Configuration you may actually want to change

| File / key | What it does |
|---|---|
| `watchlist.json` → `players`, `player_tiers` | Who to watch, and who counts as young core |
| `watchlist.json` → `target_cards` | Acquisition targets with `buy_zone` / `great_buy` / `immediate_alert` prices. Empty by default — see `_target_cards_example` |
| `settings.json` → `discount_threshold_pct`, `min_savings_dollars` | The deal gate (both must clear) |
| `settings.json` → `cheap_cards` | Rules below `price_ceiling` ($10): a higher percentage bar, a lower dollar bar, and the commodity filter. Set `enabled: false` to apply the ordinary rules at every price |
| `settings.json` → `valuation` | Comp quality gates: minimum sample, recency half-life, staleness window, dispersion ceiling, outlier threshold, and whether context-only levels may flag (leave this `true`) |
| `settings.json` → `economics` | Your real selling costs: fees, outbound shipping, supplies, tax, resale haircut |
| `settings.json` → `auctions` | Required margin for max-rational-bid, and what counts as ending soon |
| `settings.json` → `alerts` | How exceptional something must be to earn an ACT NOW slot. Keep the dollar figure in step with `focus.price_ceiling` -- a $40 card cannot save $150 |
| `settings.json` → `focus` | What reaches the email and how long it is: the price ceiling you shop under, the exception for an exceptional dearer card, whether auctions bid past your maximum are dropped, and the caps on total and per-section length |

---

## 10. Design principles (not negotiable)

1. Never build tooling to defeat a site's anti-bot measures.
2. Never go silent — always send something.
3. No black-box scoring. Every number traces to a rule and a data point.
4. A missing value means "unknown", never a guess.
5. Never confuse an asking price with a sold price, or a current bid with
   either.
6. Never compare different parallels or different grades as if they were the
   same card.
7. Prefer being uncertain over being confidently wrong.
8. Config controls behaviour; normal tuning needs no code changes.
9. Every real bug becomes a regression test.
10. CardPro discovers and analyses. You decide. It never buys anything.

---

## 11. File map

```
config/
  watchlist.json            -- players, tiers, acquisition targets
  settings.json             -- thresholds, valuation gates, economics, auctions, alerts, focus
src/
  main.py                   -- orchestration; one evaluation path for all sources
  card_identity.py          -- structured identity + negative signals
  matcher.py                -- player matching, grade details (grader/grade/qualifier)
  comps.py                  -- CompEngine: market-segmented, quality-gated valuation
  economics.py              -- fees, net proceeds, ROI, max rational bid, breakeven
  targets.py                -- acquisition targets and price bands
  search_terms.py           -- saved-search generation + coverage gaps
  desirability.py           -- what makes a card worth owning, separate from what it's worth
  reasons.py                -- the canonical vocabulary of "why not"
  observability.py          -- per-run data-quality accounting
  price_history.py          -- self-building comp corpus
  ebay_email_alerts.py      -- IMAP fetch, HTML extraction, listing-type detection
  ebay_client.py            -- eBay Browse/Insights client (dormant)
  craigslist_links.py       -- ready-to-click search links
  dedupe.py                 -- seen-listings tracking
  focus.py                  -- what reaches the email: price ceiling, bidding room, length cap
  report.py                 -- the decision-first email
  emailer.py                -- Gmail SMTP
  config.py                 -- .env + JSON config loader
  models.py                 -- the Listing dataclass
scripts/
  replay_corpus.py          -- before/after evidence against the real corpus
  test_ebay_alerts.py       -- standalone real-inbox validator
  test_email.py             -- SMTP smoke test
docs/
  CARDPRO_2_AUDIT.md        -- the current audit, scores, roadmap, data-source matrix
  AUDIT_AND_ROADMAP.md      -- the previous audit, kept for history
  PROJECT_STATUS.md         -- this file
data/
  seen_listings.json            -- dedupe state
  ebay_alert_price_history.json -- the self-built comp corpus
```
