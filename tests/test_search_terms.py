import json
from pathlib import Path

from src import card_identity, search_terms, targets

WATCHLIST = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "watchlist.json").read_text()
)["players"]


def queries_for(player, **kwargs):
    return [s.query for s in search_terms.for_player(player, **kwargs)]


def test_broad_player_query_comes_first():
    searches = search_terms.for_player("Caleb Williams")
    assert searches[0].query == "Caleb Williams"
    assert "only query type CardPro has today" in searches[0].rationale


def test_every_grader_the_matcher_can_read_a_grade_from_is_reachable():
    # The graded gap (1.2% of the corpus against a >25% KPI) is the reason
    # this module exists, and a slab CardPro cannot reach is a slab it never
    # values. PSA/BGS/SGC are the three with their own liquid markets.
    queries = queries_for("Caleb Williams")
    for grader in ("PSA", "BGS", "SGC"):
        assert any(q == f"Caleb Williams {grader}" for q in queries)


def test_specific_grades_are_reachable_as_well_as_whole_graders():
    queries = queries_for("Caleb Williams")
    assert "Caleb Williams PSA 10" in queries
    assert "Caleb Williams PSA 9" in queries
    assert "Caleb Williams BGS 9.5" in queries


def test_no_query_asks_for_two_graders_in_one_title():
    # The old "{player} BGS SGC" slice: eBay ANDs the words, so it matched
    # only titles containing BOTH grader names -- close to nothing. A search
    # that structurally cannot return results is worse than no search,
    # because it looks like coverage.
    graders = ("psa", "bgs", "sgc", "cgc", "csg")
    for search in search_terms.for_player("Caleb Williams"):
        named = [g for g in graders if g in search.query.lower()]
        assert len(named) <= 1, search.query


def test_no_query_relies_on_ebay_search_operators():
    # Every query is plain words. An OR group or a minus-exclusion would pack
    # more into one saved-search slot, but a mistyped or unsupported operator
    # fails silently by returning nothing, and verifying it would mean
    # automating eBay's own search.
    for search in search_terms.plan(WATCHLIST, budget=None):
        slice_text = search.query[len(search.player):]
        assert not any(ch in slice_text for ch in "(),|"), search.query
        # a leading "-" is eBay's exclusion operator; inside a word it is not
        assert not any(word.startswith("-") for word in slice_text.split()), search.query


def test_every_suggestion_carries_a_rationale():
    # A suggested search is never an unexplained instruction -- the report
    # prints the reason next to the query.
    assert all(s.rationale for s in search_terms.for_player("Connor Bedard", sport="hockey"))
    assert all(s.rationale for s in search_terms.plan(WATCHLIST, budget=None))


def test_every_product_query_names_a_set_card_identity_can_recognise():
    # A product query exists to fix set_name resolution (17% today). If the
    # product is not in the extractor's vocabulary, the listings it returns
    # still resolve no set, so the query buys nothing -- and a name in
    # neither place is an invented product.
    known = {name.lower() for name in card_identity.SET_KEYWORDS}
    for search in search_terms.plan(WATCHLIST, budget=None):
        if search.kind != search_terms.KIND_SET:
            continue
        product = search.query[len(search.player):].strip()
        assert product.lower() in known, product


def test_sport_specific_sets_only_for_that_sport():
    hockey = queries_for("Connor Bedard", sport="hockey")
    football = queries_for("Caleb Williams", sport="football")
    assert "Connor Bedard Young Guns" in hockey
    assert not any("Young Guns" in q for q in football)
    assert "Caleb Williams Contenders" in football


def test_hockey_products_are_not_borrowed_from_the_football_shelf():
    # Panini's NHL products ended before the hockey player on this watchlist
    # had a card, so a "Connor Bedard Prizm" search names no card that exists.
    panini_lines = ("prizm", "optic", "select", "mosaic", "contenders")
    for search in search_terms.for_player("Connor Bedard"):
        if search.kind != search_terms.KIND_SET:
            continue
        assert not any(line in search.query.lower() for line in panini_lines), search.query


def test_unknown_sport_yields_no_set_slices_rather_than_wrong_ones():
    queries = queries_for("Munetaka Murakami", sport="cricket")
    assert queries  # still gets the generic slices
    assert not any("Prizm" in q for q in queries if "refractor" not in q.lower())


def test_a_player_with_no_profile_still_gets_graded_queries_but_no_products():
    # Adding a name to the watchlist must never break the run or invent a
    # sport for them; it just means no product queries until a profile exists.
    searches = search_terms.for_player("Someone Unlisted")
    assert any(s.kind == search_terms.KIND_GRADED for s in searches)
    assert not any(s.kind == search_terms.KIND_SET for s in searches)


def test_classic_era_players_get_no_rookie_query():
    # A 1980s rookie card is four figures and buried under reprints; neither
    # is something this report can act on at its price ceiling.
    assert not any(q.endswith(" rookie") for q in queries_for("Michael Jordan"))
    assert any(q.endswith(" rookie") for q in queries_for("Caleb Williams"))


def test_target_query_is_precise():
    target = targets.load_targets(
        [
            {
                "label": "2024 Prizm Caleb Williams Silver PSA 10",
                "player": "Caleb Williams",
                "year": 2024,
                "set_name": "Prizm",
                "parallel": "Silver",
                "card_number": "301",
                "grader": "PSA",
                "grade": "10",
                "buy_zone": 400,
            }
        ]
    )[0]
    search = search_terms.for_target(target)[0]
    assert search.query == "2024 Prizm Caleb Williams Silver #301 PSA 10"
    assert "acquisition target" in search.rationale


