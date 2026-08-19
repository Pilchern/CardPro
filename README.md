# Card Deal Scraper

Daily scan of eBay for underpriced sports card listings on a watchlist,
emailed to you as a ranked report -- plus ready-to-click Craigslist search
links, since Craigslist can't be scraped automatically (see below).

**Current status: eBay API access was declined; running on the
email-alerts path instead.** eBay's developer account application was
rejected outright (see "eBay account declined" below), so the eBay Browse
API path in this README's step 1 isn't usable. Instead, eBay data comes
from its own saved-search email alerts, read via IMAP -- see "eBay via
saved-search email alerts" below. Validated against real alert emails
(2026-08-18) and working. If the API appeal ever succeeds, the Browse API
path takes over automatically with no config changes needed -- it's
checked first.

## What it does

1. For each player on your watchlist, pulls **active** eBay listings --
   either via the Browse API, or via eBay's own saved-search email alerts
   if the API isn't available (see below for both).
2. Builds comps (market-value medians) per player/card-type: real eBay
   sold data when the API + Marketplace Insights are available, otherwise
   a self-building history of observed prices (from the API's active
   listings, or from alert emails) that gets more reliable over time.
3. Flags any active eBay listing priced 30%+ (configurable) below its
   matched comp median.
4. Drops anything already emailed in a prior run, unless its price has
   dropped further.
5. Emails you a ranked report -- biggest discount first -- plus a
   Craigslist quick-check link per watchlist player so you can eyeball
   that source yourself in a few seconds. If no eBay deals qualify, you
   still get a short "nothing today" email (with the Craigslist links
   still attached), not silence. If the run crashes outright (network
   blip, eBay API issue, etc.), you get a short "Scan FAILED" email
   instead of nothing at all -- same "not silence" principle applied to
   errors, not just the zero-deals case.

## eBay account declined

eBay's developer account registration was rejected with their generic
automated message: *"Your account registration was rejected due to
problems with the data provided or other irregularities."* This is a
common, non-specific rejection -- it doesn't require basic Browse API
access to go through any manual review at all normally, so a full-account
decline usually points to something in the account data itself (name/
address mismatch between the eBay account and the application, a very new
or thin eBay purchase/selling history, unverified phone, etc.) rather than
a policy objection to the use case.

**Worth trying:** the Contact Channels / FAQ page linked from the
rejection screen, or emailing `developer-support@ebay.com` directly and
asking specifically what triggered it. If that gets resolved and you get
real keys, nothing else needs to change -- see the status note at the top
of this README.

**In the meantime:** `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` are optional.
Leave them unset (or as the `.env.example` placeholder text) and the
scraper runs fine without eBay -- either by falling through to the
email-alerts path below if you've enabled it, or (if that's not enabled
either) by logging a warning, skipping straight to building the Craigslist
links, and sending a short "eBay not configured" email instead of
crashing. Once real API credentials are added, eBay scanning switches back
to the Browse API automatically on the next run.

## eBay via saved-search email alerts

Since the API is unavailable, this is a second way to get real eBay
listing data with **zero API access required**: eBay itself has a
saved-search feature that emails you when new matching listings appear.
You set that up once on eBay's site; this just reads those emails out of
your own Gmail inbox over IMAP (same Gmail App Password already used for
SMTP -- App Passwords work for IMAP too, no new credential needed) and
feeds them into the same matching/flagging/report pipeline as the API
path. Nothing about eBay's own systems is touched or automated against --
eBay decides what to send and when; this only processes mail you already
receive.

**Validated (2026-08-18)** against 14 real alert emails covering the full
watchlist: 327 listings extracted, 96 correctly matched to watchlist
players, zero observed player-name collisions (e.g. Caleb Williams vs.
Caleb Wilson stayed correctly separated).

**Truncated-title grades:** eBay truncates long titles in these emails,
which can occasionally cut a grade number mid-digit (a "PSA 10" showing as
"PSA 1…"). This never affects which comp bucket a listing lands in (raw
vs. graded is unaffected either way), only the grade text shown in the
report. For listings that actually clear the deal threshold (a handful a
day, not the full batch), the scraper tries fetching the real title from
eBay's own item page to recover the true grade
(`ebay_email_alerts.fetch_full_title`). This hasn't been tested against
eBay's real site (this project's sandbox can't reach ebay.com to check
during development) -- if it works, the report shows the corrected grade
automatically; if eBay blocks it, it fails safe: the listing still shows
up, just with an explicit "(grade uncertain -- eBay truncated the title,
actual grade may differ)" note instead of asserting a possibly-wrong
number. No further escalation is attempted either way.

