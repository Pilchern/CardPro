from src import comps


def test_compute_median_odd_count():
    stats = comps.compute_median([10, 30, 20])
    assert stats.median == 20
    assert stats.sample_size == 3
    assert stats.is_fallback is False


def test_compute_median_empty_returns_none():
    assert comps.compute_median([]) is None


def test_build_comp_table_uses_real_sold_comps_when_enough_data():
    table = comps.build_comp_table({("MJ", "raw"): [100, 110, 120]}, min_comps_required=3)
    assert table[("MJ", "raw")].median == 110
    assert table[("MJ", "raw")].is_fallback is False


def test_build_comp_table_skips_bucket_below_min_comps():
    table = comps.build_comp_table({("MJ", "raw"): [100, 110]}, min_comps_required=3)
    assert ("MJ", "raw") not in table


def test_build_comp_table_falls_back_to_active_listings():
    table = comps.build_comp_table(
        sold_by_bucket={},
        min_comps_required=3,
        fallback_by_bucket={("MJ", "raw"): [50, 60, 70]},
    )
    assert table[("MJ", "raw")].median == 60
    assert table[("MJ", "raw")].is_fallback is True


def test_build_comp_table_prefers_real_sold_over_fallback():
    table = comps.build_comp_table(
        sold_by_bucket={("MJ", "raw"): [100, 110, 120]},
        min_comps_required=3,
        fallback_by_bucket={("MJ", "raw"): [10, 20, 30]},
    )
    assert table[("MJ", "raw")].median == 110
    assert table[("MJ", "raw")].is_fallback is False


def test_price_tier_under_5():
    assert comps.price_tier(1.0) == "under_5"
    assert comps.price_tier(4.99) == "under_5"


def test_price_tier_boundaries_are_lower_inclusive():
    assert comps.price_tier(5.0) == "5_to_25"
    assert comps.price_tier(25.0) == "25_to_100"
    assert comps.price_tier(100.0) == "100_plus"


def test_price_tier_5_to_25():
    assert comps.price_tier(10.0) == "5_to_25"
    assert comps.price_tier(24.99) == "5_to_25"


def test_price_tier_25_to_100():
    assert comps.price_tier(50.0) == "25_to_100"
    assert comps.price_tier(99.99) == "25_to_100"


def test_price_tier_100_plus_unbounded():
    assert comps.price_tier(100.0) == "100_plus"
    assert comps.price_tier(50000.0) == "100_plus"


def test_price_tiering_separates_cheap_and_expensive_comps():
    """The scenario that motivated tiering: a $1 common and a $150
    numbered parallel of the same player/card_type must land in
    different buckets so their prices don't get averaged together."""
    table = comps.build_comp_table(
        sold_by_bucket={},
        min_comps_required=1,
        fallback_by_bucket={
            ("MJ", "raw", comps.price_tier(1.0)): [1.0, 1.5, 2.0],
            ("MJ", "raw", comps.price_tier(150.0)): [140.0, 150.0, 160.0],
        },
    )
    assert table[("MJ", "raw", "under_5")].median == 1.5
    assert table[("MJ", "raw", "100_plus")].median == 150.0


def _obs(player="Caleb Williams", card_type="graded", price=200.0, **identity):
    base = {"player": player, "card_type": card_type, "price": price}
    base.update(identity)
    return base


class TestHierarchicalComps:
    """build_hierarchical_comp_table / lookup_hierarchical_comp: identity-
    aware comps that try exact -> near_exact -> family -> price_tier, first
    level with enough samples wins. See comps.py module docstring."""

    def test_exact_level_used_when_full_identity_matches(self):
        observations = [
            _obs(price=p, year=2024, set_name="Prizm", parallel="Silver", card_number="123", grader="PSA", grade="10")
            for p in (190.0, 200.0, 210.0)
        ]
        table = comps.build_hierarchical_comp_table(observations, min_comps_required=3)

        result = comps.lookup_hierarchical_comp(
            table, player="Caleb Williams", card_type="graded", price=100.0,
            grader="PSA", grade="10", year=2024, set_name="Prizm", parallel="Silver", card_number="123",
        )

        assert result is not None
        stats, level = result
        assert level == "exact"
        assert stats.median == 200.0

    def test_falls_back_to_near_exact_when_exact_has_no_samples(self):
        # Same year/set/parallel/card_type, but no observation shares this
        # exact card_number/grader/grade combo -- exact must miss.
        observations = [
            _obs(price=p, year=2024, set_name="Prizm", parallel="Silver", card_number="999", grader="BGS", grade="9.5")
            for p in (90.0, 100.0, 110.0)
        ]
        table = comps.build_hierarchical_comp_table(observations, min_comps_required=3)

        result = comps.lookup_hierarchical_comp(
            table, player="Caleb Williams", card_type="graded", price=50.0,
            grader="PSA", grade="10", year=2024, set_name="Prizm", parallel="Silver", card_number="123",
        )

        assert result is not None
        stats, level = result
        assert level == "near_exact"
        assert stats.median == 100.0

    def test_falls_back_to_family_when_parallel_unknown(self):
        observations = [
            _obs(price=p, year=2024, set_name="Prizm", parallel="Gold")
            for p in (50.0, 60.0, 70.0)
        ]
        table = comps.build_hierarchical_comp_table(observations, min_comps_required=3)

        # Listing's own parallel is unknown -- near_exact needs a parallel
        # value (even if it's a different one), family only needs year+set.
        result = comps.lookup_hierarchical_comp(
            table, player="Caleb Williams", card_type="graded", price=30.0,
            year=2024, set_name="Prizm",
        )

        assert result is not None
        stats, level = result
        assert level == "family"
        assert stats.median == 60.0

    def test_falls_back_to_price_tier_when_no_identity_known(self):
        observations = [_obs(price=p) for p in (40.0, 50.0, 60.0)]  # all "25_to_100" tier, same as the $50 listing
        table = comps.build_hierarchical_comp_table(observations, min_comps_required=3)

        result = comps.lookup_hierarchical_comp(table, player="Caleb Williams", card_type="graded", price=50.0)

        assert result is not None
        stats, level = result
        assert level == "price_tier"
        assert stats.median == 50.0

    def test_none_when_no_level_has_enough_samples(self):
        observations = [_obs(price=100.0)]  # only 1 observation
        table = comps.build_hierarchical_comp_table(observations, min_comps_required=3)

        result = comps.lookup_hierarchical_comp(table, player="Caleb Williams", card_type="graded", price=50.0)

        assert result is None

    def test_confidence_by_level_mapping(self):
        assert comps.CONFIDENCE_BY_LEVEL["exact"] == "high"
        assert comps.CONFIDENCE_BY_LEVEL["near_exact"] == "medium"
        assert comps.CONFIDENCE_BY_LEVEL["family"] == "low"
        assert comps.CONFIDENCE_BY_LEVEL["price_tier"] == "low"

    def test_hierarchical_comps_are_always_marked_as_fallback(self):
        """There's no "real sold data" version of the self-built alert
        history -- every hierarchical comp is asking-price-based."""
        observations = [_obs(price=p, year=2024, set_name="Prizm") for p in (90.0, 100.0, 110.0)]
        table = comps.build_hierarchical_comp_table(observations, min_comps_required=3)
        assert table["family"][("Caleb Williams", 2024, "Prizm")].is_fallback is True


