"""Tests for the identity-coverage KPI in scripts/replay_corpus.py.

Deliberately built on synthetic observations rather than the real corpus in
data/. That file is rewritten by the daily production run, so any test
asserting a number about it is a test that fails on a Tuesday for reasons
that have nothing to do with the code (see commit "Stop the corpus tests
asserting facts about a file that changes daily").
"""
from __future__ import annotations

import io
import contextlib

from scripts import replay_corpus


def obs(**overrides):
    """One corpus observation, complete by default so each test can knock
    out exactly the field it is about."""
    base = {
        "player": "Caleb Williams",
        "card_type": "raw",
        "price": 25.0,
        "date": "2026-08-21",
        "id": "https://www.ebay.com/itm/1",
        "year": 2024,
        "set_name": "Prizm",
        "parallel": "Silver",
        "card_number": "312",
        "grader": None,
        "grade": None,
        "qualifier": None,
        "basis": "asking",
    }
    base.update(overrides)
    return base


class TestResolved:
    def test_present_value_is_resolved(self):
        assert replay_corpus._resolved(obs(), "set_name") is True

    def test_none_is_not_resolved(self):
        assert replay_corpus._resolved(obs(set_name=None), "set_name") is False

    def test_missing_key_is_not_resolved(self):
        assert replay_corpus._resolved({}, "set_name") is False

    def test_blank_string_is_not_resolved(self):
        # "" is how an extractor reports "I found nothing", and it must not
        # be allowed to key a comp bucket as though it were a real set.
        assert replay_corpus._resolved(obs(set_name="   "), "set_name") is False

    def test_zero_is_resolved(self):
        # Not currently reachable for these fields, but "0" must never be
        # mistaken for absence -- that class of bug silently drops data.
        assert replay_corpus._resolved(obs(card_number="0"), "card_number") is True


class TestFirstMissing:
    fields = replay_corpus.LEVEL_REQUIREMENTS["same_card"]

    def test_complete_key_has_no_blocker(self):
        assert replay_corpus._first_missing(obs(), self.fields) is None

    def test_reports_the_narrowest_missing_field(self):
        # set_name comes before parallel in the key, so a listing missing
        # both is attributed to set_name -- fixing parallel alone would move
        # it nowhere.
        blocked = obs(set_name=None, parallel=None)
        assert replay_corpus._first_missing(blocked, self.fields) == "set_name"

    def test_year_outranks_set_name(self):
        assert replay_corpus._first_missing(obs(year=None, set_name=None), self.fields) == "year"

    def test_card_number_only_blocks_at_exact(self):
        no_number = obs(card_number=None)
        assert replay_corpus._first_missing(no_number, self.fields) is None
        assert (
            replay_corpus._first_missing(no_number, replay_corpus.LEVEL_REQUIREMENTS["exact"])
            == "card_number"
        )


class TestReportIdentityCoverage:
    def render(self, observations, min_comps=3):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            replay_corpus.report_identity_coverage(observations, min_comps)
        return buffer.getvalue()

    def test_empty_corpus_does_not_divide_by_zero(self):
        assert "(no observations)" in self.render([])

    def test_counts_a_complete_key_at_every_level(self):
        output = self.render([obs()])
        assert "exact            1  100.0%" in output
        assert "same_card        1  100.0%" in output

    def test_names_the_dominant_blocker(self):
        # The whole point of the KPI: turn "identity coverage is low" into
        # "set_name is what to go and fix".
        observations = [obs(id=str(i), set_name=None) for i in range(9)] + [obs(id="x")]
        output = self.render(observations)
        assert "first blocker" in output
        assert "set_name" in output and "9   90.0%" in output

    def test_a_lone_complete_key_is_not_a_usable_bucket(self):
        # A key nothing else shares values nothing. Reporting it as coverage
        # would be the same self-flattery the price-tier level committed.
        output = self.render([obs()])
        assert "same_card: 1 distinct keys, 0 observations (0.0%)" in output

    def test_bucket_needs_more_than_min_comps_members(self):
        # min_comps=1 means "one OTHER listing", so two sharing a key is the
        # first depth that counts.
        shared = [obs(id=str(i)) for i in range(2)]
        assert "same_card: 1 distinct keys, 2 observations (100.0%)" in self.render(shared, min_comps=1)

    def test_distinct_grades_are_distinct_buckets(self):
        # A PSA 9 and a PSA 10 of the same card are different markets; if
        # this KPI pooled them it would overstate the achievable ceiling.
        graded = [
            obs(id="a", card_type="graded", grader="PSA", grade="9"),
            obs(id="b", card_type="graded", grader="PSA", grade="10"),
        ]
        assert "same_card: 2 distinct keys" in self.render(graded)

    def test_unusable_market_is_excluded_not_crashed(self):
        assert "same_card: 0 distinct keys" in self.render([obs(card_type=None)])
