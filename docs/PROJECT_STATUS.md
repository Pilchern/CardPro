# CardPro — Project Status

_Last updated: August 26, 2026 (reliability pass; identity KPI; one row per listing)_

A personal, automated sports-card deal-finding system. Runs once a day,
scans your eBay saved-search alerts for the watchlist below, values what it
finds against comparable sales, and emails a decision-first report — every
morning, whether or not it finds anything.

**Status: live and running in production, with a deliberately much stricter
definition of "deal" than it had before the 2.0 pass -- and, since August 26,
a measured account of why that definition currently matches nothing.**

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

### The current state: flags nothing, and here is the measured reason

Until August 26 the honest answer to "why zero?" was a guess between three
candidates. `python -m scripts.replay_corpus` now prints the identity KPI
from [CARDPRO_2_AUDIT.md](CARDPRO_2_AUDIT.md) §8, so it is a measurement.
Against the stored corpus (907 distinct listings, 2026-08-21 .. 2026-08-26,
100% asking basis):

```
  field resolved:
    year           734   80.9%
    set_name       154   17.0%
    parallel        45    5.0%
    card_number    162   17.9%
    grader          11    1.2%
  complete key for level:
    exact            4    0.4%
    same_card        7    0.8%
    same_set       134   14.8%
  first blocker for a flag-eligible (same_card) key:
    set_name         600   66.2%
    year             173   19.1%
    parallel         127   14.0%
    (key complete)     7    0.8%
  same_card: 7 distinct keys, 0 observations (0.0%) in a bucket with >3 members
  exact: 4 distinct keys, 0 observations (0.0%) in a bucket with >3 members
```

Those are the observations *as recorded*, not a verdict on the extractor as
it stands this minute -- identity extraction is under active repair, and the
numbers move as it improves and the corpus refills. The command is the source
of truth; this paste is a snapshot of it.

That is two separate problems, and the KPI deliberately keeps them apart:

1. **Identity resolution.** 99.2% of observations cannot build a key at the
   narrowest level allowed to declare a deal. For two-thirds of the corpus
   the first thing missing is the set name; year and parallel account for
   the rest. Attributing each blocked listing to its *first* blocker rather
   than counting missing fields independently is what makes this a work
   queue: fixing parallel extraction alone would move none of the 600
   listings that never got as far as a set name.
2. **Corpus depth.** Of the 7 observations that do have a complete
   `same_card` key, **none** sits in a bucket with more than
   `min_comps_required` other members. A complete key that nothing else
   shares is worth nothing, so even perfect extraction on those 7 would
   change today's report by zero listings.

The same run shows what the engine then does with that: 0 `exact` buckets,
0 `same_card`, 8 `same_set`, 74 `price_tier`; every valuation lands at `low`
confidence, and the gates fire 866× `context_only_level`, 866×
`concentrated_sample`, 124× `dispersed_comps`. The concentration gate is
unavoidable at this age -- the corpus spans five days and
`min_comp_span_days` is 7, so no bucket in it can clear that gate yet
regardless of how good the extraction gets.

**So zero deals is not a threshold that needs relaxing.** There is nothing
to tune. The work, in order, is title parsing (set name first), then time.

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
| **130point** | Manual workflow, with tooling around it | Free, and the best public source of eBay + Goldin sold prices *including accepted Best Offers*. No API, so a human does the lookup: `src/comp_requests.py` says which cards are worth looking up and hands you the query, and `python -m scripts.add_sold_comp` records the answer. Nothing is entered yet. |
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
  → asking prices recorded to the comp corpus, ONE ROW PER LISTING
      (auctions excluded -- a bid is not a price; blocked listings excluded)
      re-seeing a listing updates its row (earliest date, latest price)
      rather than appending -- see §7 for why that matters to the gates
  → comp lookup: exact → same_card → same_set → price_tier
      only exact and same_card may declare a deal
      every level segments by market (raw / grader+grade+qualifier)
      the listing itself is excluded from its own comp
      MAD outlier trim, time-decay weighting, staleness + dispersion gates
  → economics: fees, outbound shipping, supplies, tax, net proceeds, ROI
  → auctions: max rational bid instead of a discount claim -- the same
      resale haircut the profit maths uses, and it says so when shipping
      is unknown and the ceiling is therefore only an upper bound
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
  → sold-comp suggestions: which 130point lookups would unlock the most
      listings, and how many listings no sold comp could ever match
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