# ---------------------------------------------------------------------------
# CompEngine -- the current engine. Each test below pins one of the four
# defects measured in docs/CARDPRO_2_AUDIT.md against live production data.
# ---------------------------------------------------------------------------

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TODAY = datetime(2026, 8, 22)
CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "ebay_alert_price_history.json"


def _days_ago(n):
    return (TODAY - timedelta(days=n)).strftime("%Y-%m-%d")


def _ob(price, days_ago=0, obs_id=None, player="Caleb Williams", card_type="raw", **fields):
    """One observation in the shape price_history.deduped_observations emits."""
    obs = {
        "price": price,
        "date": _days_ago(days_ago),
        "id": obs_id if obs_id is not None else "listing-%s-%s" % (price, days_ago),
        "player": player,
        "card_type": card_type,
        "year": None,
        "set_name": None,
        "parallel": None,
        "card_number": None,
        "grader": None,
        "grade": None,
    }
    obs.update(fields)
    return obs


def _spread(prices, step_days=7, base_days_ago=0, **fields):
    """The same prices, but on distinct, well-separated dates -- oldest first.

    Same-day fixtures were never realistic and are no longer harmless. The
    daily scan takes one snapshot per morning, so five prices all dated today
    are one reading five listings deep, and the sample-span gate now says so
    (see comps._is_concentrated). Any fixture that means "a healthy bucket"
    has to look like one.

    Oldest first is deliberate: the recency weighting leans the median toward
    new points, so pairing the newest date with the highest price keeps the
    weighted median where an unweighted one would have put it and stops these
    fixtures from quietly testing the decay curve instead of what they claim.
    """
    n = len(prices)
    return [
        _ob(p, days_ago=base_days_ago + (n - 1 - i) * step_days, **fields)
        for i, p in enumerate(prices)
    ]


#: A fully-identified raw card, so `exact` applies.
_IDENTITY = {"year": 2024, "set_name": "Prizm", "parallel": "Silver", "card_number": "123"}


def _engine(observations, min_comps_required=3, **kwargs):
    return comps.CompEngine(observations, min_comps_required=min_comps_required, today=TODAY, **kwargs)


class TestMarketKey:
    def test_raw_is_one_market(self):
        assert comps.market_key("raw") == ("raw",)

    def test_graded_carries_grader_grade_and_qualifier(self):
        assert comps.market_key("graded", "PSA", "10", None) == ("graded", "PSA", "10", None)
        assert comps.market_key("graded", "PSA", "10", "OC") == ("graded", "PSA", "10", "OC")

    def test_psa_9_and_psa_10_are_different_markets(self):
        assert comps.market_key("graded", "PSA", "9") != comps.market_key("graded", "PSA", "10")

    def test_psa_9_and_bgs_9_are_different_markets(self):
        assert comps.market_key("graded", "PSA", "9") != comps.market_key("graded", "BGS", "9")

    def test_graded_without_grade_has_no_market(self):
        """A slab we can't identify is excluded from grade-segmented levels
        rather than pooled with every other slab (failure mode #3)."""
        assert comps.market_key("graded", "PSA", None) is None
        assert comps.market_key("graded", None, "10") is None

    def test_grader_and_qualifier_are_case_insensitive(self):
        assert comps.market_key("graded", "psa", "10", "oc") == comps.market_key("graded", "PSA", "10", "OC")

    def test_blank_strings_count_as_unknown(self):
        assert comps.market_key("graded", "PSA", "   ") is None


class TestLevelSpecs:
    def test_only_card_identifying_levels_may_flag(self):
        assert comps.FLAG_ELIGIBLE_LEVELS == ("exact", "same_card")

    def test_lookup_order_is_narrowest_first(self):
        assert comps.LEVEL_NAMES == ("exact", "same_card", "same_set", "price_tier")

    def test_circular_price_tier_level_can_never_flag(self):
        """Failure mode #1: the bucket is defined by price, so it cannot be
        evidence about price. Belt and braces -- checked on the spec here and
        end-to-end in test_price_tier_match_is_context_only."""
        assert comps.LEVEL_SPEC_BY_NAME["price_tier"].flag_eligible is False
        assert comps.LEVEL_SPEC_BY_NAME["same_set"].flag_eligible is False
        assert comps.FLAG_ELIGIBLE_LEVELS == ("exact", "same_card")


