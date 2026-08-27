"""Tests for the "go and look these up" suggestions.

The suggestion is advice about where to spend scarce human effort, so the
things worth testing are: does it rank by what it claims to rank by, does it
stop asking once a card is done, and does it refuse to suggest a lookup that
could not possibly help.
"""
from __future__ import annotations

from src import card_identity, comp_requests, matcher
from src.models import Listing


def listing(title, listing_id="1", player="Caleb Williams", price=100.0):
    grade_info = matcher.detect_grade_details(title)
    return Listing(
        id=listing_id,
        source="ebay",
        title=title,
        price=price,
        url="https://www.ebay.com/itm/" + listing_id,
        player=player,
        card_type=grade_info.card_type,
        grader=grade_info.grader,
        grade=grade_info.grade,
        qualifier=grade_info.qualifier,
        card_identity=card_identity.extract_card_identity(title),
    )


def sold(player="Caleb Williams", year=2024, set_name="Prizm", parallel="Silver Prizm",
         card_type="raw", grader=None, grade=None, qualifier=None):
    return {
        "player": player, "year": year, "set_name": set_name, "parallel": parallel,
        "card_type": card_type, "grader": grader, "grade": grade, "qualifier": qualifier,
        "price": 300.0, "date": "2026-08-01", "basis": "sold",
    }


RAW_PRIZM = "2024 Panini Prizm Caleb Williams Silver Prizm RC #301"
PSA10_PRIZM = "2024 Panini Prizm Caleb Williams Silver Prizm RC #301 PSA 10"


class TestWhatCountsAsAskable:
    def test_a_resolved_identity_seen_twice_is_suggested(self):
        requests = comp_requests.build_requests(
            [listing(RAW_PRIZM, "1"), listing(RAW_PRIZM, "2")], []
        )
        assert len(requests) == 1
        assert requests[0].listings_waiting == 2

    def test_a_one_off_is_not_worth_a_lookup(self):
        assert comp_requests.build_requests([listing(RAW_PRIZM, "1")], []) == []

    def test_an_unresolvable_listing_is_never_suggested(self):
        # No sold comp can be matched to a card we cannot identify, so
        # suggesting one would be asking for work that cannot pay off.
        vague = [listing("Caleb Williams rookie card lot look", str(i)) for i in range(4)]
        assert comp_requests.build_requests(vague, []) == []

    def test_unidentified_listings_are_counted_not_hidden(self):
        vague = [listing("Caleb Williams rookie card", str(i)) for i in range(3)]
        assert comp_requests.unidentified_count(vague) == 3

    def test_a_graded_card_with_no_readable_grade_has_no_market(self):
        # market_key returns None, and a request keyed on an unknown market
        # would pool every slab together -- the exact defect the engine
        # exists to prevent.
        listings = [listing(RAW_PRIZM, str(i)) for i in range(2)]
        for item in listings:
            item.card_type = "graded"
            item.grader = None
            item.grade = None
        assert comp_requests.build_requests(listings, []) == []


class TestMarketSegmentation:
    def test_raw_and_graded_are_separate_requests(self):
        listings = [listing(RAW_PRIZM, "1"), listing(RAW_PRIZM, "2"),
                    listing(PSA10_PRIZM, "3"), listing(PSA10_PRIZM, "4")]
        requests = comp_requests.build_requests(listings, [])
        assert {r.market_label for r in requests} == {"raw", "PSA 10"}

    def test_a_sold_comp_for_the_wrong_grade_does_not_count(self):
        listings = [listing(PSA10_PRIZM, "1"), listing(PSA10_PRIZM, "2")]
        psa9 = sold(card_type="graded", grader="PSA", grade="9")
        requests = comp_requests.build_requests(listings, [psa9] * 3)
        assert len(requests) == 1
        assert requests[0].sold_on_file == 0

    def test_a_qualifier_is_its_own_market(self):
        assert comp_requests.CompRequest(
            player="X", year=2024, set_name="Prizm", parallel="Silver",
            market=("graded", "PSA", "8", "OC"), listings_waiting=2, sold_on_file=0,
            still_needed=3, example_url=None,
        ).market_label == "PSA 8 OC"


class TestStopsAskingWhenDone:
    def test_an_identity_with_enough_sold_comps_drops_off(self):
        listings = [listing(RAW_PRIZM, "1"), listing(RAW_PRIZM, "2")]
        assert comp_requests.build_requests(listings, [sold()] * 3, min_comps_required=3) == []

    def test_partial_progress_shortens_the_ask(self):
        listings = [listing(RAW_PRIZM, "1"), listing(RAW_PRIZM, "2")]
        requests = comp_requests.build_requests(listings, [sold()], min_comps_required=3)
        assert (requests[0].sold_on_file, requests[0].still_needed) == (1, 2)


