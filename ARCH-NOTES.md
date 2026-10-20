# CardPro architecture notes (for agents)

Written 2026-10-16 on branch `cardpro-auto-2026-10-16`, from commit `aefb60d`.
The repo default branch is `claude/sports-card-deal-scraper-6imq2v` (there is no `main`).
Longer narrative: `docs/PROJECT_STATUS.md`, `docs/AUDIT_2026_09.md`, `docs/CARDPRO_2_AUDIT.md`.

## Facts that differ from the brief

- **eBay Browse API is live** (PR #23, merged 2026-10-15). `src/main.py:fetch_ebay_active` runs it
  alongside the IMAP alert path when `EBAY_CLIENT_ID/SECRET` are set; the 2026-10-16 run wrote
  3,130 corpus rows (vs. 19-198/day from alerts alone). Marketplace Insights (sold data) returns 404:
  not granted (`config/settings.json` `ebay._insights_comment`).
- Corpus: `data/ebay_alert_price_history.json`, 10,958 distinct listings, 2026-08-21..2026-10-16,
  100% `basis: asking`. 6.0 MB. One row per listing.
- `config/sold_comps.json` `sales: []` -- zero sold comps.
- Tests: 1,332 pass (`pytest -q`, ~18 s).

## Pipeline (file:function)

1. **Fetch**
   - Browse API: `src/ebay_client.py:search_active_listings` -> `src/main.py:fetch_ebay_active` (newest page + ending-soonest auctions page, per player, category 261328).
   - IMAP alerts: `src/ebay_email_alerts.py:fetch_alert_messages` -> `extract_listings_from_html` (title/price/shipping/listing type/bids/time left) -> `src/main.py:fetch_ebay_alert_active`.
   - Merge: `src/main.py:merge_sources` (API copy wins).
2. **Build listing**: `src/main.py:_build_listing` -> player match `src/matcher.py:match_players`, grade `matcher.detect_grade_details`, identity `src/card_identity.py` (`extract_identity` / `CardIdentity` with per-field `Field(value, ...)`), negative signals `card_identity.NEGATIVE_SIGNAL_PATTERNS` / `HARD_BLOCK_SIGNALS` / `is_excluded_from_deals`.
3. **Truncation**: `src/main.py:mark_truncated_titles`, `ebay_email_alerts.looks_truncated`.
4. **Record corpus**: `src/main.py:record_observations` -> `src/price_history.py:record` (fixed-price only; auctions + blocked excluded), prune 90 d, `drop_probable_opening_bids`.
5. **Comp engine**: `src/comps.py:CompEngine.lookup`. Levels (`LEVEL_SPECS`, `comps.py:~550`):
   - `exact` = player+year+set+parallel+card#+market+variant -> flag-eligible, base confidence high
   - `same_card` = player+year+set+parallel+market+variant -> flag-eligible, medium
   - `same_set` (parallel unknown) -> context only
   - `price_tier` -> context only (circular)
   - `market` = raw | grader+grade+qualifier (`comps.market_key`); `variant` = (auto, relic, numbering) (`comps.printing_variant`).
   - Gates (`comps.assess_comp_match`, settings `valuation`): min 3 comps, >=3 distinct dates, >=7-day span (`_is_concentrated`), MAD dispersion <=0.5, stale >45 d, mixed card numbers. Asking basis -> confidence downgraded, never "high". Sold comps (`src/sold_comps.py:to_observation`) exempt from date-spread gate.
6. **Economics**: `src/economics.py:evaluate` (FVF 13.6% + $0.30, outbound $5 flat, supplies $1, haircut 5%, tax 0) and `max_rational_bid` (auctions only, 25% margin of market value). `breakeven_grade_probability` exists but is unused by the pipeline.
7. **Deal gate**: `src/main.py:evaluate_listings` (~line 691). Order: no price -> negative-signal block -> multi-player -> truncated+graded -> cheap commodity filter (`desirability.is_commodity`) -> target hit -> comp lookup -> economics -> auctions diverted -> context-only blocked -> **gross discount >=30% (cheap <$10: >=50%) AND gross $ savings >=$3 (cheap $3)** -> `is_opportunity`.
   - NOTE: the gate is on GROSS discount (`economics.gross_discount` = market - cost), not on net profit. `expected_profit` is computed and shown but does not gate. A listing can be an "opportunity" with negative net profit (`resale_uneconomic`).
8. **Dedupe**: `src/dedupe.py:is_new_or_price_drop`, `data/seen_listings.json`.
9. **Focus**: `src/focus.py:select` / `trim` (price_ceiling $50 all-in, exceptional escape: >=50% AND >=$100 AND flag-eligible; numbered <=/99 up to $200; cool cards up to $100; cap 40 listings, 10/section).
10. **Report**: `src/report.py` (text) + `src/report_html.py`; sections ACT NOW, DEALS, TARGET HITS, AUCTIONS, CHEAP AUCTIONS, COOL CARDS, CHEAP FINDS, YOUNG CORE, OFFERS, WATCH, NEEDS REVIEW, PRICE DROPS + footer. Sold-comp suggestions `src/comp_requests.py:build_requests`.
11. **Send**: `src/emailer.py` (Gmail SMTP). **State**: `src/run_marker.py`, committed by workflow.

## Scheduling

`.github/workflows/daily-scan.yml`: cron `47 12 * * *` + backup `47 15 * * *` (both `--skip-if-ran-today`; manual dispatch scans unconditionally), routed by a `case` on `SCHEDULE`; phase-2 `17 */3 * * *` (`--alert-only`) is commented out; timeout 15 min, pytest gate, state commit with rebase-retry. `tests.yml` runs CI on 3.9/3.12.
Measured from 100 GitHub runs (fact): the 15:00 cron fired 22/22 days (09-24..10-15), 3.2-7.3 h late (median 4.7 h); the 19:00 backup skipped 19/22 days and twice re-scanned after midnight UTC (second email). No dropped runs. Reviewer data, 52 scheduled runs 10-01..10-19: minimum lateness 2.14 h. Hence 12:47/15:47: earliest start 14:55 (after the burst), median ~17:30, backup worst case 23:07. After merge: if any of the first 7 primary runs starts before 14:30 UTC, move the cron 1 h later. Repo is public, so Actions minutes are unlimited.

## Hardcoded thresholds worth knowing

- `config/settings.json`: `discount_threshold_pct 30`, `min_savings_dollars 3`, `cheap_cards.price_ceiling 10 / min_discount 50`, `economics.*`, `auctions.required_margin_pct 25`, `focus.price_ceiling 50`, `valuation.*`.
- `src/desirability.py`: `SCARCE_PRINT_RUN 500`, `LOW_NUMBERED_PRINT_RUN 99`.
- `src/ebay_email_alerts.py`: `PROBABLE_OPENING_BID_MAX 1.00`, `IMAP_TIMEOUT_SECONDS 60`.
- `src/report.py`: `OFFER_FAIR_BELOW_MAX_PCT 5`, `OFFER_AGGRESSIVE_BELOW_MAX_PCT 15`, `THIN_SAMPLE_N 5`, `WATCH_CLOSE_FRACTION 0.75`.
- `src/main.py`: `CORPUS_WARN_MB 40`.

## Vocabulary / dictionaries

All in `src/card_identity.py`: `MANUFACTURERS`, `SET_KEYWORDS` (~line 57), `SET_ALIASES`, `BARE_SET_BY_BRAND`, `COMPOUND_PARALLELS`, `UNAMBIGUOUS_PARALLELS`, `COLOR_PARALLEL_WORDS`, `PARALLEL_QUALIFIER_WORDS`, `TEAM_AND_PHRASE_MASKS`, `AUTOGRAPH_KEYWORDS`, `MEMORABILIA_KEYWORDS`, `PATCH_KEYWORDS`, `NEGATIVE_SIGNAL_PATTERNS`, `HARD_BLOCK_SIGNALS`, flagship base-set guard (`FLAGSHIP_MANUFACTURERS`, `TITLE_NOISE_WORDS`, `_flagship_window_is_clean`). Graders/qualifiers: `src/matcher.py:GRADERS`, `GRADE_QUALIFIERS`.

## Comp store schema (one corpus row)

Key `"<Player>|raw"` or `"<Player>|graded"` -> list of
`{id, date, price, basis, title, title_truncated, year, set_name, manufacturer, parallel, card_number, grader, grade, qualifier, is_autograph, relic, print_run, is_serial_numbered, is_base, listing_type}`.
No shipping, no listing start time, no seller. Sold comps: `config/sold_comps.json` `sales[]` with `player, year, set_name, parallel, card_number, grader, grade, qualifier, price, shipping, date, source, note`; CLI `python -m scripts.add_sold_comp` (`--paste` bulk).

## Measurement tools

- `python -m scripts.replay_corpus --reextract`: identity KPI + would-flag list over the whole corpus. Since 2026-10-20 `WOULD FLAG` is the production gate (rows rebuilt via `main._build_listing`, decided by `main.evaluate_listings`, net >= `main.required_net_profit`), with net and required net per flag; the old gross-only count is on a `gross-discount only (pre-10-16 gate)` line. Rows are rebuilt and the engine built by the shared `eval/run_eval.py` helpers (`listing_from_row`, `build_engine`, `offline_config`), valued as of the latest observation date (eval pins 2026-10-16). Shipping is not stored, so assumed inbound shipping is charged. Should equal `python -m eval.run_eval` `corpus_replay.flags_total` (2026-10-20: both 2; gross-only 40).
- `python -m scripts.rehearse_run`: renders the real report offline.
- Baseline KPI (2026-10-16, fact): same_card-complete key 28.4%, exact 20.9%; first blocker parallel 36.1%, set_name 28.5%, year 7.0%; would-flag 15 (whole corpus, gross-discount gate; the replay printed this rule until 2026-10-20).

## Known gaps (vs. the brief)

- No net-profit floor, no time value / profit-per-hour, no shipping-out by method (flat $5), no grading branch in the pipeline, no deal tiers, no listing age, no max offer for fixed-price/Best Offer listings (max bid is auctions-only).
- Comp type is labelled (asking vs sold) but not on a single compact line per flag.
- No bought/passed/sold record.
- Corpus growth with Browse on: ~3k rows/day risks the 100 MB GitHub file limit (warn at 40 MB).