class TestLevelSelection:
    def test_exact_match_wins_when_full_identity_lines_up(self):
        engine = _engine(_spread((95.0, 100.0, 105.0), **_IDENTITY))
        match = engine.lookup(player="Caleb Williams", card_type="raw", price=40.0, **_IDENTITY)
        assert match.level == "exact"
        assert match.stats.median == 100.0
        assert match.flag_eligible is True

    def test_falls_through_to_same_card_when_card_number_differs(self):
        obs = _spread((95.0, 100.0, 105.0), year=2024, set_name="Prizm", parallel="Silver", card_number="999")
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=40.0, **_IDENTITY)
        assert match.level == "same_card"
        assert match.flag_eligible is True
        assert match.confidence == "low"  # medium base, minus one for asking basis, minus one for n<5

    def test_price_tier_match_is_context_only(self):
        """The exact production defect: nothing is known about the card, so
        the only bucket left is the circular one. It must come back usable as
        context and unusable as a deal."""
        obs = [_ob(p) for p in (40.0, 44.0, 50.0)]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=26.0)
        assert match.level == "price_tier"
        assert match.flag_eligible is False
        assert "context_only_level" in match.blocked_reasons
        assert match.stats.median == 44.0  # still reported, for the report to show

    def test_unknown_parallel_lands_at_same_set_and_is_context_only(self):
        """Failure mode: this level ignores parallel, so it mixes base cards
        with refractors -- the "$1.25 card is 95% under market" bug. It stays
        available as context and can never flag a deal."""
        obs = [
            _ob(p, year=2026, set_name="Topps Chrome", parallel="Refractor", card_type="raw")
            for p in (25.0, 27.0, 30.0)
        ]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=1.25, year=2026, set_name="Topps Chrome")
        assert match.level == "same_set"
        assert match.flag_eligible is False
        assert "context_only_level" in match.blocked_reasons

    def test_there_is_no_grade_blind_level_at_all(self):
        """A raw card must not be shown the graded median even as context --
        being wrong by multiples is worse than saying nothing. `same_set`
        segments by the full market key, and no broader identity-based level
        exists, so this returns nothing."""
        graded = [
            _ob(p, year=2026, set_name="Topps Chrome", parallel="Refractor",
                card_type="graded", grader="PSA", grade="10")
            for p in (25.0, 27.0, 30.0)
        ]
        match = _engine(graded).lookup(
            player="Caleb Williams", card_type="raw", price=1.25, year=2026, set_name="Topps Chrome"
        )
        assert match is None
        assert "family" not in comps.LEVEL_NAMES

    def test_lookup_returns_none_rather_than_guessing(self):
        match = _engine([_ob(100.0)]).lookup(player="Caleb Williams", card_type="raw", price=50.0)
        assert match is None

    def test_none_when_player_is_unknown_to_the_corpus(self):
        obs = [_ob(p, **_IDENTITY) for p in (95.0, 100.0, 105.0)]
        assert _engine(obs).lookup(player="Somebody Else", card_type="raw", price=40.0, **_IDENTITY) is None

    def test_coverage_counts_buckets_that_clear_the_minimum(self):
        obs = [_ob(p, **_IDENTITY) for p in (40.0, 44.0, 50.0)]  # one exact bucket, all in the 25_to_100 tier
        obs.append(_ob(9.0, year=2024, set_name="Prizm", parallel="Gold", card_number="777"))  # its own thin buckets
        coverage = _engine(obs).coverage()
        assert coverage["exact"] == 1  # only the 3-deep bucket counts, not the 1-deep one
        assert coverage["price_tier"] == 1  # 25_to_100 has 3; 5_to_25 has 1 and doesn't count
        assert set(coverage) == set(comps.LEVEL_NAMES)


class TestMarketSegmentation:
    def _psa10_corpus(self):
        return [
            _ob(p, card_type="graded", grader="PSA", grade="10", **_IDENTITY)
            for p in (480.0, 500.0, 520.0)
        ]

    def test_psa_9_never_lands_in_the_psa_10_bucket(self):
        """Failure mode #3. A PSA 9 must not be valued at PSA 10 money -- the
        only thing left for it is a context-only level, if anything."""
        engine = _engine(self._psa10_corpus())

        psa10 = engine.lookup(player="Caleb Williams", card_type="graded", price=300.0, grader="PSA", grade="10", **_IDENTITY)
        assert psa10.level == "exact"
        assert psa10.stats.median == 500.0

        # Every level segments by the full market key, so a PSA 9 finds
        # nothing at all here rather than a misleading context number.
        psa9 = engine.lookup(player="Caleb Williams", card_type="graded", price=300.0, grader="PSA", grade="9", **_IDENTITY)
        assert psa9 is None

    def test_raw_never_lands_in_the_graded_bucket(self):
        engine = _engine(self._psa10_corpus())
        raw = engine.lookup(player="Caleb Williams", card_type="raw", price=300.0, **_IDENTITY)
        # No level pools raw with graded, so there is simply nothing to say
        # about this card -- which is the correct answer, not a gap.
        assert raw is None

    def test_qualifier_oc_is_its_own_market(self):
        """A PSA 10 (OC) is not a PSA 10, and must not be comped as one."""
        engine = _engine(self._psa10_corpus())
        oc = engine.lookup(
            player="Caleb Williams", card_type="graded", price=300.0,
            grader="PSA", grade="10", qualifier="OC", **_IDENTITY
        )
        assert oc is None

    def test_graded_observations_without_a_grade_do_not_pool(self):
        """Four ungraded-unknown slabs must not become one 'graded' bucket."""
        obs = [_ob(p, card_type="graded", **_IDENTITY) for p in (100.0, 200.0, 300.0)]
        engine = _engine(obs)
        assert engine.coverage()["exact"] == 0
        assert engine.coverage()["same_card"] == 0
        assert engine.coverage()["same_set"] == 0


