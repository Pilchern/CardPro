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