class TestRanking:
    def test_the_identity_blocking_the_most_listings_comes_first(self):
        listings = [listing(RAW_PRIZM, str(i)) for i in range(2)]
        listings += [listing(PSA10_PRIZM, "g%d" % i) for i in range(5)]
        requests = comp_requests.build_requests(listings, [])
        assert requests[0].market_label == "PSA 10"

    def test_ties_break_toward_the_cheaper_ask(self):
        listings = [listing(RAW_PRIZM, str(i)) for i in range(2)]
        listings += [listing(PSA10_PRIZM, "g%d" % i) for i in range(2)]
        requests = comp_requests.build_requests(listings, [sold()] * 2)
        assert requests[0].market_label == "raw"  # needs 1 more, the slab needs 3

    def test_output_is_capped(self):
        listings = []
        for n in range(8):
            title = "202%d Panini Prizm Caleb Williams Silver Prizm RC #30%d" % (n % 5, n)
            listings += [listing(title, "%d-%d" % (n, i)) for i in range(2)]
        assert len(comp_requests.build_requests(listings, [], limit=3)) == 3

    def test_ordering_is_stable_not_dict_ordered(self):
        listings = [listing(RAW_PRIZM, str(i)) for i in range(2)]
        listings += [listing(PSA10_PRIZM, "g%d" % i) for i in range(2)]
        first = comp_requests.build_requests(listings, [])
        second = comp_requests.build_requests(list(reversed(listings)), [])
        assert [r.market_label for r in first] == [r.market_label for r in second]


class TestSearchQuery:
    def test_a_slab_query_carries_the_grade(self):
        requests = comp_requests.build_requests([listing(PSA10_PRIZM, str(i)) for i in range(2)], [])
        assert requests[0].search_query == "2024 Prizm Caleb Williams Silver Prizm PSA 10"

    def test_a_raw_query_does_not_say_raw(self):
        # "raw" is our internal word for "not in a slab"; typing it into a
        # sold-listing search returns nothing.
        requests = comp_requests.build_requests([listing(RAW_PRIZM, str(i)) for i in range(2)], [])
        assert "raw" not in requests[0].search_query.lower()
        assert requests[0].search_query == "2024 Prizm Caleb Williams Silver Prizm"

    def test_an_example_listing_is_carried_through(self):
        requests = comp_requests.build_requests([listing(RAW_PRIZM, str(i)) for i in range(2)], [])
        assert requests[0].example_url == "https://www.ebay.com/itm/0"


class TestAddCommand:
    """A suggestion that ends in "now go and figure out the syntax" is a
    suggestion nobody acts on twice."""

    def _request(self, **overrides):
        defaults = dict(
            player="Caleb Williams", year=2024, set_name="Prizm", parallel="Silver Prizm",
            market=("graded", "PSA", "10", None), listings_waiting=6, sold_on_file=1,
            still_needed=2, example_url=None,
        )
        defaults.update(overrides)
        return comp_requests.CompRequest(**defaults)

    def test_everything_already_known_is_filled_in(self):
        command = self._request().add_command
        assert '--player "Caleb Williams"' in command
        assert "--year 2024" in command
        assert '--set "Prizm"' in command
        assert '--parallel "Silver Prizm"' in command
        assert "--grader PSA --grade 10" in command

    def test_only_the_two_facts_the_lookup_produces_are_left_blank(self):
        assert self._request().add_command.endswith("--price ? --date ?")

    def test_a_raw_card_asks_for_no_grade(self):
        command = self._request(market=("raw",)).add_command
        assert "--grader" not in command and "--grade" not in command

    def test_a_qualifier_is_carried_through(self):
        # A PSA 8 OC is a different market; a comp entered without it would
        # be filed against the wrong bucket.
        assert "--qualifier OC" in self._request(market=("graded", "PSA", "8", "OC")).add_command

    def test_the_identity_matches_what_the_engine_keys_on(self):
        # The whole point. Hand-typing "Silver" where the extractor said
        # "Silver Prizm" produces a comp that silently never matches, which
        # is worse than not entering it -- it looks like progress.
        request = self._request()
        for value in (request.set_name, request.parallel):
            assert '"{}"'.format(value) in request.add_command