class TestUnknownNeverMatchesUnknown:
    def test_unknown_parallel_does_not_match_unknown_parallel(self):
        """The real $2.69-vs-$27.99 false positive: parallel=None on both
        sides was treated as a match. Unknown-parallel listings must land at
        same_set, which cannot flag."""
        obs = [_ob(p, year=2026, set_name="Topps Chrome", card_number="150") for p in (25.0, 28.0, 30.0)]
        match = _engine(obs).lookup(
            player="Caleb Williams", card_type="raw", price=2.69,
            year=2026, set_name="Topps Chrome", card_number="150",
        )
        assert match.level == "same_set"
        assert match.flag_eligible is False
        assert "context_only_level" in match.blocked_reasons

    def test_known_parallel_does_not_match_unknown_parallel(self):
        obs = [_ob(p, year=2026, set_name="Topps Chrome", parallel="Gold", card_number="150") for p in (25.0, 28.0, 30.0)]
        match = _engine(obs).lookup(
            player="Caleb Williams", card_type="raw", price=2.69,
            year=2026, set_name="Topps Chrome", card_number="150",
        )
        assert match.level not in comps.FLAG_ELIGIBLE_LEVELS

    def test_unknown_set_never_reaches_a_flag_eligible_level(self):
        obs = [_ob(p, year=2024, parallel="Silver", card_number="123") for p in (95.0, 100.0, 105.0)]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=40.0, year=2024, parallel="Silver", card_number="123")
        assert match is None or match.flag_eligible is False


class TestSelfExclusion:
    def test_exclude_id_removes_the_listing_from_its_own_median(self):
        """Failure mode #4. With the $10 listing in its own bucket the median
        it is judged against is $10 -- itself."""
        obs = [
            _ob(10.0, obs_id="self", **_IDENTITY),
            _ob(10.0, obs_id="a", **_IDENTITY),
            _ob(20.0, obs_id="b", **_IDENTITY),
            _ob(30.0, obs_id="c", **_IDENTITY),
        ]
        engine = _engine(obs)
        included = engine.lookup(player="Caleb Williams", card_type="raw", price=10.0, **_IDENTITY)
        excluded = engine.lookup(player="Caleb Williams", card_type="raw", price=10.0, exclude_id="self", **_IDENTITY)

        assert included.stats.sample_size == 4
        assert included.stats.median == 10.0
        assert excluded.stats.sample_size == 3
        assert excluded.stats.median == 20.0
        assert excluded.stats.minimum == 10.0  # "a" is a different listing at the same price

    def test_self_exclusion_can_drop_a_bucket_below_the_minimum(self):
        obs = [_ob(100.0, obs_id="self", **_IDENTITY), _ob(100.0, obs_id="a", **_IDENTITY), _ob(120.0, obs_id="b", **_IDENTITY)]
        engine = _engine(obs)
        assert engine.lookup(player="Caleb Williams", card_type="raw", price=100.0, **_IDENTITY).level == "exact"
        # Once the listing stops comping against itself there are only 2 left.
        assert engine.lookup(player="Caleb Williams", card_type="raw", price=100.0, exclude_id="self", **_IDENTITY) is None

    def test_exclude_id_only_removes_that_listing(self):
        obs = [_ob(p, obs_id="id-%d" % i, **_IDENTITY) for i, p in enumerate((95.0, 100.0, 105.0, 110.0))]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, exclude_id="not-in-corpus", **_IDENTITY)
        assert match.stats.sample_size == 4


