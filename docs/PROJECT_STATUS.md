# CardPro — Project Status

_Last updated: August 21, 2026_

A personal, fully-automated sports card deal-finding system. Runs once a
day, scans your eBay saved-search alerts for the watchlist below, flags
listings that are genuinely underpriced against real comps, and emails a
ranked report — every morning, whether or not it finds anything.

**Status: live and running in production.** Daily scans execute in
GitHub's cloud on a schedule (not dependent on a laptop being on), real
deals are being flagged and emailed, and the whole pipeline has been
validated against real data end-to-end, including a live production
incident found and fixed today (see "Recent incident" below).

---

## 1. What this actually answers

Not "is this price low" — the system was explicitly built to distinguish:

- **Cheap** — a low asking price, full stop.
- **A genuine deal** — low relative to what comparable copies of *that
  specific card* actually go for, with the strength of that comparison
  shown plainly (`[LOOK NOW]` / `[WATCH]` / `[LOW CONFIDENCE]`).
- **Worth owning long-term** — separately tagged (`[YOUNG CORE]`,
  `[ROOKIE CARD]`, `[AUTO]`, `[MEM]`) based on your own conviction about a
  player and the card's own attributes, never blended into a hidden score.

Every number and tag in the report is traceable to a specific rule or
data point — there is no black-box ranking anywhere in this system.

---

## 2. Data sources

| Source | Status | How |
|---|---|---|
| **eBay saved-search email alerts** | **Live, primary source** | eBay's own "save this search + email me" feature, read via Gmail IMAP. No eBay API access required. |
| **eBay Browse/Marketplace Insights API** | Built, dormant | Full implementation exists (`ebay_client.py`) for if/when API access is ever granted — the developer account application was declined. Currently unused; the alerts path takes over automatically when API credentials aren't set. |
| **Craigslist (chicago.craigslist.org)** | Link-only, not scraped | Craigslist has explicit, confirmed anti-automation defenses (tested against both headless and headed browser automation — same 403 either way). The report includes a ready-to-click search link per player instead. |
| **Facebook Marketplace** | Explicitly out of scope | Never built, per original spec. |
| **Sportlots, Fanatics Collect (formerly PWCC)** | Ruled out | Both have explicit ToS bans on automated scraping/bots — same category as Craigslist. |
| **COMC, MySlabs, 130point** | Ruled out / not pursued | No confirmed permission to automate against; COMC inventory is largely cross-listed to eBay anyway, so it's already partially covered. |
| **SportsCardsPro (PriceCharting sister site)** | Found, not pursued | Has a genuine official, documented, token-authenticated API — but it's a paid subscription, and you opted not to add a paid dependency. |

**Guiding rule, held throughout:** this project does not build tooling to
defeat a site's own anti-bot/anti-automation defenses, regardless of how
low-stakes the ask. Every data source above was evaluated against that
rule before anything was built.

---

## 3. Watchlist (`config/watchlist.json`)

**Legends** (established/retired — the deal is just about the deal):
Michael Jordan, Walter Payton, Ernie Banks, Ryne Sandberg, Dick Butkus,
Scottie Pippen, Frank Thomas, Gale Sayers.

**Young Core** (actively bet on for long-term growth — tagged
`[YOUNG CORE]` in the report):

| Team | Players |
|---|---|
| Bears | Caleb Williams, Rome Odunze, Luther Burden, Colston Loveland |
| Bulls | Josh Giddey, Matas Buzelis, Caleb Wilson |
| Blackhawks | Connor Bedard |
| White Sox | Munetaka Murakami, Kyle Teel, Colson Montgomery |
| Cubs | Pete Crow-Armstrong |

White Sox/Cubs additions were deliberately scoped to MLB-active players
with a real card market, skipping un-debuted minor-league prospects
(no price data yet to judge a "deal" against).

Editable any time with no code changes — just edit the JSON and re-run.

---

## 4. Pipeline (current, as of the hierarchical-comps work)

```
eBay saved-search alert email
  → Gmail IMAP fetch (All Mail folder, not just Inbox — see §7)
  → HTML extraction (title, price, shipping)
  → player match (matcher.py: both name parts required)
  → grading detection (PSA/BGS/SGC/CSG + number)
  → rookie detection (RC/Rookie keyword)
  → Card Identity extraction (card_identity.py: year, manufacturer, set,
    parallel, card#, serial#, autograph, memorabilia, lot-detection)
  → lot listings excluded entirely (a lot's price isn't a single card's price)
  → price + identity fields recorded into self-building comp history
  → hierarchical comp lookup: exact → near_exact → family → price_tier
    (first level with enough samples wins; confidence shown: HIGH/MEDIUM/LOW)
  → deal gate: must clear BOTH ≥30% under comp median AND ≥$10 saved
    (computed against total cost — price + shipping when shipping is known)
  → truncated-title grade recovery (re-fetches real title for flagged,
    graded, truncated listings only)
  → dedupe against prior runs (only new-or-price-dropped listings reported)
  → ranked report: sorted by $ saved, action-labeled, TOP PICKS summary
  → emailed via Gmail SMTP
```

