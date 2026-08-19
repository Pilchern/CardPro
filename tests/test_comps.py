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