class TestStatistics:
    def test_mad_trim_drops_a_2499_dollar_outlier_from_a_100_dollar_bucket(self):
        """The Murakami case: an unbounded bucket containing one $2,499
        listing produced a $403 'market value' for a $200 card."""
        obs = [_ob(p, **_IDENTITY) for p in (95.0, 98.0, 100.0, 102.0, 105.0, 2499.0)]
        stats = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY).stats
        assert stats.trimmed_count == 1
        assert stats.sample_size == 5
        assert stats.maximum == 105.0
        assert stats.median == 100.0

    def test_no_trimming_below_five_points(self):
        """With four observations, 'outlier' and 'the market' are the same
        thing -- trimming there would be noise, not signal."""
        obs = [_ob(p, **_IDENTITY) for p in (100.0, 100.0, 100.0, 2499.0)]
        stats = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY).stats
        assert stats.trimmed_count == 0
        assert stats.maximum == 2499.0

    def test_zero_mad_does_not_trim_everything(self):
        obs = [_ob(p, **_IDENTITY) for p in (100.0, 100.0, 100.0, 100.0, 100.0, 140.0)]
        stats = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY).stats
        assert stats.trimmed_count == 0
        assert stats.sample_size == 6

    def test_weighted_median_leans_toward_recent_prices(self):
        """Three $100 asks two months ago and three $50 asks today: the
        market is $50 now, and a plain median would say $75."""
        obs = [_ob(100.0, days_ago=60, obs_id="old%d" % i, **_IDENTITY) for i in range(3)]
        obs += [_ob(50.0, days_ago=0, obs_id="new%d" % i, **_IDENTITY) for i in range(3)]
        stats = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=20.0, **_IDENTITY).stats
        assert stats.median == 50.0
        assert stats.mean == 75.0  # unweighted mean is kept as the sanity check

    def test_half_life_is_what_makes_the_median_lean_recent(self):
        """Same three old $100 asks, two new $50 asks. At the 30-day default
        the recent pair outweighs the older trio; with the decay effectively
        switched off the plain majority wins instead."""
        obs = [_ob(100.0, days_ago=60, obs_id="old%d" % i, **_IDENTITY) for i in range(3)]
        obs += [_ob(50.0, days_ago=0, obs_id="new%d" % i, **_IDENTITY) for i in range(2)]

        def median_with(half_life):
            return _engine(obs, half_life_days=half_life).lookup(
                player="Caleb Williams", card_type="raw", price=20.0, **_IDENTITY
            ).stats.median

        assert median_with(30) == 50.0
        assert median_with(100000) == 100.0

    def test_weighted_median_returns_an_observed_price(self):
        assert comps.weighted_median([10.0, 20.0], [1.0, 1.0]) in (10.0, 20.0)

    def test_weighted_median_of_empty_is_an_error_not_a_zero(self):
        with pytest.raises(ValueError):
            comps.weighted_median([], [])

    def test_stats_report_range_and_dates(self):
        obs = [_ob(95.0, days_ago=10, **_IDENTITY), _ob(100.0, days_ago=5, **_IDENTITY), _ob(105.0, days_ago=1, **_IDENTITY)]
        stats = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY).stats
        assert (stats.minimum, stats.maximum) == (95.0, 105.0)
        assert stats.newest_date == _days_ago(1)
        assert stats.oldest_date == _days_ago(10)
        assert (stats.age_days_newest, stats.age_days_oldest) == (1, 10)

    def test_dispersion_is_zero_for_identical_prices(self):
        obs = [_ob(100.0, obs_id="id%d" % i, **_IDENTITY) for i in range(3)]
        stats = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY).stats
        assert stats.dispersion == 0.0
        assert stats.is_dispersed is False

    def test_observations_without_a_usable_date_are_skipped_not_guessed(self):
        obs = [_ob(p, **_IDENTITY) for p in (95.0, 100.0)] + [dict(_ob(105.0, **_IDENTITY), date="whenever")]
        engine = _engine(obs)
        assert engine.skipped_observations() == 1
        assert engine.lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY) is None


class TestQualityGates:
    def test_stale_bucket_is_not_flag_eligible_and_says_so(self):
        # Spread across three dates so staleness is the only thing wrong with
        # it -- an all-on-one-day version would also trip the span gate and
        # stop pinning what this test claims to pin.
        obs = _spread((95.0, 100.0, 105.0), base_days_ago=100, **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.level == "exact"
        assert match.stats.is_stale is True
        assert match.flag_eligible is False
        assert match.blocked_reasons == ("stale_comps",)

    def test_stale_threshold_is_configurable(self):
        obs = _spread((95.0, 100.0, 105.0), base_days_ago=100, **_IDENTITY)
        match = _engine(obs, stale_after_days=365).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.is_stale is False
        assert match.flag_eligible is True

    def test_dispersed_bucket_is_not_flag_eligible_and_says_so(self):
        """$20 / $100 / $300 for 'the same card' means the identity match is
        wrong or the market is chaos. Either way the median means little."""
        obs = _spread((20.0, 100.0, 300.0), **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=15.0, **_IDENTITY)
        assert match.stats.is_dispersed is True
        assert match.flag_eligible is False
        assert match.blocked_reasons == ("dispersed_comps",)

    def test_thin_sample_is_reported_as_a_blocked_reason(self):
        """lookup() will not return a level thinner than min_comps_required,
        so this gate is checked directly -- it is the defence for any caller
        that assembles a CompMatch itself."""
        obs = [_ob(p, **_IDENTITY) for p in (95.0, 100.0)]
        stats = comps.compute_comp_stats(_engine(obs)._buckets["exact"][
            ("Caleb Williams", 2024, "Prizm", "Silver", "123", ("raw",))
        ], today=TODAY)
        match = comps.assess_comp_match(stats, "exact", min_comps_required=3)
        assert match.stats.sample_size == 2
        assert match.flag_eligible is False
        assert "thin_sample" in match.blocked_reasons

    def test_a_clean_recent_exact_bucket_is_flag_eligible(self):
        obs = _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.flag_eligible is True
        assert match.blocked_reasons == ()

    def test_blocked_reasons_accumulate(self):
        obs = _spread((20.0, 100.0, 300.0), base_days_ago=100, **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=15.0, **_IDENTITY)
        assert set(match.blocked_reasons) == {"stale_comps", "dispersed_comps"}


class TestSampleSpan:
    """`distinct_dates` and `span_days` -- how much calendar time a bucket
    actually covers, as opposed to how many rows it has. Measured on the
    kept points, i.e. after self-exclusion and the outlier trim."""

    def _stats(self, points, **kwargs):
        return comps.compute_comp_stats(points, today=TODAY, **kwargs)

    def test_single_day_bucket_has_one_date_and_a_span_of_one(self):
        """A one-day bucket spans one day, not zero -- span counts days
        covered, and a morning's scan covers a day."""
        stats = self._stats([_ob(p, obs_id="id%d" % i, **_IDENTITY) for i, p in enumerate((95.0, 100.0, 105.0))])
        assert stats.sample_size == 3
        assert stats.distinct_dates == 1
        assert stats.span_days == 1

    def test_two_adjacent_days_span_two(self):
        points = [
            _ob(95.0, days_ago=1, obs_id="a", **_IDENTITY),
            _ob(100.0, days_ago=1, obs_id="b", **_IDENTITY),
            _ob(105.0, days_ago=0, obs_id="c", **_IDENTITY),
        ]
        stats = self._stats(points)
        assert (stats.distinct_dates, stats.span_days) == (2, 2)

    def test_span_is_inclusive_of_both_ends(self):
        points = [
            _ob(95.0, days_ago=30, obs_id="a", **_IDENTITY),
            _ob(100.0, days_ago=0, obs_id="b", **_IDENTITY),
        ]
        stats = self._stats(points)
        assert (stats.distinct_dates, stats.span_days) == (2, 31)

    def test_distinct_dates_counts_days_not_observations(self):
        """The whole point of the field: six observations captured on two
        mornings are two readings, and the number must say two."""
        points = [_ob(90.0 + i, days_ago=1 if i < 3 else 0, obs_id="id%d" % i, **_IDENTITY) for i in range(6)]
        stats = self._stats(points)
        assert stats.sample_size == 6
        assert stats.distinct_dates == 2
        assert stats.span_days == 2

    def test_repeated_dates_do_not_inflate_the_count(self):
        points = [_ob(100.0, days_ago=5, obs_id="id%d" % i, **_IDENTITY) for i in range(4)]
        stats = self._stats(points)
        assert (stats.sample_size, stats.distinct_dates, stats.span_days) == (4, 1, 1)

    def test_self_exclusion_can_shrink_the_span(self):
        """The excluded listing is not evidence about itself, so it does not
        get to make its own bucket look well spread either."""
        points = [
            _ob(100.0, days_ago=60, obs_id="self", **_IDENTITY),
            _ob(95.0, days_ago=6, obs_id="a", **_IDENTITY),
            _ob(100.0, days_ago=3, obs_id="b", **_IDENTITY),
            _ob(105.0, days_ago=0, obs_id="c", **_IDENTITY),
        ]
        assert self._stats(points).span_days == 61
        assert self._stats(points).distinct_dates == 4
        trimmed = self._stats(points, exclude_id="self")
        assert (trimmed.distinct_dates, trimmed.span_days) == (3, 7)

    def test_a_trimmed_outlier_does_not_count_toward_the_span(self):
        """A $2,499 listing thrown out of the median must not be left behind
        as evidence that the median was measured over two months."""
        points = _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)
        points.append(_ob(2499.0, days_ago=60, obs_id="outlier", **_IDENTITY))
        stats = self._stats(points)
        assert stats.trimmed_count == 1
        assert (stats.distinct_dates, stats.span_days) == (5, 13)