---

## 5. Card Identity Engine (`src/card_identity.py`)

Regex/keyword extraction, same "dumb but inspectable" philosophy as the
rest of the project — every field is either a confident extraction or
explicitly `None` (unknown), never a guess:

- `year`, `manufacturer`, `set_name`, `parallel`, `card_number`,
  `serial_number` — each with a value + confidence + source
- `is_autograph`, `is_memorabilia`, `is_lot` — booleans, confidently
  `False` when the keyword is absent (sellers reliably mention these when
  true)

This is what makes hierarchical comps possible (§6) and what surfaces as
the `Card: 2024 Panini Prizm Silver #123 (23/99)` line and `[AUTO]`/`[MEM]`
tags in the report.

---

## 6. Comps: hierarchical, confidence-scored

Replaced the original single price-tier bucket with four progressively
broader levels, tried in order — first one with enough samples wins:

| Level | Match on | Confidence shown |
|---|---|---|
| `exact` | player + year + set + parallel + card# + grader + grade | **HIGH** |
| `near_exact` | player + year + set + parallel + raw/graded | **MEDIUM** |
| `family` | player + year + set | **LOW** |
| `price_tier` | player + raw/graded + price bracket (original v1 logic) | **LOW** |

A level is only attempted when the listing actually has the identity
fields that level needs — missing data falls through to the next level,
never a guess. Comp confidence maps directly to the report's action
labels: `[LOOK NOW]` (high), `[WATCH]` (medium), `[LOW CONFIDENCE]` (low).

**Honest limitation:** there's no real sold-comps feed on this data path
(no eBay API access). Comps are self-built by accumulating every price
observed in alert emails over time, in `data/ebay_alert_price_history.json`
— an asking-price proxy, always labeled as such (`[comp = active-listing
proxy, not real sold data]`), that gets stronger with volume, not
calendar time.

---

## 7. Report format

One plain-text email per day, always sent (a "No deals today" confirmation
if nothing qualifies — silence is never used to mean "still running").

- Ranked by **dollar amount saved**, not percent (a blended judgment call,
  by design, was rejected — percent alone lets trivial deals through)
- **TOP PICKS** summary (top 3, only shown once the list is long enough to
  need skimming)
- Per-deal: price + shipping breakdown (or an honest "shipping unknown"
  note — never assumed $0), comp median + sample size + confidence level,
  card identity line, all applicable tags
- Tags: `[YOUNG CORE]`, `[ROOKIE CARD]`, `[AUTO]`, `[MEM]` — shown
  separately, never combined into a score
- Action labels: `[LOOK NOW]` / `[WATCH]` / `[LOW CONFIDENCE]` — derived
  purely from comp confidence, explicitly not a buy recommendation
- Craigslist quick-check links per watchlist player

---

## 8. Infrastructure

- **Scheduling: GitHub Actions**, not a laptop cron job. `.github/workflows/daily-scan.yml`
  runs on a daily schedule (~8am Central, drifts ~1hr with DST — accepted
  tradeoff) plus manual `workflow_dispatch` triggering. This replaced the
  original Mac `cron` setup specifically because cron doesn't fire through
  sleep/shutdown — the actual failure mode that mattered for "email every
  morning."
- **State persistence**: since GitHub Actions runners are ephemeral, the
  workflow commits `data/seen_listings.json` and
  `data/ebay_alert_price_history.json` back to the repo after every run —
  small automated `cardpro-bot` commits are expected, not a problem.