def test_the_plan_for_the_whole_watchlist_is_a_list_a_person_could_actually_create():
    # Every one of these is typed into eBay by hand. The catalogue is large;
    # what gets asked for must not be.
    plan = search_terms.plan(WATCHLIST)
    assert len(plan) == search_terms.DEFAULT_SEARCH_BUDGET
    assert len(plan) < len(search_terms.catalogue(WATCHLIST))


def test_no_single_player_can_eat_the_budget():
    plan = search_terms.plan(WATCHLIST)
    for player in WATCHLIST:
        assert sum(1 for s in plan if s.player == player) <= search_terms.DEFAULT_MAX_PER_PLAYER


def test_a_bigger_watchlist_does_not_produce_a_bigger_plan():
    # The budget is the user's time, not a per-player allowance.
    plan = search_terms.plan(["Player {:02d}".format(i) for i in range(60)])
    assert len(plan) <= search_terms.DEFAULT_SEARCH_BUDGET


def test_the_budget_is_adjustable():
    assert len(search_terms.plan(WATCHLIST, budget=7)) == 7


def test_the_plan_is_ranked_best_first():
    priorities = [s.priority for s in search_terms.plan(WATCHLIST)]
    assert priorities == sorted(priorities, reverse=True)


def test_the_first_ten_searches_all_attack_the_graded_gap():
    # Ranking claim, stated as a property rather than as ten literal strings:
    # graded is the measured 1.2%-against->25% gap, so nothing else outranks
    # a player's first grader query.
    assert all(s.kind == search_terms.KIND_GRADED for s in search_terms.plan(WATCHLIST)[:10])


def test_every_watchlist_player_gets_a_search_before_anyone_gets_a_second():
    # Otherwise the deepest markets absorb the whole budget and half the
    # watchlist stays invisible.
    plan = search_terms.plan(WATCHLIST)
    first_repeat = next(
        (i for i, s in enumerate(plan) if any(e.player == s.player for e in plan[:i])),
        len(plan),
    )
    assert first_repeat >= len(WATCHLIST)


def test_the_plan_attacks_both_measured_gaps_not_just_the_larger_one():
    # Graded coverage and set_name resolution are separate problems: of the
    # 11 graded observations in the corpus, 0 resolved a set_name, so graded
    # queries cannot be assumed to fix set_name too.
    kinds = {s.kind for s in search_terms.plan(WATCHLIST)}
    assert search_terms.KIND_GRADED in kinds
    assert search_terms.KIND_SET in kinds


def test_the_plan_never_suggests_the_broad_query_that_already_exists():
    assert all(s.query != s.player for s in search_terms.plan(WATCHLIST, budget=None))


def test_no_misspelling_permutations_are_generated():
    # Explicitly not a feature: noise cost is certain, recall gain is not.
    queries = queries_for("Munetaka Murakami", sport="baseball")
    assert all("Murakami" in q for q in queries)


def test_coverage_gaps_reports_uncovered_slices():
    gaps = search_terms.coverage_gaps(["Caleb Williams"], {"Caleb Williams": ["caleb williams"]})
    missing = [s.query for s in gaps["Caleb Williams"]]
    assert "Caleb Williams PSA" in missing


def test_coverage_gaps_respects_observed_coverage():
    # Property, not a golden list: whatever the slices are, evidence of every
    # one of them leaves nothing to suggest.
    player = "Caleb Williams"
    observed = {
        player: [
            s.query[len(player):].strip().lower()
            for s in search_terms.for_player(player)
            if s.kind != search_terms.KIND_BROAD
        ]
    }
    assert search_terms.coverage_gaps([player], observed) == {}


def test_coarse_observed_marker_covers_finer_slices():
    # A graded listing arriving for a player is evidence that a PSA-oriented
    # search exists, even though the marker recorded is just "psa".
    gaps = search_terms.coverage_gaps(["Caleb Williams"], {"Caleb Williams": ["psa", "auto"]})
    remaining = [s.query for s in gaps.get("Caleb Williams", [])]
    assert not any(q.lower().endswith(" psa") or " psa " in q.lower() for q in remaining)
    assert "Caleb Williams auto" not in remaining
    assert "Caleb Williams Prizm" in remaining


def test_coverage_gaps_spends_the_budget_on_the_players_with_gaps():
    # A player whose coverage is already evidenced should not consume slots
    # that another player's uncovered graded market needs.
    covered_everything = {
        player: [
            s.query[len(player):].strip().lower()
            for s in search_terms.for_player(player)
            if s.kind != search_terms.KIND_BROAD
        ]
        for player in WATCHLIST[:5]
    }
    gaps = search_terms.coverage_gaps(WATCHLIST, covered_everything)
    assert not any(player in gaps for player in WATCHLIST[:5])
    assert sum(len(v) for v in gaps.values()) == search_terms.DEFAULT_SEARCH_BUDGET


def test_coverage_gaps_groups_by_player_best_first():
    gaps = search_terms.coverage_gaps(WATCHLIST, {})
    flattened = [s for suggestions in gaps.values() for s in suggestions]
    assert flattened[0].priority == max(s.priority for s in flattened)
    for suggestions in gaps.values():
        priorities = [s.priority for s in suggestions]
        assert priorities == sorted(priorities, reverse=True)


def test_a_player_the_set_vocabulary_cannot_serve_gets_no_product_queries():
    # Ernie Banks' cards say "Topps", and the brand words are deliberately
    # absent from SET_KEYWORDS, so no product query could resolve a set for
    # him. Guessing which modern retro set reprints him is the invented-card
    # failure this module exists to avoid.
    searches = search_terms.for_player("Ernie Banks")
    assert not any(s.kind == search_terms.KIND_SET for s in searches)
    assert any(s.kind == search_terms.KIND_GRADED for s in searches)