class TestConcentrationGate:
    """The sample-span gate. `min_comps_required` counts rows; the daily scan
    writes every listing it sees each morning, so rows are not readings."""

    def _same_morning(self, prices=(95.0, 98.0, 100.0, 102.0, 105.0), **fields):
        return [_ob(p, obs_id="id%d" % i, **dict(_IDENTITY, **fields)) for i, p in enumerate(prices)]

    def test_single_day_asking_bucket_is_not_flag_eligible(self):
        """Five asking prices captured in one morning is one snapshot five
        listings deep. Deep enough for min_comps_required, and no basis at
        all for spending money."""
        match = _engine(self._same_morning()).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.level == "exact"
        assert match.stats.sample_size == 5
        assert (match.stats.distinct_dates, match.stats.span_days) == (1, 1)
        assert match.stats.is_concentrated is True
        assert match.flag_eligible is False
        assert "concentrated_sample" in match.blocked_reasons

    def test_the_staleness_gate_does_not_catch_it(self):
        """Why this gate had to exist: everything landed this morning, so the
        newest point is maximally fresh and the bucket is maximally
        un-independent. is_stale is the wrong question."""
        match = _engine(self._same_morning()).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.is_stale is False
        assert match.stats.age_days_newest == 0
        assert match.stats.is_concentrated is True

    def test_the_same_prices_spread_over_time_are_flag_eligible(self):
        """Same five numbers, same depth -- only the dates differ, and that
        is the whole difference between one snapshot and five readings."""
        obs = _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert (match.stats.distinct_dates, match.stats.span_days) == (5, 13)
        assert match.stats.is_concentrated is False
        assert match.flag_eligible is True
        assert match.blocked_reasons == ()

    def test_three_consecutive_mornings_are_still_too_narrow(self):
        """Three distinct dates, but three days is not a market moving -- it
        is one weekend's listings. This is the case a distinct-dates rule
        alone would wave through."""
        obs = _spread((95.0, 100.0, 105.0), step_days=1, **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.distinct_dates == 3
        assert match.stats.span_days == 3
        assert match.flag_eligible is False
        assert "concentrated_sample" in match.blocked_reasons

    def test_one_straggler_does_not_rescue_a_single_morning(self):
        """Five prices this morning plus one from a month ago spans 31 days,
        so a span rule alone would call it healthy. It is two readings, and
        one of them is a single point."""
        obs = self._same_morning()
        obs.append(_ob(100.0, days_ago=30, obs_id="straggler", **_IDENTITY))
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.sample_size == 6
        assert match.stats.span_days == 31  # a span rule alone would pass this
        assert match.stats.distinct_dates == 2
        assert match.flag_eligible is False
        assert "concentrated_sample" in match.blocked_reasons

    def test_concentration_costs_one_step_of_confidence(self):
        concentrated = _engine(self._same_morning()).lookup(
            player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY
        )
        spread = _engine(_spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)).lookup(
            player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY
        )
        assert spread.confidence == "medium"  # exact/high, minus one for asking basis
        assert concentrated.confidence == "low"  # ...minus one more for the span

    def test_both_thresholds_are_configurable(self):
        obs = self._same_morning()
        relaxed = _engine(obs, min_distinct_comp_dates=1, min_comp_span_days=1).lookup(
            player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY
        )
        assert relaxed.stats.is_concentrated is False
        assert relaxed.flag_eligible is True

    def test_raising_the_date_requirement_blocks_a_spread_bucket(self):
        obs = _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)
        match = _engine(obs, min_distinct_comp_dates=6).lookup(
            player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY
        )
        assert match.stats.distinct_dates == 5
        assert match.stats.is_concentrated is True

    def test_raising_the_span_requirement_blocks_a_spread_bucket(self):
        obs = _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)
        match = _engine(obs, min_comp_span_days=30).lookup(
            player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY
        )
        assert match.stats.span_days == 13
        assert match.stats.is_concentrated is True

    # -- sold comps are exempt ------------------------------------------------

    def test_three_sold_comps_on_one_day_are_three_real_transactions(self):
        """The exemption. Three sales on one day are three buyers who each
        agreed a price -- independent evidence that happens to share a date.
        Three asking prices on one day may be one seller's optimism."""
        obs = [_ob(p, obs_id="s%d" % i, basis="sold", **_IDENTITY) for i, p in enumerate((95.0, 100.0, 105.0))]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.basis == "sold"
        assert (match.stats.distinct_dates, match.stats.span_days) == (1, 1)
        assert match.stats.is_concentrated is False
        assert match.flag_eligible is True
        assert "concentrated_sample" not in match.blocked_reasons

    def test_sold_comps_on_one_day_keep_high_confidence(self):
        obs = [_ob(p, obs_id="s%d" % i, basis="sold", **_IDENTITY)
               for i, p in enumerate((95.0, 98.0, 100.0, 102.0, 105.0))]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.confidence == "high"
        assert match.flag_eligible is True

    def test_one_asking_price_removes_the_exemption(self):
        """basis is "sold" only when every kept point is sold. A mixed bucket
        reads as asking, and the asking points in it carry exactly the
        correlation this gate exists to catch."""
        obs = [_ob(p, obs_id="s%d" % i, basis="sold", **_IDENTITY)
               for i, p in enumerate((95.0, 98.0, 100.0, 102.0, 105.0))]
        obs[0]["basis"] = "asking"
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.basis == "asking"
        assert match.stats.is_concentrated is True
        assert match.flag_eligible is False
        assert "concentrated_sample" in match.blocked_reasons

    def test_sold_comps_are_still_subject_to_every_other_gate(self):
        """Only the calendar-spread requirement is lifted. A sale from four
        months ago is still old news."""
        obs = [_ob(p, days_ago=120, obs_id="s%d" % i, basis="sold", **_IDENTITY)
               for i, p in enumerate((95.0, 100.0, 105.0))]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.is_concentrated is False
        assert match.stats.is_stale is True
        assert match.blocked_reasons == ("stale_comps",)

    # -- composition with the other gates -------------------------------------

    def test_every_gate_at_once_reports_every_reason(self):
        """Two prices, wildly apart, from one morning four months ago.
        Checked through assess_comp_match because lookup() will not return a
        bucket thinner than min_comps_required."""
        points = [
            _ob(20.0, days_ago=120, obs_id="a", **_IDENTITY),
            _ob(300.0, days_ago=120, obs_id="b", **_IDENTITY),
        ]
        stats = comps.compute_comp_stats(points, today=TODAY)
        match = comps.assess_comp_match(stats, "exact", min_comps_required=3)
        assert set(match.blocked_reasons) == {
            "thin_sample", "stale_comps", "dispersed_comps", "concentrated_sample",
        }
        assert match.flag_eligible is False
        assert match.confidence == "low"

    def test_a_context_only_level_reports_concentration_too(self):
        obs = [_ob(p, obs_id="id%d" % i) for i, p in enumerate((40.0, 44.0, 50.0))]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=26.0)
        assert match.level == "price_tier"
        assert set(match.blocked_reasons) == {"context_only_level", "concentrated_sample"}

    def test_concentrated_sample_is_never_the_first_reason_listed(self):
        """main.py reports blocked_reasons[0] as the rejection reason, so the
        span gate must not displace a more specific one."""
        obs = [_ob(p, obs_id="id%d" % i) for i, p in enumerate((40.0, 44.0, 50.0))]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=26.0)
        assert match.blocked_reasons[0] == "context_only_level"
        assert match.blocked_reasons[-1] == "concentrated_sample"


