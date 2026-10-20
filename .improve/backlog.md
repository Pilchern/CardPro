# Backlog

Score = (value x confidence) / effort, each 1-5. Value is tied to: real flags/week, freshness, Nick's time, reliability, measurement integrity. Weighted by Nick's taste (decisions.md): mergeable code beats chores.

| ID | Item | Evidence | Files | V | C | E | Score | Status |
|---|---|---|---|---|---|---|---|---|
| B1 | Schedule earlier: primary 12:47 UTC, both crons skip-if-ran-today | 52 runs: min lateness 2.14 h, median 4.7 h; lands ~2.2 h sooner; also helps API license §8.1.3 (6 h freshness) | .github/workflows/daily-scan.yml, README, ARCH-NOTES, docs/PROJECT_STATUS.md | 4 | 4 | 1 | 16 | shipped (PR #26) |
| B3 | Corpus replay headline uses production net gate | replay says 40 flags, production gate gives 2 | scripts/replay_corpus.py, tests | 3 | 5 | 1 | 15 | shipped (PR #27) |
| B2 | No-deal-day email trim (see N3) | 0 deals in 3 days; 40 cards, ~4k words | src/report.py, src/focus.py, config/settings.json | 3 | 3 | 2 | 4.5 | shipped (PR #29) |
| B4 | Auctions you can act on: Browse auction pass windowed to end 3-36 h out (not the soonest-ending edge); MAX-bearing auctions first by bid/MAX, no-MAX auctions capped at 3; absolute end time on each card; subject stops saying 'N auctions to review' when none has a MAX | R1: section = 10 soonest-ending of ~2k/day, ~0 with a MAX, countdown taken ~1 min before send so cards end within ~1 h of arrival (code fact; time-left inferred) | src/main.py:237, src/ebay_client.py:98-104, src/report.py:1446,1609-1616,1128,1984, src/models.py | 5 | 4 | 2 | 10 | shipped (PR #31) |
| B5 | Production-gate test cost: TestRealProductionCorpus loads the live corpus (57 of 62 s; grows toward ~36 MB) | Scout timing; the workflow pytest gate pays it daily | tests/test_comps.py | 2 | 4 | 2 | 4 | todo |
| B6 | Sold tracker via Browse getItem re-checks (SOLD-INFERRED labels) | Researcher A design; ~2,800 calls/day | src/ebay_client.py, new src/sold_tracker.py | 3 | 2 | 4 | 1.5 | unfrozen (N1 accepted); next: live probe of getItemByLegacyId on ~50 old ids via a workflow_dispatch step |
| B7 | Lot/reprint detection on truncated and plural titles | eval 0/2 caught (n=2) | src/card_identity.py | 2 | 2 | 2 | 2 | todo |
| B8 | Re-show a suppressed auction inside its last 24 h | 10-16 backlog | src/dedupe.py | 2 | 3 | 2 | 3 | todo |
| B9 | `scripts/rehearse_run` fake ids defeat self-exclusion | 10-16 backlog | scripts/rehearse_run.py | 1 | 4 | 1 | 4 | todo |
| B10 | Reversed-order set names ("Bowman Chrome University", "Flair Showcase") | 10-16 backlog | src/card_identity.py | 1 | 3 | 1 | 3 | todo |
| B11 | Doc drift: ARCH-NOTES says tests ~18 s (now ~62 s), 10-16 rows 3,130 (4,832); step 7 + "Known gaps" still say the gate is gross-only, no net floor (stale since PR #25) | Scout | ARCH-NOTES.md | 1 | 5 | 1 | 5 | shipped (PR #28) |
| B12 | Dedupe records every opportunity as reported BEFORE the report trims to max_per_section (10) / max_listings (40); any deal or WATCH card cut by those caps is suppressed forever at an unchanged price without ever being shown | B2 reviewer probe (8 low-conf opps, 3 lost); builder confirmed the path pre-dates B2 | src/main.py:~1087 apply_dedupe, src/dedupe.py, src/report.py | 4 | 4 | 2 | 8 | shipped (PR #30) |
| B13 | A deal suppressed at an unchanged price (PRICE_NOT_DROPPED) with a flag-eligible comp reprints daily in WATCH as a near-miss (report._is_close_but_under) and refreshes last_flagged | B12 builder | src/report.py, src/dedupe.py | 2 | 3 | 1 | 6 | todo |

## Research queue
- R1: Auction flow (B4): of the auctions shown 10-17..10-19, how many had bidding room at email time, and how many ended before the email landed?
- R2: Why tune-eval recall fell 1/1 -> 0/1 after the merge (eval values against the live corpus; the corpus drifts). Should the eval pin a corpus snapshot?
