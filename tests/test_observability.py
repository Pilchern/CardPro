from src import observability, reasons


def _stats(**overrides):
    base = dict(
        alert_emails_scanned=14,
        listings_extracted=327,
        listings_matched_to_watchlist=100,
        identity_exact=3,
        identity_partial=70,
        identity_none=27,
        valued=40,
        valued_flag_eligible=2,
        unvalued=60,
        auctions=5,
        fixed_price=40,
        listing_type_unknown=55,
        shipping_known=12,
        shipping_unknown=88,
    )
    base.update(overrides)
    return observability.RunStats(**base)


def test_rates_are_computed_from_matched_listings():
    stats = _stats()
    assert stats.exact_identity_rate == 3.0
    assert stats.comp_coverage_rate == 40.0
    assert stats.flag_eligible_coverage_rate == 2.0
    assert stats.unknown_shipping_rate == 88.0


def test_rates_are_none_rather_than_zero_when_there_is_no_denominator():
    # "0%" printed from an empty denominator reads as a failure when it is
    # actually an absence of input. Those must look different.
    stats = observability.RunStats()
    assert stats.exact_identity_rate is None
    assert stats.comp_coverage_rate is None
    assert stats.unknown_shipping_rate is None
    assert stats.unknown_listing_type_rate is None


def test_health_lines_omit_percentages_when_there_is_no_data():
    lines = "\n".join(observability.RunStats().health_lines())
    assert "%" not in lines


def test_unexplained_count_is_zero_when_everything_is_accounted_for():
    stats = observability.RunStats(listings_matched_to_watchlist=3, opportunities_reported=1)
    stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL)
    stats.rejections.record(reasons.Reason.REPRINT)
    assert stats.unexplained_count() == 0


def test_unexplained_listings_are_called_out_loudly():
    # The whole point of this accounting: a listing that leaves the pipeline
    # with neither an outcome nor a reason is a bug, not a quiet day.
    stats = observability.RunStats(listings_matched_to_watchlist=5, opportunities_reported=1)
    stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL)
    assert stats.unexplained_count() == 3
    footer = "\n".join(stats.health_lines())
    assert "no outcome and no recorded reason" in footer
    assert "that is a bug" in footer


def test_top_reasons_are_labelled_in_english():
    stats = _stats(listings_matched_to_watchlist=2)
    stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL)
    stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL)
    footer = "\n".join(stats.health_lines())
    assert reasons.label(reasons.Reason.NO_COMP_AT_ANY_LEVEL) in footer
    assert "2x" in footer


def test_categories_are_summarised():
    stats = _stats(listings_matched_to_watchlist=2)
    stats.rejections.record(reasons.Reason.REPRINT)
    stats.rejections.record(reasons.Reason.STALE_COMPS)
    footer = "\n".join(stats.health_lines())
    assert "policy" in footer
    assert "data quality" in footer


def test_warnings_are_surfaced_in_the_footer():
    stats = _stats(listings_matched_to_watchlist=0)
    stats.warn("No comp bucket is strong enough to declare a deal from.")
    assert any("No comp bucket" in line for line in stats.health_lines())


def test_health_lines_are_short_enough_to_skim():
    # The footer's job is to be noticed, not read. If it grows past a dozen
    # lines it stops being a footer and starts being a wall of metrics.
    stats = _stats()
    stats.rejections.record(reasons.Reason.NO_COMP_AT_ANY_LEVEL)
    assert len(stats.health_lines()) <= 12