**One real tradeoff worth knowing:** eBay's saved-search alerts only cover
*newly listed* items, not their full standing inventory, and there's no
sold-comps feed on this path. So comps are built by accumulating every
price this script observes in alert emails over time
(`data/ebay_alert_price_history.json`, pruned after 180 days by default) --
weaker than real sold data, same "not real sold data" caveat already
labeled elsewhere in the report. How fast comps become usable depends on
volume, not calendar time: a bucket needs `ebay.min_comps_required`
observations (default 3) before it gets a comp at all, and every matched
listing in a run counts toward that -- so a high-volume player can clear
that bar on day one if a single alert batch contains enough listings for
them, while a low-volume player may take longer to accumulate enough
history.

### Setup

1. On eBay's site (not in this repo): for each watchlist player, search
   for their cards, then use eBay's "Save this search" option and turn on
   email alerts for it. Repeat for every player you want covered.
2. In `config/settings.json`, set `ebay_alerts.enabled` to `true`.
3. Run the standalone check against your real inbox:
   ```bash
   python -m scripts.test_ebay_alerts
   python -m scripts.test_ebay_alerts --raw   # also shows listings before player-matching
   ```
   This prints what it can extract from your actual alert emails --
   nothing is sent or written to disk. If eBay ever changes their email
   template and matched listings come back empty or obviously wrong, run
   this again and share the `--raw` output so the parser can be adjusted.
4. Once that looks right, `python -m src.main --dry-run` will show the
   full report, comps included.

### Config (`config/settings.json`'s `ebay_alerts` section)

| Key | What it does |
|---|---|
| `enabled` | Turns this path on. Only takes effect when `EBAY_CLIENT_ID`/`SECRET` are absent (the Browse API path always wins if both are configured). |
| `sender_contains` | IMAP filters alert emails to ones whose From address contains this (default `ebay.com`). If real alerts come from an address that doesn't match, alerts won't be found -- `test_ebay_alerts.py` will tell you if that's happening. |
| `lookback_days` | How many days back to search the inbox each run (default 2 -- eBay sends these roughly daily). |
| `price_history_path` | Where the self-building comp history is stored (default `data/ebay_alert_price_history.json`, gitignored). |
| `price_history_max_age_days` | How long an observed price stays in the comp history before aging out (default 180). |

## Known limitation: eBay sold comps

eBay's Browse API only returns **active** listings. Real historical sold
prices come from eBay's **Marketplace Insights API**, which is a
limited-release API -- a standard free developer account is not granted
access to it by default; you have to apply for it separately in the eBay
developer portal.

Until/unless that access is approved, this scraper falls back to using the
median price of **currently active** listings for a player/card-type as a
rough stand-in for "market value." That's a weaker signal than real sold
comps (it reflects what sellers are asking, not what buyers actually paid),
and any deal flagged using the fallback is labeled in the report as
`[comp = active-listing proxy, not real sold data]` so you can tell the
difference at a glance. Once/if you get Insights access, real sold comps
are used automatically and this note stops appearing.

## Why Craigslist isn't scraped

The original plan was to hit Craigslist's RSS search feed the same way as
eBay. In testing, Craigslist's bot mitigation turned out to hard-block
automated access outright:

- Plain HTTP requests (Python `requests`, `curl`) got a 403 "Your request
  has been blocked" page (with a `blockID`), even with a fully realistic
  browser User-Agent -- ruling out a simple header problem.
- A real, manually-used browser on the same network loaded the same URL
  fine -- ruling out an IP-level block.
- Driving an actual Chromium instance via Playwright (both headless *and*
  headed/visible) got the **same** block page -- meaning Craigslist is
  fingerprinting the browser-automation layer itself (Playwright/CDP), not
  just headers or rendering mode.

Getting past that would require deliberately defeating Craigslist's
anti-automation controls (spoofing `navigator.webdriver`, hiding CDP
artifacts, etc.). That's a different thing than presenting as a normal
browser, and isn't something this project does, regardless of how low-
stakes a personal card-deal script is.

Instead, `src/craigslist_links.py` just builds a plain search URL per
watchlist player, and the daily email includes them under a "Craigslist
quick check" section so you can skim Craigslist yourself in a few
seconds. No scraping, no automation, fully within Craigslist's terms.

