# CardPro — Card Deal Scraper

Daily scan of eBay for underpriced sports card listings on a watchlist,
emailed to you as a decision-first, sectioned report -- plus ready-to-click
Craigslist search links, since Craigslist can't be scraped automatically
(see below).

**Current status: eBay API access was declined; running on the
email-alerts path instead.** eBay's developer account application was
rejected outright (see "eBay account declined" below), so the eBay Browse
API path in this README's step 1 isn't usable. Instead, eBay data comes
from its own saved-search email alerts, read via IMAP -- see "eBay via
saved-search email alerts" below. Validated against real alert emails
(2026-08-18) and working. If the API appeal ever succeeds, the Browse API
path takes over automatically with no config changes needed -- it's
checked first.

**Contents**

- [What changed in 2.0, and why you'll see fewer deals](#what-changed-in-20-and-why-youll-see-fewer-deals)
- [What it does](#what-it-does)
- [The two emails](#the-two-emails)
- [How a deal is decided](#how-a-deal-is-decided)
- [Comps: what may and may not declare a deal](#comps-what-may-and-may-not-declare-a-deal)
- [Cheap, underpriced, flippable, collectible, target](#cheap-underpriced-flippable-collectible-target)
- [Focus: what reaches the email, and how long it is](#focus-what-reaches-the-email-and-how-long-it-is)
- [Known limitation: every comp is an asking price](#known-limitation-every-comp-is-an-asking-price)
- [eBay account declined](#ebay-account-declined)
- [eBay via saved-search email alerts](#ebay-via-saved-search-email-alerts)
- [Search coverage: why almost everything you see is a cheap raw card](#search-coverage-why-almost-everything-you-see-is-a-cheap-raw-card)
- [Why Craigslist isn't scraped](#why-craigslist-isnt-scraped)
- [Setup](#setup)
- [Running tests](#running-tests)
- [Checking the claims yourself: `replay_corpus`](#checking-the-claims-yourself-replay_corpus)
- [Sold comps worth adding, and why nothing flags today](#sold-comps-worth-adding-and-why-nothing-flags-today)
- [Configuring](#configuring)
- [Acquisition targets](#acquisition-targets)
- [How dedupe works](#how-dedupe-works)
- [Project layout](#project-layout)
- [The non-negotiables](#the-non-negotiables)
- [Explicitly out of scope](#explicitly-out-of-scope)

## What changed in 2.0, and why you'll see fewer deals

The most important thing to know before you read a report: **the deal count
dropped on purpose, and it dropped a long way.**

[`docs/CARDPRO_2_AUDIT.md`](docs/CARDPRO_2_AUDIT.md) audited the system
against its own production data (563 observations, 280 distinct listings)
and found the valuation engine wasn't valuing anything. **76% of all
valuations resolved to a comp bucket defined by price itself** -- the median
of everything priced $25-$100 is about $44, so anything priced $25-$31 was
automatically "30% under market". That is not a valuation, it is a
restatement of the price. Zero exact comps had ever existed in production.
All fifteen "deals" CardPro had ever flagged were artifacts of that
circularity, or of broader levels that pooled different parallels and
different grades together (one was a $1.25 base card reported as 95% under
market against a bucket containing refractors).

Replaying that exact same corpus through the 2.0 engine flags **zero**
opportunities where the old one flagged fifteen. You can re-run that
comparison yourself in about a second -- see
[`replay_corpus`](#checking-the-claims-yourself-replay_corpus).

So: some mornings the email will say it found nothing. That is the system
working. A report that says "I found nothing I can stand behind" is worth
more than one that says "95% under market" about a common.

## What it does

1. For each player on your watchlist, pulls **active** eBay listings --
   either via the Browse API, or via eBay's own saved-search email alerts
   if the API isn't available (see below for both).
2. Extracts a structured identity from each title -- year, season,
   manufacturer, set, parallel, card number, serial/print run, autograph,
   memorabilia, lot -- plus grading details (grader, grade, label
   qualifier, authenticity-only slabs) and negative signals (reprint,
   replica, custom, digital, facsimile auto, sealed product, break slot,
   pick-your-card, lot). Anything it can't read stays **unknown**; nothing
   is ever guessed.
3. Records every non-auction, non-blocked asking price into a self-building
   comp corpus -- **one row per listing**, kept at its first-seen date and
   its latest price -- then values each listing against comparable
   observations of *that card in that grade* -- see
   [How a deal is decided](#how-a-deal-is-decided).
4. Flags a fixed-price listing only if it clears **both** gates: 30%+
   (configurable) below its matched comp median, AND at least $3
   (configurable) in real dollar savings, measured against **total cost**
   (price + shipping + any tax you configure). Percent alone lets trivial
   deals through (50% off a $5 common is still just $2.50), so both
   together is what "worth your time" actually means.
5. Runs the resale arithmetic -- marketplace fees, outbound shipping,
   supplies, tax, a resale haircut -- and prints every assumption next to
   the number it produced.
6. Drops anything already emailed in a prior run, unless its price has
   dropped further.
7. Applies your **focus**: the email is built around the cheap end you
   actually buy and bid at (cards at or under `$40` by default, plus
   anything genuinely exceptional above it), auctions already bid past your
   max rational bid are dropped, and the whole thing is capped at a
   readable number of listings. Everything it leaves out is counted in the
   footer with the setting that would show it -- see
   [Focus](#focus-what-reaches-the-email-and-how-long-it-is).
8. Emails a **sectioned, decision-first report** -- sent as
   `multipart/alternative`, so a phone gets a laid-out HTML version and
   anything else (a terminal, a filter that strips markup, a client with
   images off) still gets the full plain-text one. Both are rendered from
   the same `report.ReportModel`, so they cannot describe a card
   differently; see [The two emails](#the-two-emails). Each section is
   omitted when empty:

   `ACT NOW` · `DEALS` · `TARGET CARD HITS` · `CHEAP AUCTIONS` ·
   `AUCTIONS ENDING SOON` · `COOL CARDS` · `CHEAP FINDS` · `YOUNG CORE` ·
   `OFFER OPPORTUNITIES` · `WATCH` · `LOW CONFIDENCE / NEEDS REVIEW` ·
   `PRICE DROPS`

   The order is what a reader is looking for, not how strong the valuation
   behind it is. That distinction matters here more than it would elsewhere:
   the first three sections need a comp CardPro will stand behind, and on
   this project's data that almost never exists -- so a report ordered only
   by valuation strength has a body made entirely of things it could not
   value. `COOL CARDS`, `CHEAP FINDS` and `YOUNG CORE` answer questions that
   *can* be answered from a title -- what is this card, is it cheap, is it
   one of my players -- and make no claim about price at all. No market
   value, no discount, no ROI, even when a comp exists.

   `CHEAP AUCTIONS` is the same idea for the bidding side, and it sits
   *above* `AUCTIONS ENDING SOON` on purpose. That section sorts by time
   left, which is right for a card that costs real money and wrong for a
   $2 one: a numbered rookie at $0.99 ends up below whatever slab happens
   to close an hour sooner, and is never seen. So auctions whose current
   bid plus shipping falls in your pocket-change band
   (`report.cheap_auction_floor`..`cheap_auction_ceiling`, $0.01--$10 by
   default) get their own section, ordered by how interesting the *card*
   is and only then by what ends soonest. It makes no deal claim -- a
   current bid is not a price and most of these have no comp at all.
   Anything in the band that *does* clear the deal gate is still claimed
   by `ACT NOW` or `DEALS` first; this section never takes a card away
   from a real valuation.

   followed by a **SYSTEM HEALTH** footer, the
   [sold comps worth adding](#sold-comps-worth-adding-and-why-nothing-flags-today),
   the Craigslist quick-check links, and suggested saved searches you don't
   appear to have yet.

   Within a section, entries are still ranked by **dollar amount saved**,
   not percent (a $250-off $999 card matters more than a 90%-off $10 one).
   Each headline entry answers the whole thesis: what the card is, total
   acquisition cost, estimated market value *with the comp level, sample
   size, asking-vs-sold basis, price range and recency behind it*, the
   discount, the resale economics and their assumptions, the confidence
   **and why it isn't higher**, the risks that would make it wrong, and the
   link. There is no blended 0-100 "deal score" and there never will be.

   Auctions get their own block that never calls a current bid a price, and
   shows the maximum bid that still preserves your margin instead.

   If nothing qualifies you still get a short "nothing today" email (with
   the Craigslist links and the health footer attached), not silence. If the
   run crashes outright (network blip, eBay issue, a state file that won't
   parse), you get a "Scan FAILED" email carrying the traceback instead of
   nothing at all -- same "not silence" principle applied to errors, not
   just the zero-deals case. The traceback travels in the email because the
   log file it used to point at lives on a runner GitHub deletes minutes
   later.

Every listing exits the pipeline with either a slot in the report or exactly
one recorded reason, counted in the footer (`src/reasons.py`). Nothing is
dropped silently -- 21% of listings used to vanish with no flag, no count
and no explanation.

## The two emails

The report goes out as `multipart/alternative`: a plain-text part and an
HTML part. Your mail client picks one -- in practice a phone shows the HTML
and a terminal shows the text.

They are not two reports. `report.build_model()` decides everything that is
a judgment -- which cards, in which sections, under which headline, with
which caveats -- and returns a `ReportModel`. `report.render_text()` and
`report_html.render()` each turn that same model into an email. A renderer
chooses typography and has no way to add, drop or reword a fact, which is
what makes "whichever half you read is the same report" true rather than
hopeful. A test asserts field-by-field that the HTML carries everything the
text does.

The plain-text part is never dropped. It is the version that still works
when the markup is stripped, when the client is a terminal, and when the
HTML renderer has a bug.

Three things about the HTML worth knowing:

- **Colour is a content rule, not decoration.** A saturated accent means
  CardPro is standing behind a number in that section. `ACT NOW`, `DEALS`,
  `TARGET CARD HITS` and `OFFER OPPORTUNITIES` get one. Everything that
  shows a card *without* valuing it -- `CHEAP AUCTIONS`, `AUCTIONS ENDING
  SOON`, `COOL CARDS`, `CHEAP FINDS`, `YOUNG CORE`, and the
  not-a-recommendation sections -- is grey. Painting a browse section like a
  deal would undo in CSS the separation the valuation engine enforces in
  code.
- **It follows your phone's dark mode**, and carries no images, no web
  fonts and no tracking pixels -- so nothing is blocked, nothing loads
  slowly, and nothing looks broken with remote content off (the Apple Mail
  default).
- **Every listing title is HTML-escaped.** They are written by strangers on
  eBay.

To see it before it lands in your inbox:

```bash
python -m scripts.rehearse_run --html /tmp/today.html   # then open it in a browser
```

## How a deal is decided

The whole path, in order. Every step can only ever *narrow* what qualifies.

1. **Identity extraction.** Title → player(s), year, set, parallel, card
   number, print run, auto/memorabilia, negative signals, and grading
   details. Team names, award names and league names are masked before
   parallel matching, and a parallel must come from a known vocabulary --
   `White Sox` used to become parallel `White`.
2. **Truncated grades are refused, not repaired.** eBay truncates long
   titles in alert emails, and `PSA 1…` parses as PSA 1 when it's really
   PSA 10. CardPro does **not** fetch the item page to resolve this: that
   would be automated access to eBay's site, which principle 6 below rules
   out for the same reason Craigslist is link-only. So a truncated title
   that produced a grade is refused *before* the comp lookup — it can't be
   valued, can't become a deal, and can't enter the comp corpus as a
   mislabelled grade. It appears under NEEDS REVIEW with "grade uncertain"
   as the stated reason. Raw cards are unaffected; there's no grade to get
   wrong.
3. **Market key.** Every card-level comp bucket is segmented by the market
   the card actually trades in: `raw`, or `graded + grader + grade +
   qualifier`. PSA 9 and BGS 9 are different markets. PSA 9 and PSA 10 are
   different markets. PSA 9 with an `OC` (off-centre) qualifier is a third.
   A slab whose grader or grade can't be read has **no known market** and is
   excluded from grade-segmented levels rather than pooled with every other
   slab.
4. **Comp level.** First level that applies *and* has enough surviving
   samples wins. A level only applies when the listing itself knows every
   field that level keys on -- missing means skip the level, never a guessed
   match. Only the top two may declare a deal; see the next section.
5. **Quality gates**, applied in this order:
   - **self-exclusion** -- a listing is removed from the comp set used to
     judge it, by listing id;
   - **outlier trim** -- MAD-based, default 3.5 scaled deviations, and only
     when there are at least 5 points (with 3 or 4, "outlier" is
     indistinguishable from "the market");
   - **recency weighting** -- weighted median with a 30-day half-life, so a
     month-old asking price counts half as much as today's;
   - **minimum sample** -- default 3 surviving points, or the level doesn't
     apply;
   - **staleness** -- if the newest comp in the bucket is more than 45 days
     old, the bucket may not declare a deal;
   - **dispersion** -- if MAD/median exceeds 0.5, the comps disagree too
     much to trust a median, and the bucket may not declare a deal.
6. **Confidence**, as a checklist rather than a model -- every step is a
   sentence the report prints. It starts at the level's own confidence
   (`exact` = high, `same_card` = medium, everything else = low) and steps
   down once for each of: asking-price basis, fewer than 5 comps, stale
   comps, wide dispersion. **Because 100% of the corpus is asking prices
   today, "medium" is this project's honest ceiling -- an asking-basis comp
   can never reach "high".**
7. **Economics.** Total acquisition cost (price + shipping + tax) versus
   expected net proceeds after marketplace fees, outbound shipping, supplies
   and a resale haircut. Unknown shipping never becomes $0; it becomes
   "shipping unknown -- actual cost may be higher".
8. **The two-gate deal test, on TOTAL cost.** `pct_under_market >=
   discount_threshold_pct` **AND** `dollar_savings >= min_savings_dollars`.
   Both, or it isn't a deal. Auctions never reach this test at all -- a
   current bid is not a price, so they route to their own section with a max
   rational bid instead.
9. **Dedupe.** Already-reported opportunities are suppressed unless the
   price has dropped further. Auctions, target hits and needs-review entries
   pass through undeduped -- hiding a still-live auction is the opposite of
   useful.
10. **Focus.** Last, and the only step that is about *you* rather than
    about the card: the email is built around the cheap end you actually
    buy and bid at, and it is capped in length. A card above the ceiling
    has to be exceptional to take a slot, an auction already bid past your
    max rational bid is dropped, and what is left is trimmed to a readable
    number of listings. Nothing here changes a valuation or a verdict --
    everything it removes is counted in the report footer, with the setting
    that would bring it back. See
    [Focus](#focus-what-reaches-the-email-and-how-long-it-is).

## Comps: what may and may not declare a deal

CardPro 1.0 bucketed comps by `(player, card_type, price_tier)` and defended
it as noise reduction. It was, in fact, circular: **the bucket is defined by
price, so the cheap end of every bucket automatically reads as "under
market."** There is no card in existence that this can't flag if it happens
to be priced below its neighbours. It produced 76% of all valuations in
production and every single false positive. It can no longer declare a deal.

The current ladder, narrowest first:

| Level | Matches on | May flag a deal? |
|---|---|---|
| `exact` | player + year + set + parallel + card # + market | **Yes** (base confidence: high) |
| `same_card` | player + year + set + parallel + market | **Yes** (base confidence: medium) |
| `same_set` | player + year + set + market (parallel unknown) | No -- context only |
| `price_tier` | player + market + price bracket | No -- circular by construction |

`market` is `raw`, or `graded + grader + grade + qualifier`.

**Why `exact` and `same_card` may flag:** both require a *known* parallel, so
"unknown parallel" never matches "unknown parallel", and both are segmented
by the full market key, so a PSA 8 is never compared to a PSA 10.

**Why `price_tier` was kept at all:** as context, and only as context. "Cards
of this player, in this grade, around this price" is genuinely useful colour
when you're eyeballing a listing that has no identity-matched comp -- it tells
you roughly what neighbourhood you're in. What it cannot do is tell you
anything about *price*, because price is what defines the bucket. It is
structurally incapable of triggering a purchase: the level is marked
`flag_eligible=False` in `comps.LEVEL_SPECS` and the deal gate is never
reached from it. Note that it still keys on the full market, not just
raw-vs-graded, for the same reason everything else does -- a PSA 9 must never
be shown the PSA 10 median, not even as colour.

**What was deleted rather than demoted:** the old grade-blind `family` level
(`player + year + set`). `same_set` already covers "same year and set,
parallel unknown" while keeping the market segmentation, so the only thing
`family` added was the ability to pool different grades. Labelling a number
"context only" does not make it safe when it's wrong by 4x, so the level is
gone. A card with no market-matched comps gets **no number at all** and is
reported as unvalued.

Comps are computed once per run. Real sold data is used the moment it exists
(the eBay Marketplace Insights path is built and dormant); until then, every
comp is an asking price -- see below.

## Cheap, underpriced, flippable, collectible, target

CardPro keeps five different questions apart and never blends them into a
single score:

| Question | What it means |
|---|---|
| **Cheap** | The asking price is objectively low. Says nothing about value. On an auction, "cheap" is a current bid, which is a floor under the cost rather than the cost -- see `CHEAP AUCTIONS`. |
| **Underpriced** | Materially below what comparable copies of *that exact card in that exact grade* go for. Requires an identity-and-grade-matched comp; a price-bracket estimate can never establish this. |
| **Flippable** | Enough spread to resell at a worthwhile profit after fees, shipping, supplies and a resale haircut. Shown with its assumptions attached. |
| **Collectible opportunity** | Underpriced *and* carrying attributes you care about (rookie, auto, numbered, patch, young core). Tagged separately, never folded into the price maths. |
| **Target acquisition** | A specific card you told CardPro to find below a price you set. A target hit is **not** a claim that the card is underpriced -- different answers, different sections. See [Acquisition targets](#acquisition-targets). |

### Value vs. potential tags

The two attribute tags sit alongside those five answers and are displayed,
never blended into the ranking math:

- **`[YOUNG CORE]`** -- the player is tagged `young_core` in
  `config/watchlist.json`'s `player_tiers`, meaning you're betting on their
  long-term growth, not just today's price. Untagged players default to
  `legend` (established value, no tag shown).
- **`[ROOKIE CARD]`** -- the title matched "RC" or "Rookie"
  (`matcher.detect_rookie_card`), the same simple keyword-match style as
  grading detection.

Entries are still ranked within a section by dollar saved; you apply your own
judgment about how much a `[YOUNG CORE] [ROOKIE CARD]` tag should move
something up your list. Edit `player_tiers` any time your read on a player
changes.

## Focus: what reaches the email, and how long it is

Finding is not the same job as reporting, and CardPro used to conflate
them: everything that survived the pipeline got printed, at full length, in
whatever quantity the morning produced. Replaying a real August corpus
through the report produced a **4,255-line email**. The information was
right and the document was unreadable, which in practice means the
information was not delivered.

`settings.json` -> `focus` is the editorial layer. It removes; it never
promotes, never re-values anything, and never changes a verdict.

**The price ceiling.** You buy at the cheap end and bid to win, so the
email is built around cards at or under `price_ceiling` (shipping
included -- the ceiling is what leaves your account, not what the listing
advertises). A dearer card is not being called a bad card; it is being
called not-what-you-shop-for. To take a slot from the cheap ones it has to
be exceptional: `exceptional_min_discount_pct` **and**
`exceptional_min_savings_dollars`, **and** a flag-eligible comp. All three,
because each alone has a known failure mode -- percent alone flags a $900
card off a price-bracket bucket, dollars alone flags every expensive card
with a mild discount, and without the comp gate the whole exception runs
off valuations the engine itself refuses to declare deals from.

**Targets are exempt.** A card on your target list is one you named, at a
price you set. A second price opinion from `focus` would be your config
arguing with itself.

**Bidding room.** An auction whose current bid already sits above your max
rational bid is not a card you can win at a price that works. It is
dropped, and counted, with its own sentence in the footer. An auction with
no market value has no rational ceiling to be past, so it stays -- "we
could not judge this" must never look the same as "we judged it and it
failed".

**Length.** `max_listings` caps the email. Slots are handed out in two
passes over a priority order that puts auctions above the fixed-price
also-rans: first every section takes up to `max_per_section`, then the
leftovers go to whoever still has cards. The first pass is the point -- a
straight top-down cap means a 40-opportunity morning prints zero auctions,
and "the day was so good you saw none of the thing you bid on" is a bug
wearing a ranking's clothes. `LOW CONFIDENCE / NEEDS REVIEW` never takes
leftovers: it says of itself that it is not a recommendation, and on a
quiet day it is also the biggest section CardPro produces.

**Nothing is dropped silently.** Every group removed is counted in the
thresholds footer, with the setting that would bring it back:

```
107 listings above your $40.00 focus ceiling left out -- none was 50%+ off
with $100.00+ saved off a comp CardPro will stand behind. Raise
focus.price_ceiling to see them. 172 listings matched your focus but were
trimmed for length -- at most 40 listings print, and at most 10 from any
one section.
```

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `enabled` | Master switch. `false` restores the old everything-every-day report. | `true` | Turn it off for a day when you want the raw firehose. |
| `price_ceiling` | Total cost (price + known shipping) at or under which a card is email material. | `40.0` | Raise it as your budget per card rises. Move `alerts.immediate_alert_min_savings_dollars` with it -- see below. |
| `exceptional_min_discount_pct` | Discount a dearer card needs to get in anyway. | `50` | Lower it if you're happy seeing more expensive cards; raise it to make the exception near-impossible. |
| `exceptional_min_savings_dollars` | Dollars a dearer card must save to get in anyway. **Both** this and the percentage, plus a flag-eligible comp. | `100` | Same reasoning. |
| `require_auction_bidding_room` | Drop auctions already bid past your max rational bid. | `true` | Set `false` if you want to watch auctions you can't profitably win. |
| `max_listings` | Hard cap on distinct listings in one email. | `40` | 25 for a phone-sized digest, 60+ if you read it at a desk. |
| `max_per_section` | Each section's first-pass share, and the hard cap for `NEEDS REVIEW`. | `10` | Raise it if one section keeps getting cut short; `0` disables the per-section pass entirely (pure top-down). |

On that same August corpus, this takes the email from **4,255 lines to
215** without changing a single valuation.

## Known limitation: every comp is an asking price

This is the largest single limitation in the system, and it is structural.

eBay's Browse API only returns **active** listings. Real historical sold
prices come from eBay's **Marketplace Insights API**, which is a
limited-release API -- a standard free developer account is not granted
access to it, and eBay's own documentation describes it as restricted and not
open to new users. The saved-search email path has no sold feed at all.

So **100% of CardPro's comp corpus today is asking prices**, accumulated from
what it observes in alert emails
(`data/ebay_alert_price_history.json`, pruned after 180 days by default). An
asking price is the seller's opinion; a sold price is the market's. Worse,
alert emails are biased toward *brand-new* listings, which skew high (sellers
start high and reduce) and skew toward whatever is being freshly dumped.

The engine treats this as a first-class fact rather than a footnote:

- **An asking-basis comp can never reach "high" confidence.** It is
  downgraded one full step automatically, so "medium" is the ceiling until
  real sold data exists. `basis` is only `sold` when *every* surviving point
  in the bucket is a sold price.
- Every headline entry in the report prints the basis on its Market line
  ("asking comps" vs "sold comps"), and the Confidence line leads with the
  asking-vs-sold caveat whenever it applies.

How fast comps become usable depends on volume, not calendar time: a bucket
needs `valuation.min_comps_required` observations (default 3) before it can
be used at all, and every matched listing in a run counts toward that -- so a
high-volume player can clear the bar quickly, while a thin one may never.
Note that with identity-and-grade-matched buckets this is a much higher bar
than it was in 1.0, which is exactly the point.

The highest-value next step is entering real sold comps by hand (130point is
free and includes accepted Best Offers; Card Ladder is the paid option). That
path is built -- `src/sold_comps.py` loads them, `python -m
scripts.add_sold_comp` records one, and the report's footer ranks which
lookups would unlock the most listings -- but nothing has been entered yet,
so the sentence above still holds in full. See
[Sold comps worth adding](#sold-comps-worth-adding-and-why-nothing-flags-today)
for how to pick which ones, and
[`docs/CARDPRO_2_AUDIT.md`](docs/CARDPRO_2_AUDIT.md) §6 for the full
data-source matrix and cost analysis.

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
asking specifically what triggered it. Browse access alone would be a large
upgrade even with no sold data -- structured item specifics (set, parallel,
grade as real fields instead of title guesses), real auction flags, bid
counts, end times and seller data. If that gets resolved and you get real
keys, nothing else needs to change -- see the status note at the top of this
README.

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
feeds them into the same matching/valuation/report pipeline as the API
path. Nothing about eBay's own systems is touched or automated against --
eBay decides what to send and when; this only processes mail you already
receive.

**Validated (2026-08-18)** against 14 real alert emails covering the full
watchlist: 327 listings extracted, 96 correctly matched to watchlist
players, zero observed player-name collisions (e.g. Caleb Williams vs.
Caleb Wilson stayed correctly separated).

**When the mailbox itself misbehaves:** an IMAP SEARCH error raises rather
than being reported as an empty inbox, individual messages that fail to fetch
are counted and named in the health footer, and "alert emails arrived, zero
listings came out" raises the template-change alarm in the footer too. See
[When something goes wrong](#when-something-goes-wrong).

**What is NOT validated:** listing-type detection (auction vs. Buy It Now),
bid counts, time-left text, shipping-cost extraction, and Best Offer
detection are all pulled best-effort out of the alert-email HTML and have
**not** been checked against real emails. They fail safe rather than
guessing: no evidence either way yields `unknown`, never a default of "fixed
price", and unknown shipping is never treated as $0. The report says
"unknown" out loud in both cases, and an `unknown` listing type is not
eligible for the auction math. If you want to know how these behave on your
actual mail, run `python -m scripts.test_ebay_alerts --raw` and read the
output.

**Truncated-title grades:** eBay truncates long titles in these emails, which
can cut a grade number mid-digit (a "PSA 10" showing as "PSA 1…"). Since the
grade is part of the comp key, a wrong grade is a wrong valuation — and worse
than a missing one, because it looks confident.

CardPro used to fetch the real title from eBay's item page to fix this. It no
longer does, and the reason is worth being explicit about: that request went
out behind a spoofed Chrome `User-Agent`, whose only purpose is to make an
automated request look like a person in a browser. That is exactly the
behaviour principle 6 rules out, and the same repo refuses to scrape
Craigslist on those grounds. eBay's User Agreement prohibits automated access
to the site. The fetch is gone; no code under `src/` sends a `User-Agent`
header, and a test asserts it stays that way.

What happens instead: a listing whose title is truncated **and** which parsed
a grade is refused before valuation. It gets no market value, never becomes
an opportunity, never enters the comp corpus, and shows up under NEEDS REVIEW
with the grade-uncertain reason stated. You still see the card and the link —
you just don't see a number derived from a grade nobody could verify.

**One real tradeoff worth knowing:** eBay's saved-search alerts only cover
*newly listed* items, not their full standing inventory, and there's no
sold-comps feed on this path -- see
[Known limitation](#known-limitation-every-comp-is-an-asking-price) above and
[Search coverage](#search-coverage-why-almost-everything-you-see-is-a-cheap-raw-card)
below.

### Setting up the saved searches

1. On eBay's site (not in this repo): for each watchlist player, search
   for their cards, then use eBay's "Save this search" option and turn on
   email alerts for it. Repeat for every player you want covered. Then read
   [Search coverage](#search-coverage-why-almost-everything-you-see-is-a-cheap-raw-card)
   -- one search per player is not enough, and the daily report tells you
   which extra ones to create.
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

| Key | What it does | Default |
|---|---|---|
| `enabled` | Turns this path on. Only takes effect when `EBAY_CLIENT_ID`/`SECRET` are absent (the Browse API path always wins if both are configured). | `true` |
| `sender_contains` | IMAP filters alert emails to ones whose From address contains this. If real alerts come from an address that doesn't match, alerts won't be found -- `test_ebay_alerts.py` will tell you if that's happening. | `"ebay.com"` |
| `lookback_days` | How many days back to search each run. eBay sends these roughly daily; 2 gives one day of slack. | `2` |
| `mailbox` | IMAP folder searched -- All Mail, not `INBOX`, see "Keeping these emails out of your inbox" below. Only change this if your Gmail account's UI language gives this folder a different IMAP name. | `"[Gmail]/All Mail"` |
| `price_history_path` | Where the self-building comp corpus is stored. Tracked in git, not ignored -- see "Running on a schedule". | `"data/ebay_alert_price_history.json"` |
| `price_history_max_age_days` | How long an observed price stays in the corpus before aging out. Longer = deeper buckets but staler comps; the staleness gate is a separate control. | `180` |

### Keeping these emails out of your inbox

Since the scraper searches Gmail's **All Mail**, not just Inbox (see the
`mailbox` setting above), you can set up a Gmail filter that routes eBay's
alert emails straight out of your inbox without breaking anything:

1. In Gmail, click the search bar's filter icon (or Settings -> Filters
   and Blocked Addresses -> Create a new filter).
2. Set **From** to `ebay.com` (or narrower, matching whatever
   `sender_contains` is set to), then **Create filter**.
3. Check **Skip the Inbox (Archive it)**. Optionally also check **Apply
   the label** and create a label like `CardPro Alerts` so they're still
   easy to find/browse by hand if you ever want to.
4. Do **not** check **Delete it** -- that's the one action that actually
   removes a message from All Mail (via Trash, purged after 30 days) and
   would make it invisible here too.

With this filter in place, alert emails never touch your inbox at all,
and the scraper still sees every one of them via All Mail. You can also
freely archive or label-organize emails by hand after the fact for the
same reason -- only genuine deletion breaks it.

## Search coverage: why almost everything you see is a cheap raw card

Measured against the stored corpus on 2026-08-26 (907 distinct listings):
**98.8% of everything CardPro has observed was a raw card -- 11 graded
listings in total -- 48% of it was under $25, and the median observation was
$29.99.** The graded market -- the liquid, high-value half of
the hobby, and the only half where comps are dense enough to value anything
precisely -- is effectively invisible.

That is a direct consequence of having **one saved search per player**.
eBay's alert digest returns whatever is *newest* for a query, and what is
newest for a bare player name is overwhelmingly cheap raw filler. More
listings won't fix it, because the problem isn't the number of listings, it's
which ones. The fix is several narrow queries per player, each pulling a
different slice of the market.

`src/search_terms.py` generates those queries, and the daily report prints
the ones it sees no evidence of coverage for, each with the reason it exists:

| Slice | Why |
|---|---|
| `<player> PSA 10` | Graded top-pop market -- densest comps, and ~99% missing from today's data (1.2% of observations have a grader at all) |
| `<player> PSA` | All graded copies, any grade -- a separate market from raw |
| `<player> BGS SGC` | The other graders; their markets price differently from PSA |
| `<player> rookie` | Rookie cards, where most of the money and liquidity is |
| `<player> auto` | Autographs -- a different market from the base card |
| `<player> /99` | Serial-numbered parallels; the print run is what makes them scarce |
| `<player> refractor prizm` | Premium parallels from the two dominant modern lines |

Plus sport-appropriate set slices (Prizm / Optic / Select / Contenders /
Mosaic for football, Topps Chrome / Bowman Chrome / Heritage / Stadium Club
for baseball, Young Guns / SP Authentic for hockey, and so on), and one
precise query per acquisition target.

**CardPro only generates the query strings. It never creates a saved search
for you** -- that would mean automating against eBay's own UI, which this
project doesn't do. Creating one is a two-minute copy/paste job on eBay's
site: search the suggested string, hit "Save this search", turn on email
alerts. The report exists to make that a copy/paste rather than a research
project.

Two things the generator deliberately does *not* produce: misspelling
permutations (they multiply query count fast, eBay's own search already fuzzy
matches most of them, and the recall gain is speculative) and every parallel
name (parallel-level precision belongs on an acquisition target, where you've
said you actually want that specific card).

The coverage check is a heuristic and the report words it as one. eBay's
alert emails don't reliably say which saved search produced a listing, so it
can only say "there's no evidence of coverage here", never "you definitely
have no such search."

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
pip install -r requirements.txt   # pinned with upper bounds -- see below

cp .env.example .env
# then edit .env and fill in:
#   GMAIL_ADDRESS, GMAIL_APP_PASSWORD  (required)
#   EMAIL_TO (defaults to GMAIL_ADDRESS if left out)
#   EBAY_CLIENT_ID, EBAY_CLIENT_SECRET (optional -- leave blank/placeholder if you don't have them yet)
```

Dependencies carry upper bounds (`requests<3`, `python-dotenv<2`,
`beautifulsoup4<5`). The daily workflow installs them fresh on every run, so
an unbounded constraint would put a parser's next major release straight into
production with nothing in front of it -- and the symptom would be zero
listings extracted, which looks exactly like a quiet day. Bump them on
purpose, with the test suite as the gate.

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
**not** touch the dedupe file or the comp corpus -- safe to run repeatedly
while testing. Check `logs/scraper.log` for details if something looks off.

Once a dry run looks right, run it for real:

```bash
python -m src.main
```

If the report says nothing qualified, read the SYSTEM HEALTH footer before
assuming something broke -- it counts what was seen, what was valued, what
could be valued at a flag-eligible level, and the reason every unreported
listing was set aside. A run where no comp bucket anywhere is strong enough
to declare a deal from says so explicitly, and points you at the search
coverage section.

### 6. Running on a schedule

**Recommended: GitHub Actions (`.github/workflows/daily-scan.yml`).**
A cron job on a laptop only runs if the laptop happens to be on, awake, and
unlocked at that exact minute -- closing the lid or losing power silently
skips a day, with nothing to tell you it didn't run. Running the scan in
GitHub's cloud instead removes that failure mode entirely: it fires on
schedule regardless of your Mac's state.

One-time setup:

1. On GitHub: repo **Settings -> Secrets and variables -> Actions -> New
   repository secret**, and add these (same names/values as your local
   `.env`): `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`, and
   `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` if you have eBay API access.
   Never paste these into a chat with an AI assistant -- only into GitHub's
   secret form, which is write-only (nobody, including repo admins, can
   read a secret's value back afterward).
2. That's it -- the workflow is already in the repo. It runs daily at
   13:00 UTC (~8am US Central; drifts to ~7am during winter/CST since
   GitHub Actions cron doesn't follow DST -- not worth a second seasonal
   schedule to fix a one-hour drift).
3. To trigger a run immediately instead of waiting for the schedule: repo
   **Actions tab -> Daily Card Deal Scan -> Run workflow**.

What the job does, in order: installs `requirements-dev.txt`, **runs the
test suite**, runs the scan, then persists state. The test step is a gate,
not a formality -- the scan reads your inbox and emails you conclusions, and
running it on code that doesn't pass its own tests produces a report that
looks exactly like a good one and isn't. Two seconds of pytest against that
is a trade worth making, and a failure there means no report today and a red
job you get told about.

The job also has a `concurrency` group and a 15-minute timeout. The group
stops a manual dispatch racing the 13:00 cron run: two jobs checked out at
the same commit means the second push is rejected, and by then the email has
gone out and the day's observations exist only on a runner about to be
deleted. `cancel-in-progress` stays `false` -- killing a run mid-flight is
the one thing worse than making it wait. The timeout catches a stalled
socket that somehow escapes the client-side timeouts, so it ends as a failed
job you hear about rather than six hours of silence.

The runner is thrown away after each run, so the workflow commits
`data/seen_listings.json` and `data/ebay_alert_price_history.json` (dedupe
state and the self-building comp corpus) back to the repo as its last
step -- otherwise both would reset to empty every single day. That step
fails loudly rather than quietly: `git add` errors are no longer swallowed
(they used to leave the step printing "No state changes" and the job green
having persisted nothing), and the push rebases and retries up to four times,
because a rejected push means the day's observations are gone for good --
past the IMAP lookback window there is nothing to re-read. Neither file is
gitignored, on purpose. That means this data becomes part of the repo's
git history going forward; it's just listing prices/dates/ids, nothing
sensitive, but worth knowing if this repo is or becomes public.

If a scheduled run fails (e.g. secrets not set yet), check the **Actions**
tab for the red X and the step's log -- same "send something, never fail
silently" principle as the failure-notification email.

**Alternative: local cron on your Mac**, if you'd rather not put Gmail
credentials in GitHub secrets and are fine with the "only runs if the Mac
is awake" tradeoff:

```bash
bash scripts/install_cron.sh        # defaults to 8:00am daily
bash scripts/install_cron.sh 7 30   # or specify HOUR MINUTE, e.g. 7:30am
```

This adds (or replaces) a single crontab entry that runs the scraper daily
using this project's virtualenv if present, and appends output to
`logs/cron.log`. Verify with `crontab -l`. Remove it with
`bash scripts/uninstall_cron.sh` (e.g. once you've switched to GitHub
Actions, to avoid getting two emails some mornings).

**Note on macOS + cron:** if `logs/cron.log` stays empty and nothing runs,
your Mac's cron may need Full Disk Access under System Settings > Privacy
& Security (this varies by macOS version) -- grant it to `/usr/sbin/cron`.

**Note on logs:** `logs/scraper.log` (the script's own detailed log) is
capped at ~2MB with 5 rotated backups, so it won't grow unbounded over
months of daily runs. `.gitignore` covers the rotated files as well as the
live one (`logs/*.log*`) -- `scraper.log.1` through `.5` carry every title,
every price and your email address, and the old `logs/*.log` pattern missed
all five of them. `logs/cron.log` (cron's redirected stdout/stderr,
just a few lines a day) isn't rotated -- it's tiny enough not to matter,
but nothing stops you from truncating it (`> logs/cron.log`) whenever you
feel like it.

### When something goes wrong

The rule for all of these is the same: a failure must not be able to look
like a quiet day.

- **A state file that won't parse raises.** `data/ebay_alert_price_history.json`
  and `data/seen_listings.json` used to fall back to "start fresh" with a
  warning saying the old file was left in place. That was true for about
  thirty seconds -- until the save at the end of the run replaced it with the
  day's observations and the workflow committed the wipe, losing the corpus
  with the only evidence a log line on a deleted runner. Both now raise
  (`price_history.CorruptCorpus`, `dedupe.CorruptSeenListings`), so the run
  aborts, you get the traceback by email, and there is nothing staged to
  commit. Repair or move the file and re-run.
- **An empty corpus never overwrites a full one.** Nothing in the pipeline
  legitimately empties the corpus, so `price_history.save()` refuses that
  write. A *shrink* still goes through -- that is what pruning looks like,
  and second-guessing it here would mean writing the retention policy down
  twice.
- **IMAP and SMTP have socket timeouts** (60s and 30s). Without them a server
  that accepts the connection and then stalls raises nothing at all, so the
  failure-email handler never fires -- the one path in this system that
  produced no report *and* no failure email.
- **A mailbox that can't be read is not an empty mailbox.** An IMAP SEARCH
  error raises instead of rendering as "Emails scanned: 0, Listings parsed:
  0" directly above prose telling you those counts are proof it looked. A
  single message that fails to fetch doesn't lose the run, but it is counted
  and reported in the health footer rather than skipped in silence.
- **The template canary reaches you.** If alert emails arrive and zero
  listings come out of them, eBay almost certainly changed their email
  template and every number under it is fabricated. That warning is now a
  `!!` line in the SYSTEM HEALTH footer, telling you to run
  `python -m scripts.test_ebay_alerts --raw`. It used to go only to the log
  file, on a runner that no longer exists by the time you read the email.

## Running tests

The identity/comps/economics/targets/reasons/dedupe/report logic and the full
daily-run orchestration (with eBay/email mocked out) are covered by a pytest
suite that needs no real credentials:

```bash
pip install -r requirements-dev.txt
pytest
```

This same suite runs automatically on every push via GitHub Actions
(`.github/workflows/tests.yml`, against Python 3.9 and 3.12) -- no
credentials needed there either, since everything external is mocked -- and
it gates the daily scan itself, which will not send an email if the tests
don't pass. Every failure mode in the audit has a regression test; every
real bug becomes one.

## Checking the claims yourself: `replay_corpus`

```bash
python -m scripts.replay_corpus
python -m scripts.replay_corpus --legacy        # also show what the v1 engine did
python -m scripts.replay_corpus --min-comps 5   # try a stricter sample gate
python -m scripts.replay_corpus --reextract     # re-parse the stored titles with today's code
```

This replays every observation in `data/ebay_alert_price_history.json` as
though it were a live listing, values it against the rest of the corpus
(excluding itself, exactly as the real pipeline does), and prints the
before/after: which comp levels were used, what confidence they landed at,
which quality gates fired, and what each engine would have flagged. With
`--legacy` it runs the v1 hierarchical/price-tier engine over the same data
first, so the two sit side by side.

It also prints the **identity-coverage KPI**, which is the part that says
*why* the answer is what it is rather than only what it is -- see
[Sold comps worth adding](#sold-comps-worth-adding-and-why-nothing-flags-today)
below for the reading of it.

**This is how every number in
[What changed in 2.0](#what-changed-in-20-and-why-youll-see-fewer-deals) was
measured, and it's how you re-check them.** It hits no network, sends no
email, and writes nothing.

Be honest about what it is: the corpus is asking prices, only as deep as the
alert emails that produced it. This measures whether the *engine* behaves
correctly, not whether the market values are right. No data source available
to this project can tell us that yet.

## Sold comps worth adding, and why nothing flags today

The engine flags nothing on the current corpus. Three different things could
cause that -- the gates are too strict, the identities never resolve, or the
resolved ones are too lonely to form a bucket -- and until August 26 nothing
in the project told you which. Two pieces of output now do.

### The identity KPI (`replay_corpus`)

`python -m scripts.replay_corpus` prints this against
`data/ebay_alert_price_history.json`. Real output, 907 distinct listings
observed 2026-08-21 to 2026-08-26, all of them asking prices:

```
--- identity coverage (KPI: CARDPRO_2_AUDIT.md section 8) ---
  observations: 907
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
numbers move as it improves and the corpus refills. Re-run the command rather
than trusting this paste.

Read it as two questions kept deliberately apart:

1. **Can a flag-eligible key be built at all?** For 99.2% of the corpus, no.
   The `first blocker` list is the useful half: each blocked listing is
   attributed to the *narrowest single field* that stops it, not to every
   field it happens to be missing. That is what makes it a work queue rather
   than a tally -- a listing missing both set and parallel is one problem,
   and fixing parallel extraction alone would move none of the 600 listings
   that never got as far as a set name.
2. **Does a built key land somewhere useful?** No: of the 7 observations with
   a complete `same_card` key, none is in a bucket holding more than
   `valuation.min_comps_required` other listings. A complete key nothing else
   shares values nothing. So even perfect extraction on those 7 changes
   today's report by zero listings.

The engine block underneath says the same thing from the other end: 0 `exact`
buckets, 0 `same_card`, 8 `same_set`, 74 `price_tier`, every valuation at
`low` confidence, and the sample-span gate (`concentrated_sample`) firing on
all 866 of them -- unavoidable while the corpus spans five days and
`valuation.min_comp_span_days` is 7.

**None of that is a threshold that wants relaxing.** The work is title
parsing, set name first, and then time.

### The sold-comp suggestions (report footer)

Every price CardPro has is an asking price, which is why no comp reaches
"high" confidence
([Known limitation](#known-limitation-every-comp-is-an-asking-price)). The
one fix that needs no paid data and no scraping is you looking a card up on
130point and typing the number in -- and since that tops out around twenty
entries, those twenty have to be the right ones. Left to pick by hand you
would enter comps for whatever card caught your eye, which is uncorrelated
with what the engine is stuck on.

`src/comp_requests.py` picks instead. It ranks the card identities the most
of today's listings are waiting on, drops the ones that already have enough
sales on file, and hands back a query to paste into 130point. The report's
footer prints the top five with the command that records the answer.

Next to it, it prints the number of listings that could not be identified
precisely enough for *any* sold comp to match. That is the honest
counterweight: when it dwarfs the suggestions, data entry is not the
bottleneck. Replaying the stored corpus as listings, that is exactly what it
says today:

```
Sold comps worth adding (130point.com -> python -m scripts.add_sold_comp):
  (900 listing(s) could not be identified precisely enough for any sold comp to match -- those need better title parsing, not more data.)
```

900 of 907. No suggestion clears the two-listing minimum, because the seven
identities that resolved are seven *different* cards. Forcing one line per
resolved identity shows what a suggestion looks like when the extractor is
doing better:

```
  Kyle Teel (2026 Topps Chrome Red White Blue raw) -- 1 listing waiting, 3 sales still needed
      search: "2026 Topps Chrome Kyle Teel Red White Blue"
      example: https://www.ebay.com/itm/178427169565
      add:    python -m scripts.add_sold_comp --player "Kyle Teel" --year 2026 --set "Topps Chrome" --parallel "Red White Blue" --price ? --date ?
```

The `add:` line is the command with everything CardPro already knows filled
in, leaving the two fields only the lookup can produce. Paste it, replace the
two question marks, run it.

If you would rather not think about flags at all, paste the listing title
instead and let the same parser the daily scan uses read the identity out of
it:

```bash
python -m scripts.add_sold_comp \
    --from-title "2026 Topps Chrome Kyle Teel Red White Blue Refractor #RA-KT" \
    --price 34.00 --date 2026-08-20
```

It prints what it read before it writes anything, and any flag you pass
explicitly wins over what it read -- you looked at the card, the parser only
looked at the title.

The typing saved is not really the point. A comp entered either of these ways
is keyed **exactly** the way the listings it will be matched against are
keyed. A hand-typed `--parallel Silver` against an extracted `Silver Prizm`
is a sold comp that silently never matches anything, which looks like
progress and is worse than not entering it at all.

Entries land in `config/sold_comps.json` (`settings.json` -> `sold_comps.path`),
are validated before writing rather than repaired, and are the only comps in
the system carrying `basis: sold`. Three caveats stated up front, because
they are in the module docstrings too: "would unlock" means "would give these
listings a comp level allowed to declare a deal", not "would find a deal";
ranking by listings-per-lookup optimises your effort, not your portfolio, so
a lookup for a card you actually want beats five for cards you don't; and a
*wrong* entry is worse than no entry, because the engine trusts sold data
more than anything else it has.

## Configuring

### Watchlist -- `config/watchlist.json`

```json
{
  "players": ["Michael Jordan", "Walter Payton", "..."]
}
```

Add or remove names freely, one per entry in the `players` array. Matching
is case-insensitive and requires every word of the name to appear in the
listing title (e.g. "Walter Payton" requires both "walter" and "payton"
somewhere in the title) -- no code changes needed. The same list drives
the eBay searches, the suggested saved searches, and the Craigslist
quick-check links. A title matching two watchlist players is treated as a
multi-player card and not valued: a dual auto is a different market from
either player's single card, and no comp bucket represents it.

Optionally tag any player as `young_core` in the same file's
`player_tiers` object to get the `[YOUNG CORE]` report tag -- players left
out of `player_tiers` default to `legend`:

```json
"player_tiers": {
  "Caleb Wilson": "young_core"
}
```

`player_tiers` is purely a display tag. It does not affect matching, comps,
or flagging math at all.

The third key, `target_cards`, is [documented below](#acquisition-targets).

### The deal gate -- `config/settings.json`

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `discount_threshold_pct` | Percent under the matched comp median a listing must be, measured on **total cost**. | `30` | Lower for more (weaker) deals, higher for fewer (stronger) ones. |
| `min_savings_dollars` | Real dollars that must be saved, regardless of percent. | `3` | Raise it if the report feels cluttered with low-value listings; lower it (or `0`) to see everything that clears the percent gate. |

A listing must clear **both**. `min_savings_dollars` is the knob that keeps
trivial deals (50% off a $5 common) out of the report even when the percent
alone would qualify.

### Valuation quality gates -- `settings.json` → `valuation`

These are the difference between a real valuation and a number that just
restates the price. See [How a deal is decided](#how-a-deal-is-decided) for
where each one applies.

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `min_comps_required` | How many comparable observations a bucket needs before it may be used at all. | `3` | Raise to 5+ once the corpus is deep enough -- fewer, better-supported valuations. Lowering below 3 is not advisable; a median of two asking prices isn't a market. |
| `half_life_days` | Recency weighting: a comp this many days old counts half as much as one from today. | `30` | Shorten in a fast-moving market, lengthen for vintage where prices barely move month to month. |
| `stale_after_days` | If the **newest** comp in a bucket is older than this, the bucket may not declare a deal. | `45` | Tighten if you only trust very recent data; loosen only if thin players are being blocked purely on age. |
| `max_dispersion` | MAD/median above this means the comps disagree too much to trust a median. | `0.5` | Lower it to demand tighter agreement. Raise it and you start flagging off buckets that don't describe one market. |
| `mad_threshold` | Outlier trim, in scaled median-absolute-deviations. Only applied when a bucket has 5+ points. | `3.5` | Lower to trim more aggressively (the classic "modified z-score" cutoff is 3.5). |
| `min_distinct_comp_dates` | How many separate days a bucket's observations must come from. Counting observations alone isn't enough: the scan records every listing it sees each morning, so six asks captured in one morning is one snapshot six listings deep, not six readings of a market. Hand-entered sold comps are exempt -- three real sales on one day are three real transactions. | `3` | Rarely. Lowering it re-opens the hole that one-row-per-listing closed. |
| `min_comp_span_days` | How much calendar time those observations must span, for the same reason. | `7` | Rarely. Note a corpus younger than this cannot clear it at all, which is where the project sits today. |
| `require_flag_eligible_comp` | Only identity-and-grade-matched comp levels may declare a deal; broader levels are context and can never flag. **Leave this `true`** -- it is what stops the circular price-tier comparison that produced every false positive in v1. | `true` | Never, in practice. Setting it `false` really does let deals be declared off context-only comps, including the price-bracket level that is defined by price and therefore cannot be evidence about price. CardPro will do it, but it puts a warning at the top of the report's health footer every run while it is off. |

### Resale economics -- `settings.json` → `economics`

**These are assumptions, not facts.** Every one shows up in the report next
to the profit figure it produced, so you can disagree with one and re-run.
Tune them to how you actually sell. Percentages apply to the expected sale
price only.

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `marketplace_fee_pct` | Marketplace final value fee. The default is eBay's standard trading-card rate for a seller without a Store subscription, at time of writing. | `13.25` | Verify against your own account -- Store subscribers and Top Rated sellers pay less. Leaving it high is the safe error: it understates profit. If you charge buyers for shipping, eBay fees that too, so fold the difference in here. |
| `marketplace_fixed_fee` | Flat per-order fee. | `0.3` | Match your marketplace. |
| `payment_fee_pct` | Separate payment-processing percentage. Zero because eBay's managed payments bundles processing into the fee above -- charging it again would double-count. | `0.0` | Set it if you sell somewhere that bills processing separately. |
| `outbound_shipping` | What it costs **you** to mail the card out. Default assumes a bubble mailer with USPS Ground Advantage and tracking. | `5.0` | Lower it (~$1) if you ship raw commons in an eBay Standard Envelope with no real tracking. |
| `supplies` | Penny sleeve, toploader, team bag, mailer -- per card, every time. | `1.0` | Adjust to what you actually pay in bulk. |
| `sales_tax_pct` | Tax paid on **acquisition**. Zero means "not modelled", and the report says so out loud rather than letting you assume it was measured. | `0.0` | Set your own rate (e.g. `7.0`) to include it in total cost. |
| `resale_haircut_pct` | The gap between the median comp and what you'd realistically get selling promptly. | `5.0` | Raise it if you sell fast and cheap, or for illiquid cards. |

### Auctions -- `settings.json` → `auctions`

An auction's current bid is not a price, so auctions never reach the deal
gate. They get their own section and their own math.

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `required_margin_pct` | Max rational bid is the highest you could bid and still keep this much margin against estimated market value, after fees and shipping. | `25` | Raise it to bid more conservatively, lower it if you're happy on thinner margins. |
| `ending_soon_hours` | What counts as urgent. **Only** used for ordering the auction section -- never to relax any quality gate. | `24` | Widen it if you check email less than daily. |

The pocket-change band that splits `CHEAP AUCTIONS` off from
`AUCTIONS ENDING SOON` lives in `settings.json` → `report`, because it is a
question about how the email is organised rather than about auction maths:

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `report.cheap_auction_floor` | Bottom of the band. Below it a "bid" is usually an untouched opening price -- but the pocket-change band starts at a penny, and what may reach this section at all is already gated by `cheap_cards.require_desirable_attribute`, so an untouched $0.01 opener still needs a rookie, auto, patch, serial number, parallel or grade on it. | `0.01` | Raise it to `0.5` if penny openers start crowding the section. |
| `report.cheap_auction_ceiling` | Top of the band, measured on **current bid + shipping**, not the bid alone. | `10.0` | Raise it to bid in a wider band. |

What may reach the section at all is still decided once, upstream, by
`cheap_cards.require_desirable_attribute` -- `CHEAP AUCTIONS` does not
re-filter, so switching that off widens this section too.

### Focus -- `settings.json` → `focus`

Which findings reach the email, and how long it may be. Documented in full
in [Focus](#focus-what-reaches-the-email-and-how-long-it-is), including the
table of every key.

### Immediate alerts -- `settings.json` → `alerts`

The `ACT NOW` tier. Deliberately conservative: too many alerts destroy the
product. An opportunity must clear **both** of these **and** have a
flag-eligible comp to earn the top slot; everything else waits for the
morning digest.

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `immediate_alert_min_savings_dollars` | Dollar savings required. | `25` | Keep it in step with `focus.price_ceiling`: a $40 card cannot save $150 without being worth $190, so the old `150` meant `ACT NOW` could never fire on the cards the report is now built around, and an alert tier that cannot fire is not conservative -- it is off. Raise both together. |
| `immediate_alert_min_discount_pct` | Discount required. | `40` | Lower only if `ACT NOW` is firing too rarely to be useful; raise it the first time it fires on something you'd have skipped. |

### Everything else in `settings.json`

| Key | What it does | Default | When you'd change it |
|---|---|---|---|
| `ebay.category_id` | eBay category to search within, for the dormant Browse API path. | `"212"` (Sports Trading Cards) | eBay renumbers categories occasionally -- re-check via `python -m scripts.lookup_ebay_category` if results look off-topic. |
| `ebay.marketplace_id` | eBay site the API path queries. | `"EBAY_US"` | Only if you're buying on a non-US eBay. |
| `ebay.active_listing_limit_per_player` | Max active listings pulled per player on the API path. | `50` | Raise for deeper coverage once API access exists. |
| `ebay.sold_lookback_days` | How far back to pull sold comps on the API path. | `60` | Only relevant with Marketplace Insights access. |
| `ebay.min_comps_required` | Minimum samples for the **legacy** API-path comp table. Also the fallback default for `valuation.min_comps_required` if the `valuation` section is missing entirely. | `3` | Tune `valuation.min_comps_required` instead -- that's the one the live engine reads. |
| `craigslist.site` | Which Craigslist subdomain the quick-check links point at. | `"chicago"` | Your metro. |
| `craigslist.category` | Craigslist search category for those links. | `"sss"` (all for sale) | Narrow it if `sss` is too noisy. |
| `dedupe.seen_listings_path` | Where dedupe state lives. | `"data/seen_listings.json"` | Rarely. |
| `dedupe.prune_after_days` | How long a listing stays in the "already seen" file after its last flag before it's forgotten. | `120` | Shorten if you want to be re-shown old listings sooner. |
| `email.subject_prefix` | Prefix on every email subject, so Gmail filters can catch them. | `"[Card Deals]"` | Whatever your filters expect. |
| `sold_comps.path` | Where hand-entered sold prices live -- the only comps in the system with `basis: sold`. Add one with `python -m scripts.add_sold_comp --help`; the report's footer says which ones are worth the trip to 130point. | `"config/sold_comps.json"` | Rarely. |

`ebay_alerts` is documented in
[its own section above](#config-configsettingsjsons-ebay_alerts-section).

## Acquisition targets

A target is a specific card you've told CardPro to find below a price **you**
set. It is deliberately separate from the player watchlist and from
valuation, because they answer different questions:

- player watchlist → "show me anything interesting about this player"
- comp engine → "what is this card actually worth"
- acquisition target → "I want THIS card at or below THIS price"

**A target hit is not a claim that the card is underpriced.** A card can be a
target hit and a bad deal at the same time (you're paying up for something
you specifically want), or a great deal and not a target at all. They get
separate report sections for exactly that reason.

Add one to `config/watchlist.json` → `target_cards`. It ships empty on
purpose -- these are your prices to set:

```json
"target_cards": [
  {
    "label": "2024 Panini Prizm Caleb Williams Silver PSA 10",
    "player": "Caleb Williams",
    "year": 2024,
    "set_name": "Prizm",
    "parallel": "Silver",
    "card_number": "301",
    "grader": "PSA",
    "grade": "10",
    "buy_zone": 400,
    "great_buy": 350,
    "immediate_alert": 300
  }
]
```

`_target_cards_example` in the file holds that exact shape, ready to copy.

**Price bands.** All three are optional; specify at least one. A listing is
assigned the strongest band its **total cost** (price + shipping + tax)
clears:

| Band | Meaning |
|---|---|
| `buy_zone` | The price at which you'd be happy to own it. |
| `great_buy` | Better than you expected to pay. |
| `immediate_alert` | Drop-everything cheap. |

A card that matches the target but sits above every band still shows up, as
"above buy zone" -- knowing your target card is listed at all is useful
information, not noise.

**The matching rule, which is the whole point:** `player` is required, and
every other field is optional and narrows the match. **A field you specify
must match exactly. A field the listing couldn't identify never counts as a
match.** Unknown is never treated as satisfying a target. Being told "your
target card showed up" and finding it's a different parallel is exactly the
failure this avoids -- so if you specify `"parallel": "Silver"`, a listing
whose parallel CardPro couldn't read will not hit, even if it really is a
Silver. Write broad targets (player + set + grade) when you'd rather see
near-misses, narrow ones (down to the card number) when you only want the
exact card.

Targets also generate their own precise suggested saved search, since a card
you've explicitly said you want is worth a dedicated query in a way that
speculative parallel permutations are not.

An entry missing `player` is skipped rather than crashing the run -- a typo
in a personal config file shouldn't take down the daily scan.

## How dedupe works

`data/seen_listings.json` tracks every eBay listing ID that's ever been
emailed as an opportunity, along with the price it was flagged at. An
opportunity is included in the report again only if:

- it's never been flagged before, **or**
- its price has dropped further since the last time it was flagged (these
  show up under `PRICE DROPS`).

Auctions, target hits and needs-review entries are **not** deduped -- an
auction that's still live needs to keep appearing until it closes.

(Craigslist links aren't deduped either -- they're static search URLs, not
individual listings, so the same links appear in every email.)

If you ever want to re-see everything (e.g. after changing a threshold),
delete `data/seen_listings.json`, or edit it and leave it valid JSON. A file
that exists but won't parse is *not* treated as "start fresh" any more: it
raises (`dedupe.CorruptSeenListings`) and the run stops, because starting
fresh would re-report every listing you have already seen as new and then
commit that reset over the real file. Note it's a tracked file, not
gitignored -- see "Running on a schedule" for why.

## Project layout

```
config/
  watchlist.json      -- players, tiers, acquisition targets
  settings.json        -- deal gate, valuation gates, economics, auctions, alerts, focus
  sold_comps.json       -- hand-entered sold prices (empty by default)
.github/workflows/
  tests.yml             -- CI: runs pytest on push/PR
  daily-scan.yml          -- runs the scraper on a schedule in GitHub's cloud (recommended over local cron)
src/
  main.py                  -- orchestrates the daily run; one evaluation path for all sources
  card_identity.py          -- title -> year/set/parallel/card#/print run/auto/memorabilia, each with
                               confidence, plus negative signals (reprint/custom/digital/sealed/lot/...)
  matcher.py                 -- title -> player(s); grade details (grader, grade, qualifier, auth-only slabs)
  comps.py                    -- CompEngine: market-segmented, quality-gated valuation
                                 (the legacy price-tier engine lives above it, deprecated, unable to flag)
  sold_comps.py                -- hand-entered sold prices -- the only comps carrying basis "sold"
  comp_requests.py              -- which sold comps to go and get, ranked by listings unlocked
  economics.py                   -- fees, net proceeds, ROI, max rational bid, breakeven grade probability
  targets.py                      -- acquisition targets and price bands
  search_terms.py                  -- saved-search generation + coverage gaps
  desirability.py                   -- what makes a copy scarce, kept out of the price maths
  reasons.py                         -- the canonical vocabulary of "why this wasn't reported"
  observability.py                    -- per-run data-quality counters for the SYSTEM HEALTH footer
  price_history.py                     -- the self-building comp corpus, one row per listing
  ebay_email_alerts.py                  -- IMAP fetch, HTML extraction, listing-type detection
  ebay_client.py                         -- eBay Browse/Insights client (built, dormant)
  craigslist_links.py                     -- builds quick-check search URLs (no scraping)
  dedupe.py                                -- seen-listings tracking
  focus.py                                  -- what reaches the email: price ceiling, bidding room, length cap
  report.py                                  -- decides the report, and renders the text email
  report_html.py                              -- renders the same model as the HTML email
  emailer.py                                   -- Gmail SMTP send (multipart: text + HTML)
  config.py                                    -- loads .env + config JSON
  models.py                                     -- the Listing dataclass
data/
  seen_listings.json -- dedupe state (tracked in git -- see "Running on a schedule")
  ebay_alert_price_history.json -- the self-built comp corpus (also tracked)
logs/
  scraper.log, cron.log (gitignored) -- run logs
scripts/
  replay_corpus.py -- identity KPI, plus replays of the stored corpus through both engines
  collapse_corpus_duplicates.py -- one-shot migration to one row per listing (idempotent)
  add_sold_comp.py -- records one hand-entered sold comp, validated before it writes
  test_ebay_alerts.py -- standalone check of the email-alerts path against your real inbox
  test_email.py -- standalone Gmail send check (no eBay needed)
  lookup_ebay_category.py -- verifies the eBay category ID with real credentials
  install_cron.sh / uninstall_cron.sh -- crontab helpers (local-cron alternative)
tests/
  pytest suite covering card_identity/matcher/comps/economics/targets/reasons/
  observability/dedupe/report/focus/search_terms/craigslist_links/price_history/
  sold_comps/comp_requests/desirability/replay_corpus/ebay_email_alerts/config/
  models + mocked full-run tests for both eBay paths
docs/
  CARDPRO_2_AUDIT.md -- the current audit: scores, failure modes, roadmap, data-source matrix
  PROJECT_STATUS.md -- what the system is and does, today
  AUDIT_AND_ROADMAP.md -- the previous audit, kept for history
```

## The non-negotiables

These are not tunables. Nothing in `settings.json` relaxes them.

1. **Never confuse an asking price with a sold price.** They are different
   claims and the report always says which one it has.
2. **Never treat a current bid as a price.** Auctions get their own section
   and a max rational bid, never a discount claim. The max bid is the bid
   alone, with the resale haircut already applied; when shipping is
   unknown it is an upper bound and the line says so, because the
   calculation had to assume $0 postage to produce a number at all.
3. **Never compare different parallels or different grades** as if they were
   the same card.
4. **Never guess a missing attribute.** Unknown means unknown -- it never
   matches, never satisfies a target, and never fills in a comp key.
5. **Never a single black-box score.** Every number traces to a rule and a
   data point you can go look at.
6. **Never build tooling to defeat a site's anti-bot defenses**, regardless
   of how low-stakes the ask.
7. **Never buy anything automatically.** CardPro discovers and analyses. You
   decide.

Two more that follow from those: never go silent (you always get an email,
even when the answer is "nothing"), and prefer being uncertain over being
confidently wrong.

## Explicitly out of scope

- Facebook Marketplace scraping.
- Automated Craigslist scraping -- see "Why Craigslist isn't scraped"
  above; it's quick-check links instead.
- Fuzzy/ML-based title matching or comp modeling -- matching is
  keyword-based and comps are plain trimmed, recency-weighted medians on
  purpose, so you can see exactly why something was (or wasn't) flagged.
- Any prediction. Nothing here guesses what a card will sell for, guesses a
  grade, or guesses whether shipping is free.
- Seller-risk scoring, image/OCR slab reading, an LLM parsing layer, a
  dashboard, portfolio tracking, and non-card collectibles -- all
  deliberately deferred behind trustworthy valuation. See
  [`docs/CARDPRO_2_AUDIT.md`](docs/CARDPRO_2_AUDIT.md) §7.