class TestConfidence:
    def _five(self, basis, **extra):
        """Five points on five separate dates -- a bucket whose confidence is
        decided by basis and depth, with nothing else wrong with it."""
        return _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, basis=basis, **dict(_IDENTITY, **extra))

    def test_asking_basis_can_never_reach_high(self):
        """Asking prices are what sellers hope for. 100% of this project's
        corpus is asking, so 'medium' is the honest ceiling today."""
        match = _engine(self._five("asking")).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.basis == "asking"
        assert match.confidence == "medium"

    def test_missing_basis_defaults_to_asking(self):
        obs = _spread((95.0, 98.0, 100.0, 102.0, 105.0), step_days=3, **_IDENTITY)
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.basis == "asking"
        assert match.confidence != "high"

    def test_sold_basis_exact_match_reaches_high(self):
        match = _engine(self._five("sold")).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.basis == "sold"
        assert match.confidence == "high"
        assert match.flag_eligible is True

    def test_one_asking_point_makes_the_whole_bucket_asking(self):
        obs = self._five("sold")
        obs[0]["basis"] = "asking"
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.basis == "asking"
        assert match.confidence == "medium"

    def test_thin_sample_downgrades_confidence(self):
        obs = [_ob(p, basis="sold", **_IDENTITY) for p in (95.0, 100.0, 105.0)]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert match.stats.sample_size == 3
        assert match.confidence == "medium"  # high, minus one for n<5

    def test_confidence_floors_at_low(self):
        obs = [_ob(p, days_ago=100, **_IDENTITY) for p in (20.0, 100.0, 300.0)]
        match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=15.0, **_IDENTITY)
        assert match.confidence == "low"

    def test_context_only_levels_are_still_reported_with_a_confidence(self):
        match = _engine([_ob(p) for p in (40.0, 44.0, 50.0)]).lookup(player="Caleb Williams", card_type="raw", price=26.0)
        assert match.confidence == "low"
        assert match.flag_eligible is False