## Setup

### Running without eBay (current situation)

eBay access is optional -- see "eBay account declined" above. Everything
below except step 1 (eBay keys) works fine without it:

```bash
cp .env.example .env   # fill in just the GMAIL_* / EMAIL_TO lines for now
pip install -r requirements.txt
python -m scripts.test_email
python -m src.main --dry-run   # will show "eBay not configured" + Craigslist links
```

Sends one real test email using only your Gmail credentials -- no eBay
keys required. (Craigslist needs no separate testing now that it's just
link generation -- you'll see the links in any report the scraper
produces, including a `--dry-run`.)

If/when real eBay keys land (approval or a resolved appeal), drop them
into `.env` and pick up at step 4 below -- no code changes needed, eBay
scanning just starts working on the next run.

### 1. Get an eBay developer account + keys

- Sign up at https://developer.ebay.com (free).
- Create an application to get a production **Client ID** and **Client
  Secret** (Browse API access is included by default; Marketplace Insights
  requires separately applying for access, see above).
- If the account itself gets rejected, see "eBay account declined" above
  before giving up on this step -- it's usually fixable.

### 2. Get a Gmail App Password

This uses your own Gmail account via SMTP -- no third-party email service,
no extra account to manage, free. Tradeoff: deliverability/analytics are
whatever Gmail gives you, which is fine for a single daily email to
yourself.

- Enable 2-Step Verification on the sending Gmail account if it isn't
  already: https://myaccount.google.com/security
- Generate an App Password: https://myaccount.google.com/apppasswords
  (choose "Mail" / "Other", copy the 16-character password it gives you).

### 3. Install

```bash
cd ~/Documents/AI-Lab
git clone <this-repo-url> cardpro
cd cardpro

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and fill in:
#   GMAIL_ADDRESS, GMAIL_APP_PASSWORD  (required)
#   EMAIL_TO (defaults to GMAIL_ADDRESS if left out)
#   EBAY_CLIENT_ID, EBAY_CLIENT_SECRET (optional -- leave blank/placeholder if you don't have them yet)
```

### 4. Verify the eBay category ID (skip if you don't have eBay keys yet)

eBay reorganizes its trading-card category tree from time to time, so
before your first real run, confirm `config/settings.json`'s
`ebay.category_id` (defaults to `212`, "Sports Trading Cards") is still
current for your account/marketplace:

```bash
python -m scripts.lookup_ebay_category
python -m scripts.lookup_ebay_category "Football Cards"   # or any other query
```

This calls eBay's Taxonomy API with your real credentials and prints the
matching category IDs + their place in the tree. Update
`ebay.category_id` if it's changed.

### 5. Test it

```bash
python -m src.main --dry-run
```

