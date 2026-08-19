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
