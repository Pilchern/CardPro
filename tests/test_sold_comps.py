"""Hand-entered sold prices are the only real market data CardPro has.

They deliberately have no valuation path of their own: they arrive as
CompEngine observations marked basis="sold", and the existing engine does
the segmenting, self-exclusion, trimming and weighting. So these tests
cover the loading contract and prove the basis actually reaches the engine
-- market segmentation and recency are CompEngine's tests, not these."""
import json
from datetime import datetime

import pytest

from src import comps, sold_comps

CARD = dict(player="Caleb Williams", year=2024, set_name="Prizm", parallel="Silver",
            card_number="301", grader="PSA", grade="10")


def _write(tmp_path, comps_list):
    path = tmp_path / "sold_comps.json"
    path.write_text(json.dumps({"comps": comps_list}))
    return path


def _entry(sales, **overrides):
    return dict(CARD, sales=sales, **overrides)


class TestLoading:
    def test_missing_file_is_the_normal_starting_state(self, tmp_path):
        assert sold_comps.load_observations(tmp_path / "nope.json") == []

    def test_malformed_file_raises_rather_than_degrading_every_valuation(self, tmp_path):
        """A typo must not quietly drop the whole system back to
        asking-price-only with no warning."""
        path = tmp_path / "sold_comps.json"
        path.write_text("{not json")

        with pytest.raises(json.JSONDecodeError):
            sold_comps.load_observations(path)

    def test_a_sale_without_price_or_date_is_skipped(self, tmp_path):
        path = _write(tmp_path, [_entry([{"price": 300.0}, {"price": 344.0, "date": "2026-08-01"}])])

        assert len(sold_comps.load_observations(path)) == 1

    def test_every_observation_is_marked_sold(self, tmp_path):
        """The whole integration rests on this one field."""
        path = _write(tmp_path, [_entry([{"price": 344.0, "date": "2026-08-01"}])])

        assert all(o["basis"] == comps.BASIS_SOLD for o in sold_comps.load_observations(path))

    def test_card_identity_is_carried_through_for_segmentation(self, tmp_path):
        path = _write(tmp_path, [_entry([{"price": 344.0, "date": "2026-08-01"}])])
        obs = sold_comps.load_observations(path)[0]

        assert (obs["player"], obs["year"], obs["set_name"], obs["parallel"]) == \
               ("Caleb Williams", 2024, "Prizm", "Silver")
        assert (obs["grader"], obs["grade"], obs["card_type"]) == ("PSA", "10", "graded")

    def test_a_card_with_no_grader_is_raw(self, tmp_path):
        path = _write(tmp_path, [_entry([{"price": 40.0, "date": "2026-08-01"}], grader=None, grade=None)])

        assert sold_comps.load_observations(path)[0]["card_type"] == "raw"

    def test_ids_cannot_collide_with_a_listing_id(self, tmp_path):
        """Self-exclusion drops observations by id -- a collision would
        silently delete a hand-entered sale from its own comp."""
        path = _write(tmp_path, [_entry([{"price": 344.0, "date": "2026-08-01"},
                                         {"price": 350.0, "date": "2026-08-02"}])])
        ids = [o["id"] for o in sold_comps.load_observations(path)]

        assert all(i.startswith("soldcomp:") for i in ids)
        assert len(set(ids)) == len(ids)


class TestReachesTheEngine:
    def test_a_sold_only_bucket_reports_basis_sold(self, tmp_path):
        """CompEngine calls a comp "sold" only when every kept point is sold.
        This proves hand-entered sales actually satisfy that."""
        sales = [{"price": p, "date": "2026-08-01"} for p in (320.0, 340.0, 344.0, 350.0, 375.0)]
        observations = sold_comps.load_observations(_write(tmp_path, [_entry(sales)]))

        engine = comps.CompEngine(observations, min_comps_required=3, today=datetime(2026, 8, 15))
        match = engine.lookup(
            player="Caleb Williams", card_type="graded", price=239.0,
            grader="PSA", grade="10", year=2024, set_name="Prizm",
            parallel="Silver", card_number="301",
        )

        assert match is not None
        assert match.stats.basis == comps.BASIS_SOLD

    def test_mixing_in_an_asking_observation_drops_the_basis_back(self, tmp_path):
        """One asking price in the bucket means the comp is no longer a
        statement about what the card sells for. The asking price here sits
        inside the range on purpose -- an outlier would be trimmed out and
        the bucket would stay (correctly) all-sold."""
        sales = [{"price": p, "date": "2026-08-01"} for p in (320.0, 340.0, 344.0, 350.0)]
        observations = sold_comps.load_observations(_write(tmp_path, [_entry(sales)]))
        observations.append({
            "id": "listing:1", "player": "Caleb Williams", "card_type": "graded",
            "grader": "PSA", "grade": "10", "year": 2024, "set_name": "Prizm",
            "parallel": "Silver", "card_number": "301", "price": 345.0, "date": "2026-08-02",
        })

        engine = comps.CompEngine(observations, min_comps_required=3, today=datetime(2026, 8, 15))
        match = engine.lookup(
            player="Caleb Williams", card_type="graded", price=239.0,
            grader="PSA", grade="10", year=2024, set_name="Prizm",
            parallel="Silver", card_number="301",
        )

        assert match.stats.basis == comps.BASIS_ASKING


class TestConfidenceForEntryScripts:
    def test_a_single_sale_is_low_not_settled_fact(self):
        assert sold_comps.confidence_for(1) == "low"

    def test_confidence_rises_with_sample_size(self):
        assert sold_comps.confidence_for(3) == "medium"
        assert sold_comps.confidence_for(5) == "high"