This prints the report to your terminal instead of emailing it, and does
**not** touch the dedupe file -- safe to run repeatedly while testing.
Check `logs/scraper.log` for details if something looks off (e.g. a 403
from eBay usually means the app doesn't have Marketplace Insights access
yet -- see the limitation above, it's expected and handled).

Once a dry run looks right, run it for real:

```bash
python -m src.main
```

### 6. Install the daily cron job

```bash
bash scripts/install_cron.sh        # defaults to 8:00am daily
bash scripts/install_cron.sh 7 30   # or specify HOUR MINUTE, e.g. 7:30am
```

This adds (or replaces) a single crontab entry that runs the scraper daily
using this project's virtualenv if present, and appends output to
`logs/cron.log`. Verify with `crontab -l`. To remove it later, run
`crontab -e` and delete the line marked `# card-deal-scraper`.

**Note on macOS + cron:** if `logs/cron.log` stays empty and nothing runs,
your Mac's cron may need Full Disk Access under System Settings > Privacy
& Security (this varies by macOS version) -- grant it to `/usr/sbin/cron`.

**Note on logs:** `logs/scraper.log` (the script's own detailed log) is
capped at ~2MB with 5 rotated backups, so it won't grow unbounded over
months of daily runs. `logs/cron.log` (cron's redirected stdout/stderr,
just a few lines a day) isn't rotated -- it's tiny enough not to matter,
but nothing stops you from truncating it (`> logs/cron.log`) whenever you
feel like it.

## Running tests

The matching/comps/dedupe/report logic and the full daily-run orchestration
(with eBay/email mocked out) are covered by a pytest suite that needs no
real credentials:

```bash
pip install -r requirements-dev.txt
pytest
```

This same suite runs automatically on every push via GitHub Actions
(`.github/workflows/tests.yml`, against Python 3.9 and 3.12) -- no
credentials needed there either, since everything external is mocked.

## Configuring

### Watchlist -- `config/watchlist.json`

```json
{
  "players": ["Michael Jordan", "Walter Payton", "..."]
}
```

Add or remove names freely, one per line in the `players` array. Matching
is case-insensitive and requires every word of the name to appear in the
listing title (e.g. "Walter Payton" requires both "walter" and "payton"
somewhere in the title) -- no code changes needed. The same list drives
both the eBay searches and the Craigslist quick-check links.

### Discount threshold -- `config/settings.json`

```json
"discount_threshold_pct": 30
```

Change `30` to whatever percent-under-comp-median you want to flag as a
deal. Lower = more (weaker) deals reported; higher = fewer, stronger ones.

### Other tunables in `config/settings.json`

| Key | What it does |
|---|---|
| `ebay.category_id` | eBay category to search within (defaults to `212`, "Sports Trading Cards"). eBay renumbers categories occasionally -- if results look off-topic, re-check the current ID via `python -m scripts.lookup_ebay_category`. |
| `ebay.sold_lookback_days` | How far back to pull sold comps (default 60 days). |
| `ebay.min_comps_required` | Minimum sold (or fallback) data points needed before trusting a median (default 3). Buckets below this are skipped entirely rather than flagged off a shaky number. |
| `craigslist.site` | Which Craigslist subdomain the quick-check links point at (default `chicago`). |
| `craigslist.category` | Craigslist search category for those links (default `sss`, all-for-sale). |
| `dedupe.prune_after_days` | How long a listing stays in the "already seen" file after its last flag before it's forgotten (default 120). |

## How dedupe works

`data/seen_listings.json` (gitignored -- it's local run state, not code)
tracks every eBay listing ID that's ever been emailed, along with the
price it was flagged at. A listing is included in the report again only
if:

- it's never been flagged before, **or**
- its price has dropped further since the last time it was flagged.

(Craigslist links aren't deduped -- they're static search URLs, not
individual listings, so the same links just appear in every email.)

If you ever want to re-see everything (e.g. after changing the threshold),
delete or edit `data/seen_listings.json`.

## Project layout

```
config/
  watchlist.json      -- editable player list
  settings.json        -- threshold + API/site tuning
src/
  config.py             -- loads .env + config JSON
  models.py              -- Listing dataclass
  matcher.py              -- title -> player, graded/raw detection
  ebay_client.py           -- eBay Browse API: OAuth, active search, sold comps
  ebay_email_alerts.py      -- eBay-via-IMAP: reads saved-search alert emails
  price_history.py           -- self-building comp history for the alerts path
  craigslist_links.py         -- builds quick-check search URLs (no scraping)
  comps.py                     -- median comp calculation
  dedupe.py                     -- seen-listings tracking
  report.py                      -- ranked report text + Craigslist links
  emailer.py                      -- Gmail SMTP send
  main.py                          -- orchestrates the daily run
data/
  seen_listings.json (gitignored) -- dedupe state
  ebay_alert_price_history.json (gitignored) -- self-built comp history (alerts path)
logs/
  scraper.log, cron.log (gitignored) -- run logs
scripts/
  install_cron.sh -- crontab installer helper
  lookup_ebay_category.py -- verifies the eBay category ID with real credentials
  test_email.py -- standalone Gmail send check (no eBay needed)
  test_ebay_alerts.py -- standalone check of the email-alerts path against your real inbox
tests/
  pytest suite covering matcher/comps/dedupe/report/craigslist_links/
  price_history/ebay_email_alerts + mocked full-run tests for both eBay paths
```

## Explicitly out of scope (v1)

- Facebook Marketplace scraping.
- Automated Craigslist scraping -- see "Why Craigslist isn't scraped"
  above; it's quick-check links instead.
- Fuzzy/ML-based title matching or comp modeling -- matching is
  keyword-based and comps are plain medians on purpose, so you can see
  exactly why something was (or wasn't) flagged.
- Sub-bucketing comps by exact grade (e.g. PSA 9 vs PSA 10 priced
  separately) -- grading is only split raw vs. graded for now.
