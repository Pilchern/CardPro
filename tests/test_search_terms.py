from src import search_terms, targets


def test_broad_player_query_comes_first():
    searches = search_terms.for_player("Caleb Williams")
    assert searches[0].query == "Caleb Williams"
    assert "only query type CardPro has today" in searches[0].rationale


def test_graded_slices_are_generated():
    queries = [s.query for s in search_terms.for_player("Caleb Williams")]
    assert "Caleb Williams PSA 10" in queries
    assert "Caleb Williams PSA" in queries
    assert "Caleb Williams BGS SGC" in queries


def test_every_suggestion_carries_a_rationale():
    # A suggested search is never an unexplained instruction -- the report
    # prints the reason next to the query.
    assert all(s.rationale for s in search_terms.for_player("Connor Bedard", sport="hockey"))


def test_sport_specific_sets_only_for_that_sport():
    hockey = [s.query for s in search_terms.for_player("Connor Bedard", sport="hockey")]
    football = [s.query for s in search_terms.for_player("Caleb Williams", sport="football")]
    assert "Connor Bedard Young Guns" in hockey
    assert not any("Young Guns" in q for q in football)
    assert "Caleb Williams Contenders" in football


def test_unknown_sport_yields_no_set_slices_rather_than_wrong_ones():
    queries = [s.query for s in search_terms.for_player("Munetaka Murakami", sport="cricket")]
    assert queries  # still gets the generic slices
    assert not any("Prizm" in q for q in queries if "refractor" not in q.lower())


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


def test_coverage_gaps_reports_uncovered_slices():
    gaps = search_terms.coverage_gaps(["Caleb Williams"], {"Caleb Williams": ["caleb williams"]})
    missing = [s.query for s in gaps["Caleb Williams"]]
    assert "Caleb Williams PSA 10" in missing


def test_coverage_gaps_respects_observed_coverage():
    observed = {"Caleb Williams": [f"caleb williams {suffix}" for suffix, _ in search_terms.PLAYER_SLICES]}
    assert search_terms.coverage_gaps(["Caleb Williams"], observed) == {}


def test_no_misspelling_permutations_are_generated():
    # Explicitly not a feature: noise cost is certain, recall gain is not.
    queries = [s.query for s in search_terms.for_player("Munetaka Murakami", sport="baseball")]
    assert all("Murakami" in q for q in queries)
