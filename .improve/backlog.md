# Backlog

Score = (value x confidence) / effort, each 1-5. Value is tied to: real flags/week, freshness, Nick's time, reliability, measurement integrity. Weighted by Nick's taste (decisions.md): mergeable code beats chores.

| ID | Item | Evidence | Files | V | C | E | Score | Status |
|---|---|---|---|---|---|---|---|---|
| B1 | Schedule earlier: primary 12:47 UTC, both crons skip-if-ran-today | 52 runs: min lateness 2.14 h, median 4.7 h; lands ~2.2 h sooner; also helps API license §8.1.3 (6 h freshness) | .github/workflows/daily-scan.yml, README, ARCH-NOTES, docs/PROJECT_STATUS.md | 4 | 4 | 1 | 16 | shipped (PR #26) |
| B3 | Corpus replay headline uses production net gate | replay says 40 flags, production gate gives 2 | scripts/replay_corpus.py, tests | 3 | 5 | 1 | 15 | shipped (PR #27) |
| B2 | No-deal-day email trim (see N3) | 0 deals in 3 days; 40 cards, ~4k words | src/report.py, src/focus.py, config/settings.json | 3 | 3 | 2 | 4.5 | blocked on N3 |
| B4 | Auction section quality: are "10 auctions to review" real flips (bid < max, ends after email lands)? Research first | ~2.7k auction bids/day are the top non-deal reason; subject leads with auctions | src/main.py, src/report.py, src/economics.py | 4 | 2 | 2 | 4 | research next run |
| B5 | Production-gate test cost: TestRealProductionCorpus loads the live corpus (57 of 62 s; grows toward ~36 MB) | Scout timing; the workflow pytest gate pays it daily | tests/test_comps.py | 2 | 4 | 2 | 4 | todo |
| B6 | Sold tracker via Browse getItem re-checks (SOLD-INFERRED labels) | Researcher A design; ~2,800 calls/day | src/ebay_client.py, new src/sold_tracker.py | 3 | 2 | 4 | 1.5 | unfrozen (N1 accepted); next: live probe of getItemByLegacyId on ~50 old ids via a workflow_dispatch step |
| B7 | Lot/reprint detection on truncated and plural titles | eval 0/2 caught (n=2) | src/card_identity.py | 2 | 2 | 2 | 2 | todo |
| B8 | Re-show a suppressed auction inside its last 24 h | 10-16 backlog | src/dedupe.py | 2 | 3 | 2 | 3 | todo |
| B9 | `scripts/rehearse_run` fake ids defeat self-exclusion | 10-16 backlog | scripts/rehearse_run.py | 1 | 4 | 1 | 4 | todo |
| B10 | Reversed-order set names ("Bowman Chrome University", "Flair Showcase") | 10-16 backlog | src/card_identity.py | 1 | 3 | 1 | 3 | todo |
| B11 | Doc drift: ARCH-NOTES says tests ~18 s (now ~62 s), 10-16 rows 3,130 (4,832); step 7 + "Known gaps" still say the gate is gross-only, no net floor (stale since PR #25) | Scout | ARCH-NOTES.md | 1 | 5 | 1 | 5 | shipped (PR #28) |

## Research queue
- R1: Auction flow (B4): of the auctions shown 10-17..10-19, how many had bidding room at email time, and how many ended before the email landed?
- R2: Why tune-eval recall fell 1/1 -> 0/1 after the merge (eval values against the live corpus; the corpus drifts). Should the eval pin a corpus snapshot?
