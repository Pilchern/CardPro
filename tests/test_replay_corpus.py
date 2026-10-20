"""Tests for scripts/replay_corpus.py: the identity-coverage KPI and the
would-flag count (which must be the production deal gate).

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
import json
from datetime import datetime, timezone

import pytest

from eval import run_eval
from src import config as config_module
from src import price_history


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
        "title": "",
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


class TestReextract:
    """Until titles were stored, a change to card_identity.py could only be
    measured against invented examples. --reextract replays the real inputs
    through the current parser."""

    def test_a_stored_title_is_reparsed(self):
        stale = obs(title="2024 Panini Prizm Caleb Williams Silver Prizm RC #301",
                    set_name=None, parallel=None, card_number=None)
        fresh = replay_corpus.reextract([stale])[0]
        assert fresh["set_name"] == "Prizm"
        assert fresh["parallel"] == "Silver Prizm"
        assert fresh["card_number"] == "301"

    def test_a_row_with_no_title_keeps_what_it_was_written_with(self):
        # Its input is gone. Blanking its fields would destroy data to make a
        # number look consistent.
        stale = obs(title="", set_name="Prizm")
        assert replay_corpus.reextract([stale])[0]["set_name"] == "Prizm"

    def test_the_grade_is_reparsed_too(self):
        # A market key is grader plus grade; re-extracting identity while
        # leaving a stale grade behind would build a bucket out of two
        # different parser versions.
        stale = obs(title="2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10",
                    card_type="raw", grader=None, grade=None)
        fresh = replay_corpus.reextract([stale])[0]
        assert (fresh["card_type"], fresh["grader"], fresh["grade"]) == ("graded", "PSA", "10")

    def test_the_variant_fields_are_reparsed_too(self):
        stale = obs(title="2024 Topps Chrome - Rookie Autographs Caleb Williams #RA-CWI /99",
                    is_autograph=False, print_run=None)
        fresh = replay_corpus.reextract([stale])[0]
        assert (fresh["is_autograph"], fresh["print_run"]) == (True, 99)

    def test_price_and_date_are_never_touched(self):
        stale = obs(title="2024 Panini Prizm Caleb Williams #301", price=25.0, date="2026-08-21")
        fresh = replay_corpus.reextract([stale])[0]
        assert (fresh["price"], fresh["date"], fresh["id"]) == (25.0, "2026-08-21", stale["id"])


class TestReextractionDelta:
    """The measurement the extraction work has been missing. Until titles
    were recorded, a change to card_identity.py could be argued for and
    never shown to help."""

    def render(self, stored, refreshed):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            replay_corpus.report_reextraction_delta(stored, refreshed)
        return buffer.getvalue()

    def _pair(self, title, **stored_overrides):
        stored = obs(title=title, **stored_overrides)
        return [stored], replay_corpus.reextract([stored])

    def test_it_reports_what_the_parser_now_finds_that_it_did_not(self):
        stored, refreshed = self._pair(
            "2024 Panini Prizm Caleb Williams Silver Prizm RC #301",
            set_name=None, parallel=None, card_number=None, year=None,
        )
        output = self.render(stored, refreshed)
        assert "set_name" in output
        assert "+1" in output

    def test_a_field_that_changed_value_counts_even_though_coverage_did_not(self):
        # "Chrome" becoming "Topps Chrome" moves a listing between buckets
        # without moving a single coverage percentage.
        stored, refreshed = self._pair(
            "2026 Topps Chrome Kyle Teel Refractor RA-KT", set_name="Chrome",
        )
        assert "1 row(s) (100.0%) would be keyed differently today" in self.render(stored, refreshed)

    def test_an_unchanged_row_is_not_counted_as_changed(self):
        stored, refreshed = self._pair(
            "2024 Panini Prizm Caleb Williams Silver Prizm #301",
            year=2024, set_name="Prizm", parallel="Silver Prizm", card_number="301",
        )
        assert "0 row(s) (0.0%) would be keyed differently" in self.render(stored, refreshed)

    def test_rows_with_no_title_are_left_out_of_the_denominator(self):
        # Counting rows the parser was never given a chance at understates
        # the improvement for no reason.
        titled = obs(title="2024 Panini Prizm Caleb Williams Silver Prizm #301", set_name=None)
        untitled = obs(title="", id="other")
        stored = [titled, untitled]
        assert "from 1 stored title" in self.render(stored, replay_corpus.reextract(stored))

    def test_an_untitled_corpus_says_so_rather_than_dividing_by_zero(self):
        stored = [obs(title="")]
        assert "nothing to re-extract against" in self.render(stored, replay_corpus.reextract(stored))


SILVER = "2024 Panini Prizm Caleb Williams Silver #301 PSA 10"
BLUE = "2024 Panini Prizm Caleb Williams Blue #301 PSA 10"
COMP_DATES = ("2026-08-01", "2026-08-05", "2026-08-09", "2026-08-13", "2026-08-17", "2026-08-20")


def gate_corpus():
    """Two cards, each with six comps spread over three weeks plus one cheap
    listing on 2026-08-21. Silver: $55 against $100 clears the gross rule but
    nets under the floor. Blue: $150 against $400 clears both. One untitled
    row cannot be rebuilt. So gross says 2, production says 1."""
    def row(title, price, row_id, date="2026-08-21"):
        return {"id": row_id, "date": date, "price": price, "title": title,
                "basis": "asking", "listing_type": "fixed_price"}
    rows = [row(title, market, "%s-%d" % (name, i), date)
            for title, name, market in ((SILVER, "s", 100.0), (BLUE, "b", 400.0))
            for i, date in enumerate(COMP_DATES)]
    rows += [row(SILVER, 55.0, "silver-cheap"), row(BLUE, 150.0, "blue-cheap"),
             dict(row("", 20.0, "untitled"), title=None)]
    return {"Caleb Williams|graded": rows}


@pytest.fixture
def shipped_cfg(monkeypatch):
    """The shipped config (config/settings.json), without real credentials,
    and installed as the replay's config so main() never touches os.environ."""
    monkeypatch.setenv("GMAIL_ADDRESS", "replay@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "not-a-real-password")
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    cfg = config_module.load_config()
    monkeypatch.setattr(run_eval, "offline_config", lambda: cfg)
    return cfg


class TestProductionGate:
    """The headline WOULD FLAG count is main.evaluate_listings' decision, not
    the replay's own gross-discount rule. On the live corpus (2026-10-20) the
    gross rule said 40 and production said 2."""

    def _replay(self, cfg):
        observations = replay_corpus.reextract(price_history.deduped_observations(gate_corpus()))
        today = datetime(2026, 8, 21, tzinfo=timezone.utc)
        engine = run_eval.build_engine(cfg, observations, today)
        _, _, _, _, gross = replay_corpus.replay_new(observations, 3, today, engine)
        return gross, replay_corpus.replay_production_gate(observations, engine, cfg)

    def test_clearing_gross_but_not_net_is_not_a_flag(self, shipped_cfg):
        gross, (flagged, stats, skipped) = self._replay(shipped_cfg)
        assert sorted(o["id"] for _, _, _, o in gross) == ["blue-cheap", "silver-cheap"]
        assert [listing.id for listing, _ in flagged] == ["blue-cheap"]
        assert stats.rejections.counts()["below_min_net_profit"] == 1
        assert skipped == 1
        listing, required = flagged[0]
        assert required == pytest.approx(max(shipped_cfg.min_net_profit,
                                             shipped_cfg.min_net_profit_pct_of_value * 400.0))
        assert listing.economics.expected_profit >= required

    def test_the_headline_is_the_production_count(self, shipped_cfg, tmp_path, monkeypatch):
        corpus = tmp_path / "corpus.json"
        corpus.write_text(json.dumps(gate_corpus()))
        monkeypatch.setattr(replay_corpus, "CORPUS", corpus)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            replay_corpus.main(["--reextract"])
        text = out.getvalue()
        assert "  WOULD FLAG: 1  (production gate" in text
        assert "gross-discount only (pre-10-16 gate, 30% and $10 off median): 2" in text
        assert "valued as of: 2026-08-21" in text
        assert "unknown inbound shipping assumed $%.2f" % shipped_cfg.assumed_inbound_shipping in text
        assert "not gated (no title, or title no longer names the player): 1" in text
        required = max(shipped_cfg.min_net_profit, shipped_cfg.min_net_profit_pct_of_value * 400.0)
        assert "(needs ${:>6.2f})".format(required) in text