class TestDeterminismAndCaching:
    def test_today_is_injected_never_read_from_the_clock(self):
        obs = [_ob(p, days_ago=50, **_IDENTITY) for p in (95.0, 100.0, 105.0)]
        assert _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY).stats.age_days_newest == 50

    def test_repeated_lookups_return_identical_stats(self):
        obs = [_ob(p, **_IDENTITY) for p in (95.0, 100.0, 105.0)]
        engine = _engine(obs)
        first = engine.lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        second = engine.lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        assert first.stats == second.stats

    def test_cache_is_keyed_by_exclude_id(self):
        obs = [_ob(p, obs_id="id-%s" % p, **_IDENTITY) for p in (95.0, 100.0, 105.0, 110.0)]
        engine = _engine(obs)
        both = engine.lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
        one = engine.lookup(player="Caleb Williams", card_type="raw", price=50.0, exclude_id="id-95.0", **_IDENTITY)
        assert both.stats.sample_size == 4
        assert one.stats.sample_size == 3


class TestRealProductionCorpus:
    """Regression guard against the exact defect this engine was built for:
    replay the live corpus and prove nothing in it can be bought on the
    strength of a circular or identity-blind comp."""

    def _observations(self):
        if not CORPUS_PATH.exists():
            pytest.skip("live corpus %s not present" % CORPUS_PATH)
        history = json.loads(CORPUS_PATH.read_text())
        # Deduped inline (one row per listing id, latest date) rather than via
        # price_history, so this regression guard depends on nothing but the
        # on-disk file and comps.py.
        observations = []
        for key, entries in history.items():
            player, _, card_type = key.partition("|")
            latest = {}
            for i, obs in enumerate(entries):
                obs_id = obs.get("id") or "no-id-%s-%d" % (key, i)
                if obs_id not in latest or obs.get("date", "") >= latest[obs_id].get("date", ""):
                    latest[obs_id] = dict(obs, id=obs_id)
            for obs in latest.values():
                observations.append(dict(obs, player=player, card_type=card_type))
        return observations

    def _matches(self):
        observations = self._observations()
        engine = comps.CompEngine(observations, min_comps_required=3, today=TODAY)
        for obs in observations:
            match = engine.lookup(
                player=obs["player"], card_type=obs["card_type"], price=obs["price"],
                grader=obs.get("grader"), grade=obs.get("grade"), qualifier=obs.get("qualifier"),
                year=obs.get("year"), set_name=obs.get("set_name"),
                parallel=obs.get("parallel"), card_number=obs.get("card_number"),
                exclude_id=obs["id"],
            )
            if match is not None:
                yield obs, match

    def test_corpus_loads_and_produces_some_matches(self):
        assert len(list(self._matches())) > 0

    def test_no_listing_can_be_flagged_from_a_price_tier_or_family_comp(self):
        offenders = [
            (obs["id"], match.level)
            for obs, match in self._matches()
            if match.flag_eligible and match.level in ("price_tier", "family", "same_set")
        ]
        assert offenders == []

    def test_every_flag_eligible_match_is_an_identified_card(self):
        for obs, match in self._matches():
            if match.flag_eligible:
                assert match.level in comps.FLAG_ELIGIBLE_LEVELS

    def test_no_corpus_comp_reaches_high_confidence(self):
        """Every price in this corpus is an asking price."""
        assert not [obs["id"] for obs, match in self._matches() if match.confidence == "high"]

    def test_the_corpus_is_two_calendar_days_of_scanning(self):
        """The premise of the next test, asserted rather than assumed: 563
        observations, 2 dates. Depth without spread."""
        dates = {obs.get("date") for obs in self._observations()}
        assert len(dates) <= 2

    def test_the_two_day_corpus_produces_no_flag_eligible_match(self):
        """Now true for a second, independent reason. Even a bucket that got
        past the identity, market, self-exclusion, staleness and dispersion
        gates cannot get past this one, because every observation in the
        corpus was captured on one of two consecutive mornings."""
        assert [obs["id"] for obs, match in self._matches() if match.flag_eligible] == []

    def test_every_corpus_bucket_is_time_concentrated(self):
        """Not just blocked -- blocked by this gate specifically."""
        matches = list(self._matches())
        assert matches
        assert all(match.stats.is_concentrated for _, match in matches)
        assert all(match.stats.distinct_dates <= 2 for _, match in matches)
        assert all("concentrated_sample" in match.blocked_reasons for _, match in matches)

    def test_coverage_is_reportable_for_the_real_corpus(self):
        engine = comps.CompEngine(self._observations(), min_comps_required=3, today=TODAY)
        coverage = engine.coverage()
        assert set(coverage) == set(comps.LEVEL_NAMES)
        assert all(isinstance(v, int) for v in coverage.values())


def test_year_matches_across_int_and_string_representations():
    """2024 and "2024" are the same year; a type difference must not quietly
    demote a listing to a broader comp level."""
    obs = [_ob(p, **dict(_IDENTITY, year="2024")) for p in (95.0, 100.0, 105.0)]
    match = _engine(obs).lookup(player="Caleb Williams", card_type="raw", price=50.0, **_IDENTITY)
    assert match.level == "exact"


def test_unparseable_year_is_unknown_not_coerced():
    assert comps._clean_year("2023-24 season") is None


def test_compute_comp_stats_works_on_plain_observation_dicts():
    """Usable outside the engine (report debugging), not just on prepared points."""
    stats = comps.compute_comp_stats(
        [_ob(95.0), _ob(100.0), _ob(105.0)], today=TODAY
    )
    assert (stats.sample_size, stats.median, stats.basis) == (3, 100.0, "asking")