Measured against the corpus as it stood on August 22, where 48% of all
observations were under $10: **the commodity filter rejects 94% of that
cheap slice.** What survives is the handful of cheap cards that are actually
distinguishable. (On today's 907-listing corpus a third of observations are
under $10; the filter's job is unchanged.)

Cheap finds are also marked as collector buys rather than flips. Below roughly
$10, postage and fees exceed the whole spread, so the report says so plainly
instead of printing a negative ROI as though something were wrong.

---

## 6. What the report tells you

Decision-first sections, each omitted when empty: **ACT NOW**, **TOP
OPPORTUNITIES**, **TARGET CARD HITS**, **INVESTMENT WATCHLIST**, **AUCTIONS
ENDING SOON**, **OFFER OPPORTUNITIES**, **WATCH**, **LOW CONFIDENCE / NEEDS
REVIEW**, **PRICE DROPS**, followed by a **SYSTEM HEALTH** footer, the
sold comps worth adding, Craigslist quick-check links and search-coverage
suggestions.

Every headline card answers the whole thesis: what it is, total acquisition
cost, estimated market value *with the comp level, sample size, basis, price
range and recency behind it*, the discount, the resale economics and their
assumptions, the confidence and why, the risks that could invalidate it, and
the link.

Auctions get their own block that never calls a current bid a price, and
shows the maximum bid that still preserves your margin.

The footer also ranks the sold comps worth going and getting
(`src/comp_requests.py`): the card identities the most of today's listings
are waiting on, each with a query to paste into 130point and the command
that records the answer. Next to it, the number of listings that could not
be identified precisely enough for *any* sold comp to match -- because when
that number dwarfs the suggestions, typing prices in will not help and
title parsing is the real work. On the stored corpus replayed as listings
today it is 900 of 907, which is the honest verdict on where the effort
belongs.

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
  each run because runners are ephemeral. The job has a `concurrency` group
  (a manual dispatch cannot race the cron run into a rejected push), a
  15-minute timeout, and it runs the test suite before the scan -- two
  seconds of pytest against the alternative, which is emailing conclusions
  drawn by broken code. The state-persisting step no longer swallows errors
  from `git add`, and its push rebases and retries up to four times; a
  rejected push used to mean the day's observations were lost, since past
  the IMAP lookback window there is nothing to re-read.
- **Secrets**: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_TO` (plus
  optional eBay keys) as GitHub repo secrets. Never in code, never logged.
- **Testing**: pytest, CI on Python 3.9 and 3.12, and the same suite gates
  the daily scan.
- **Dependencies**: `requirements.txt` pins upper bounds (`requests<3`,
  `python-dotenv<2`, `beautifulsoup4<5`). The workflow installs fresh every
  run, so an unbounded constraint would put a parser's next major release
  straight into production -- and the symptom would be zero listings
  extracted, which looks exactly like a quiet day.
- **Logging**: rotating file log capped at ~2MB × 5, plus a
  failure-notification email carrying the traceback on any unhandled
  exception. `.gitignore` covers the rotated files as well as the live one
  (`logs/*.log*`); `scraper.log.1` through `.5` carry every title, price and
  your email address.
- **State files fail loudly.** A corpus or seen-listings file that exists
  but will not parse raises (`price_history.CorruptCorpus`,
  `dedupe.CorruptSeenListings`) instead of quietly starting fresh. The old
  behaviour returned `{}` with a warning saying the old file was left in
  place, which was true for about thirty seconds -- until `save()` replaced
  it with the day's observations and the workflow committed the wipe.
  Raising means the run aborts, the traceback is emailed, and there is
  nothing staged to commit. `price_history.save()` additionally refuses to
  replace a non-empty corpus with an empty one; a shrink that is not a wipe
  still goes through, because that is what pruning looks like.
- **Sockets time out.** IMAP (60s) and SMTP (30s) both set one. Without
  them, a server that accepts the connection and then stalls raises nothing,
  the failure-notification handler never fires, and the job burns until
  GitHub's six-hour cap -- the one path in the system that produced no
  report *and* no failure email.
- **A mailbox that cannot be read is not an empty mailbox.** An IMAP SEARCH
  error raises rather than rendering as "Emails scanned: 0"; individual
  messages that fail to fetch are counted and surfaced in the health footer
  instead of skipped in silence; and the "eBay changed their email template"
  canary (N alert emails, zero listings extracted) is now a health-footer
  warning rather than a log line on a runner GitHub deletes minutes later.
- **Validation tools**: `python -m scripts.replay_corpus` replays the stored
  corpus through the engine and prints the identity KPI -- this is how §0
  was measured; `--legacy` adds the v1 engine's numbers alongside for the
  before/after. `python -m scripts.collapse_corpus_duplicates` is the
  one-shot migration to one row per listing; it is idempotent and refuses to
  write if the collapse would lose a listing.

---

## 8. Known limitations (open, acknowledged)

- **Identity extraction is the binding constraint, and it is now measured.**
  99.2% of stored observations cannot form a key at the narrowest level
  allowed to declare a deal; the set name is the first missing field for
  66% of them. See §0 for the full KPI and `python -m scripts.replay_corpus`
  to re-run it. Everything else on this list is downstream of that.
- **No confirmed sold prices anywhere.** Structural: 100% of the corpus is
  asking prices. The hand-entry path now exists (`src/sold_comps.py`,
  `python -m scripts.add_sold_comp`) and the report ranks which lookups
  would unlock the most listings (`src/comp_requests.py`), but nothing has
  been entered yet, and hand entry tops out around twenty comps. Card Ladder
  remains the paid alternative and is still not bought.
- **The corpus is five days deep**, and `min_comp_span_days` is 7, so no
  bucket in it can currently clear the sample-span gate. That resolves with
  time and nothing else.
- **Graded coverage is almost nil** — 11 of 907 observations, 1.2%. The
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
| `settings.json` → `valuation` | Comp quality gates: minimum sample, recency half-life, staleness window, dispersion ceiling, outlier threshold, how much calendar spread a bucket's observations need (`min_distinct_comp_dates`, `min_comp_span_days` -- six asks captured in one morning are one snapshot, not six readings), and whether context-only levels may flag (leave `require_flag_eligible_comp` `true`) |
| `settings.json` → `sold_comps.path` | Where hand-entered sold comps live (`config/sold_comps.json`). Add one with `python -m scripts.add_sold_comp --help`; the report's footer says which ones are worth the trip to 130point |
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
  sold_comps.json           -- hand-entered sold prices (empty by default)
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
  sold_comps.py             -- hand-entered sold prices, the only real transaction data
  comp_requests.py          -- which sold comps to go and get, ranked by listings unlocked
  price_history.py          -- self-building comp corpus, one row per listing
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
  replay_corpus.py          -- identity KPI + before/after evidence against the real corpus
  collapse_corpus_duplicates.py -- one-shot migration to one row per listing (idempotent)
  add_sold_comp.py          -- record one hand-entered sold comp, validated before writing
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
