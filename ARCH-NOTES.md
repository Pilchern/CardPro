# CardPro architecture notes (for agents)

Written 2026-10-16 on branch `cardpro-auto-2026-10-16`, from commit `aefb60d`; updated 2026-10-20 (after PRs #25-#27).
The repo default branch is `claude/sports-card-deal-scraper-6imq2v` (there is no `main`).
Longer narrative: `REPORT-2026-10-16.md` (PR #25 changes), `docs/PROJECT_STATUS.md`, `docs/AUDIT_2026_09.md`, `docs/CARDPRO_2_AUDIT.md`.

## Facts that differ from the brief

- **eBay Browse API is live** (PR #23, merged 2026-10-15). `src/main.py:fetch_ebay_active` runs it
  alongside the IMAP alert path when `EBAY_CLIENT_ID/SECRET` are set; the two 2026-10-16 runs wrote
  4,832 corpus rows (3,130 + 1,702; vs. 19-198/day from alerts alone), then 2,834 / 1,738 / 1,770 on 10-17/18/19.
- **No sold data, and no automated source for it.** Browse returns active listings only; the Finding/Shopping
  APIs were decommissioned 2025-02-05; Marketplace Insights returns 404: not granted
  (`config/settings.json` `ebay._insights_comment`). Sold comps come only by hand or from your own sales.
- Corpus: `data/ebay_alert_price_history.json`, 16,923 rows (16,916 distinct listings, all titled), first seen
  2026-08-26..2026-10-19, 100% `basis: asking`. 7.7 MB, +0.78 MB/day (10-18, 10-19 commits). One row per listing.
- Zero sold comps: `config/sold_comps.json` `sales: []`, `data/flips.csv` header only.
- Tests: 1,630 pass (`python -m pytest -q`, ~60-70 s locally; most of it `TestRealProductionCorpus` loading the live corpus).

## Pipeline (file:function)

1. **Fetch**
   - Browse API: `src/ebay_client.py:search_active_listings` -> `src/main.py:fetch_ebay_active` (newest page with a $20 item floor, `ebay.newest_min_price`, + ending-soonest auctions page at any price, windowed to auctions ending `auctions.min_hours_left`..`max_hours_left` (3..27 h) after fetch via the `itemEndDate` filter (`main.auction_window`, `ebay_client.item_end_date_filter`), per player, category 261328). Browse `itemEndDate` is stored as `Listing.ends_at`.
   - IMAP alerts: `src/ebay_email_alerts.py:fetch_alert_messages` -> `extract_listings_from_html` (title/price/shipping/listing type/bids/time left) -> `src/main.py:fetch_ebay_alert_active`.
   - Merge: `src/main.py:merge_sources` (API copy wins).
2. **Build listing**: `src/main.py:_build_listing` -> player match `src/matcher.py:match_players`, grade `matcher.detect_grade_details`, identity `src/card_identity.py` (`extract_identity` / `CardIdentity` with per-field `Field(value, ...)`), negative signals `card_identity.NEGATIVE_SIGNAL_PATTERNS` / `HARD_BLOCK_SIGNALS` / `is_excluded_from_deals`.
3. **Truncation**: `src/main.py:mark_truncated_titles`, `ebay_email_alerts.looks_truncated`.
4. **Record corpus**: `src/main.py:record_observations` -> `src/price_history.py:record` (fixed-price only; auctions, blocked, search-only player, truncated+graded excluded; a re-seen listing updates its row's price and `last_seen`), prune 45 d on `last_seen` (`ebay_alerts.price_history_max_age_days`), `drop_probable_opening_bids`.
5. **Comp engine**: `src/comps.py:CompEngine.lookup`. Stored titles are re-read with today's parser (`comps.py:~497`). Levels (`LEVEL_SPECS`, `comps.py:~631`):
   - `exact` = player+year+set+parallel+card#+market+variant -> flag-eligible, base confidence high
   - `same_card` = player+year+set+parallel+market+variant -> flag-eligible, medium
   - `same_set` (parallel unknown) -> context only
   - `price_tier` -> context only (circular)
   - `market` = raw | grader+grade+qualifier (`comps.market_key`); `variant` = (auto, relic, numbering) (`comps.printing_variant`).
   - Gates (`comps.assess_comp_match`, settings `valuation`): min 3 comps, >=3 distinct dates, >=7-day span (`_is_concentrated`), MAD dispersion <=0.5, stale >45 d, mixed card numbers. Asking basis -> confidence downgraded, never "high". Sold comps (`src/sold_comps.py:to_observation`, plus sells in `data/flips.csv` via `src/flips.py`) exempt from date-spread gate; >=2 agreeing sales (higher <=1.5x lower) value a card-level bucket alone, at the lower sale (`SOLD_TAKEOVER_MIN`).
6. **Economics** (settings `economics`): `src/economics.py:evaluate`: FVF 14.25% (13.25% on the tax-inclusive total) + $0.40/order ($0.30 at or under $10); ship-out $0.82 envelope for a raw sale under $20, else $6.00 tracked; supplies $0.50 raw / $1.00 slab; buy tax 10.25% on item + shipping; unknown inbound shipping assumed $4.50; resale haircut 20% on ASKING comps, 5% on SOLD. `max_rational_bid` = most you can pay for the item and still net `required_net_profit` at the haircut sale price, for every priced listing (auction bid ceiling, fixed-price max offer/buy), only off a flag-eligible comp; `auctions.required_margin_pct` (0) is an optional extra auction floor. `deal_tier`: Strong needs >=3 SOLD comps; any ASKING comp caps at Maybe. `breakeven_grade_probability` exists but is unused (no grading branch; `grading.enabled false`).
7. **Deal gate**: `src/main.py:evaluate_listings` (~line 744). Order: no price -> negative-signal block -> multi-player -> truncated+graded -> cheap commodity filter (`desirability.is_commodity`) -> target hit -> comp lookup -> economics + MAX -> auctions diverted -> context-only blocked -> **gross discount >=30% (cheap <$10: >=50%) -> gross $ savings >=$3 (cheap $3) -> net `expected_profit` >= `main.required_net_profit` = max($15, 10% of comp median)** -> `is_opportunity` (+ `deal_tier`, $/h at 25 min/flip, net at the lowest comp).
   - The discount checks use GROSS (`economics.gross_discount` = median - all-in cost incl. tax and assumed shipping); the net floor is the last check, so every opportunity nets at least the floor at the median (at the cheap end of the comps it can net less; the summary line shows both).
8. **Dedupe**: `src/main.py:apply_dedupe`, `src/dedupe.py:is_new_or_price_drop`, `data/seen_listings.json` (prune 120 d). Every printed card, not only deals: a non-deal returns only on a >=5% drop (`SHOWN_REPEAT_MIN_DROP_PCT`); target hits always shown; bought/passed rows in `data/flips.csv` are marked seen.
9. **Focus**: `src/focus.py:select` / `trim` (price_ceiling $50 all-in; opportunities up to `focus.deal_capital_ceiling` $150 all-in; exceptional escape: >=50% AND >=$100 AND flag-eligible; numbered <=/99 up to $200; cool cards up to $100; cap 40 listings, 10/section, 5/section for browse sections `report.BROWSE_SECTIONS` via `focus.browse_max_per_section` (held through the leftover pass; cards with a MAX exempt; WATCH excluded because dedupe records opportunities before the report); CHEAP FINDS hidden by `focus.cheap_finds_enabled: false`, counted in the footer).
10. **Report**: `src/report.py` (text) + `src/report_html.py`; sections ACT NOW, DEALS, TARGET HITS, CHEAP AUCTIONS, AUCTIONS, COOL CARDS, CHEAP FINDS (off by default), YOUNG CORE, OFFERS, WATCH, NEEDS REVIEW, PRICE DROPS + footer. A zero-deal subject starts "No deals · " (`report.NO_DEALS_PREFIX`) and counts only auctions with room under their MAX ("2 auctions with room under MAX"; none -> falls through). AUCTIONS (B4, `report.build_model`): auctions with under `auctions.min_hours_left` known time left are dropped before classification (target hits kept), MAX-bearing auctions sort first by bid/MAX, at most `auctions.no_max_cap` (3) without a MAX print; both cuts are counted in the thresholds footer. Auction cards print "ends Tue 7:14 PM CT" (`report.ends_text`, zoneinfo America/Chicago with a hand-coded US rule fallback) when `ends_at` is known. Each deal leads with `report.summary_line` (tier, price, MAX, sale range, ASKING/SOLD n, net low..high, $/h, listing age, ID confidence). Sold-comp suggestions `src/comp_requests.py:build_requests`.
11. **Send**: `src/emailer.py` (Gmail SMTP). **State**: `src/run_marker.py`, committed by workflow.

## Scheduling

`.github/workflows/daily-scan.yml`: cron `47 12 * * *` + backup `47 15 * * *` (both `--skip-if-ran-today`; manual dispatch scans unconditionally), routed by a `case` on `SCHEDULE`; phase-2 `17 */3 * * *` (`--alert-only`) is commented out; timeout 15 min, pytest gate, state commit with rebase-retry. `tests.yml` runs CI on 3.9/3.12.
Measured from 100 GitHub runs (fact): the 15:00 cron fired 22/22 days (09-24..10-15), 3.2-7.3 h late (median 4.7 h); the 19:00 backup skipped 19/22 days and twice re-scanned after midnight UTC (second email). No dropped runs. Reviewer data, 52 scheduled runs 10-01..10-19: minimum lateness 2.14 h. Hence 12:47/15:47: earliest start 14:55 (after the burst), median ~17:30, backup worst case 23:07. After merge: if any of the first 7 primary runs starts before 14:30 UTC, move the cron 1 h later. Repo is public, so Actions minutes are unlimited.

## Hardcoded thresholds worth knowing

- `config/settings.json`: `discount_threshold_pct 30`, `min_savings_dollars 3`, `cheap_cards.price_ceiling 10 / min_discount 50`, `economics.min_net_profit 15 / min_net_profit_pct_of_value 0.1`, `economics.*`, `auctions.required_margin_pct 0 / min_hours_left 3 / max_hours_left 27 / no_max_cap 3`, `focus.price_ceiling 50 / deal_capital_ceiling 150 / max_per_section 10 / browse_max_per_section 5 / cheap_finds_enabled false`, `alerts.immediate_alert_min_savings_dollars 25 / min_discount_pct 40`, `ebay.newest_min_price 20`, `ebay_alerts.price_history_max_age_days 45`, `valuation.*`.
- `src/desirability.py`: `SCARCE_PRINT_RUN 500`, `LOW_NUMBERED_PRINT_RUN 99`.
- `src/ebay_email_alerts.py`: `PROBABLE_OPENING_BID_MAX 1.00`, `IMAP_TIMEOUT_SECONDS 60`.
- `src/report.py`: `THIN_SAMPLE_N 5`, `WATCH_CLOSE_FRACTION 0.75`.
- `src/main.py`: `CORPUS_WARN_MB 40`, `SHOWN_REPEAT_MIN_DROP_PCT 5.0`.

## Vocabulary / dictionaries

All in `src/card_identity.py`: `MANUFACTURERS`, `SET_KEYWORDS` (~line 57), `SET_ALIASES`, `BARE_SET_BY_BRAND`, `COMPOUND_PARALLELS`, `UNAMBIGUOUS_PARALLELS`, `COLOR_PARALLEL_WORDS`, `PARALLEL_QUALIFIER_WORDS`, `TEAM_AND_PHRASE_MASKS`, `AUTOGRAPH_KEYWORDS`, `MEMORABILIA_KEYWORDS`, `PATCH_KEYWORDS`, `NEGATIVE_SIGNAL_PATTERNS`, `HARD_BLOCK_SIGNALS`, flagship base-set guard (`FLAGSHIP_MANUFACTURERS`, `TITLE_NOISE_WORDS`, `_flagship_window_is_clean`). Graders/qualifiers: `src/matcher.py:GRADERS`, `GRADE_QUALIFIERS`.

## Comp store schema (one corpus row)

Key `"<Player>|raw"` or `"<Player>|graded"` -> list of
`{id, date, last_seen, price, basis, title, title_truncated, year, set_name, manufacturer, parallel, card_number, grader, grade, qualifier, is_autograph, relic, print_run, is_serial_numbered, is_base, listing_type}` (`date` = first seen, `last_seen` = latest sighting; rows recorded before the field existed lack it until re-seen, 7,090 of 16,923 have it).
No shipping, no listing start time, no seller. Sold comps: `config/sold_comps.json` `sales[]` with `player, year, set_name, parallel, card_number, grader, grade, qualifier, price, shipping, date, source, note`; CLI `python -m scripts.add_sold_comp` (`--paste` bulk); your own sales: `data/flips.csv` (`date,item_id,action,price,ship,fees,grader,grade,title,note`, action pass|buy|sell).

## Measurement tools

- `python -m scripts.replay_corpus --reextract`: identity KPI + would-flag list over the whole corpus. Since 2026-10-20 `WOULD FLAG` is the production gate (rows rebuilt via `main._build_listing`, decided by `main.evaluate_listings`, net >= `main.required_net_profit`), with net and required net per flag; the old gross-only count is on a `gross-discount only (pre-10-16 gate)` line. Rows are rebuilt and the engine built by the shared `eval/run_eval.py` helpers (`listing_from_row`, `build_engine`, `offline_config`), valued as of the latest observation date (eval pins 2026-10-16). Shipping is not stored, so assumed inbound shipping is charged. Should equal `python -m eval.run_eval` `corpus_replay.flags_total` (2026-10-20: both 2; gross-only 40).
- `python -m scripts.rehearse_run`: renders the real report offline.
- Baseline KPI (2026-10-16, fact): same_card-complete key 28.4%, exact 20.9%; first blocker parallel 36.1%, set_name 28.5%, year 7.0%; would-flag 15 (whole corpus, gross-discount gate; the replay printed this rule until 2026-10-20).

## Known gaps (vs. the brief)

- Zero sold comps in the store, and no automated sold source (see Facts): every valuation is ASKING, so no deal can reach the Strong tier until sales are entered by hand or logged as flips.
- No grading branch in the pipeline (`grading` prices are loaded but unused). Time value is display only ($/h); nothing gates on it.
- Corpus growth: ~1.75k rows/day (10-18, 10-19), +0.78 MB/day; at 45-day retention that plateaus around 35 MB (inferred), close to the 40 MB warn and under GitHub's 100 MB file limit.