- **Secrets**: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_TO` (and
  optional `EBAY_CLIENT_ID`/`SECRET`) stored as GitHub repo secrets, never
  in code or chat.
- **Local Mac cron**: uninstalled (`scripts/uninstall_cron.sh`) once the
  cloud schedule was confirmed working, to avoid duplicate emails.
- **Testing**: 168 tests, pytest, GitHub Actions CI on Python 3.9 + 3.12
  (`.github/workflows/tests.yml`).
- **Logging**: rotating file log (`logs/scraper.log`, capped ~2MB × 5
  backups) plus a failure-notification email on any unhandled exception —
  a crashed run never fails silently.

### Keeping alert emails out of your inbox

Since the IMAP search now targets Gmail's **All Mail** (not just Inbox —
see incident below), you can freely set up a Gmail filter that routes
eBay's alert emails straight out of your inbox (Skip the Inbox / Archive)
without breaking anything — only actual deletion (emptying Trash) makes a
message invisible to the scraper. Steps are in `README.md`.

---

## 9. Recent incident (today, resolved)

The switch to searching Gmail's All Mail folder (to support inbox
decluttering) shipped with a real bug: Python's `imaplib` doesn't quote
mailbox names, so the default `[Gmail]/All Mail` (which contains a space)
was sent to Gmail's IMAP server unquoted and rejected outright (`BAD
Could not parse command`). This silently broke every scheduled run —
correctly triggered the failure-notification email, but never actually
scanned anything.

**Found, fixed, and verified live** the same day: quoted the mailbox
argument, re-ran the workflow, confirmed via direct Gmail inspection that
a real 15-deal report was generated and delivered correctly (independently
verified by decoding the raw MIME source — the email itself was always
correctly formed; an unrelated display artifact in the diagnostic tool
used to check it briefly suggested otherwise and was ruled out).

Also caught in the same pass: `config/settings.json`'s `ebay_alerts.enabled`
was still committed as `false` (a local-only edit on the Mac had flipped
it to `true` but was never committed) — would have made the new cloud
schedule silently send Craigslist-links-only emails forever. Fixed.

---

## 10. Known limitations (open, acknowledged)

- **No real sold-comps feed** — structural, not a bug. Would require eBay
  API access (declined) or a paid third-party API (declined, cost).
- **Auction vs. fixed-price listings aren't distinguished** — a live
  auction's current bid could be misreported as a final price. Needs real
  alert-email samples showing auction data before it can be built
  correctly.
- **No shipping data on most listings** — extraction exists and is used
  when eBay's alert email happens to include it, but most listings show
  "shipping unknown" rather than a real number.
- **No seller-risk signals** (feedback, account age) — unverified whether
  this data even exists in alert emails.
- **Truncated-title grade recovery** depends on eBay's item pages being
  reachable from wherever the scan runs — untested against a real block,
  fails safe (marks grade uncertain) if it doesn't work.

---

## 11. Backlog, in priority order

See `docs/AUDIT_AND_ROADMAP.md` for the full architectural audit this is
drawn from. Remaining, roughly by value:

1. **Target-card watchlist** — specific year/set/card#/grade targets
   (e.g. "1986 Fleer Jordan #57, PSA 8, under $7000"), coexisting with the
   player-level watchlist, `[TARGET CARD]` tag.
2. **Comp sample recency shown in the report** — age of the observations
   backing a comp, not just the count (a median from 3 five-month-old
   observations reads identically to 40 fresh ones today).
3. **Auction vs. fixed-price detection** — pending real sample data.
4. **"Why is this cheap?" reasoning field** — surfaces likely explanations
   (grader mismatch, lower grade than comps, reprint keyword, thin sample)
   on flagged deals.
5. **Full sectioned report redesign** (Top Opportunities / Long-Term
   Targets / Target Card Hits / Watchlist / Auctions Ending Soon).
6. **Data-quality/observability pass** — comp-coverage logging, thin-sample
   warnings surfaced more visibly.

---

## 12. Design principles (held throughout, not negotiable)

1. Never build tooling to defeat a site's own anti-bot/anti-automation
   measures — full stop, regardless of pressure or stakes.
2. Never go silent — always send something (deals, "no deals," or a
   failure notice), so "nothing happened" and "it broke" are never
   indistinguishable.
3. No black-box scoring — every tag, label, and number is individually
   inspectable and traceable to a specific rule.
4. A missing value means "unknown," never a guess.
5. Config controls behavior wherever practical — no code changes needed
   for normal tuning (watchlist, thresholds, tiers).
6. Every real bug becomes a regression test.

---

## 13. File map

```
config/
  watchlist.json           -- players, tiers (editable, no code changes)
  settings.json             -- thresholds, eBay/Craigslist/email/alerts config
src/
  main.py                   -- daily orchestration entry point
  card_identity.py          -- year/set/parallel/serial/auto extraction
  comps.py                  -- price-tier + hierarchical comp matching
  price_history.py          -- self-building comp corpus (eBay-alerts path)
  matcher.py                -- player/grading/rookie keyword matching
  ebay_email_alerts.py      -- IMAP fetch + HTML extraction from alert emails
  ebay_client.py            -- eBay Browse/Insights API client (dormant)
  craigslist_links.py       -- ready-to-click search link builder
  dedupe.py                 -- seen-listings tracking
  report.py                 -- email body construction
  emailer.py                -- Gmail SMTP send
  config.py                 -- .env + JSON config loader
  models.py                 -- Listing dataclass
scripts/
  test_ebay_alerts.py       -- standalone real-inbox validator
  test_email.py              -- SMTP smoke test
  uninstall_cron.sh          -- removes the (now-retired) local Mac cron job
  install_cron.sh            -- (legacy, superseded by GitHub Actions)
.github/workflows/
  daily-scan.yml             -- the actual production schedule
  tests.yml                  -- CI (pytest, Python 3.9 + 3.12)
docs/
  AUDIT_AND_ROADMAP.md       -- full architectural audit + backlog detail
  PROJECT_STATUS.md          -- this file
data/                        -- gitignored locally, committed by CI
  seen_listings.json          -- dedupe state
  ebay_alert_price_history.json -- self-built comp corpus
```

168 tests passing. Branch: `claude/sports-card-deal-scraper-6imq2v`
(the repository's default branch).
