"""The sold-comp store holds the only figures CardPro may call market
value. This parser is what fills it, so its failure mode must be "refused
to guess", never "quietly imported something wrong"."""
from datetime import date

import pytest

from src import sold_comp_import
from src.sold_comp_import import ImportRefused, parse_pasted_sales

TODAY = date(2026, 8, 21)

EBAY_SOLD = """\
2024 Panini Prizm Caleb Williams Silver Prizm RC #301 PSA 10 GEM MINT
Pre-Owned · Panini
Sold  Aug 15, 2026
$344.00
+$5.99 shipping

Caleb Williams 2024 Prizm Silver RC PSA 10
Sold  Jul 28, 2026
$375.00
Free shipping
"""


class TestRefusal:
    def test_active_listing_page_is_refused_not_silently_empty(self):
        """Pasting an active-search page would put ASKING prices into the
        one store that must contain nothing but real sales."""
        with pytest.raises(ImportRefused, match="no sign of being about SOLD"):
            parse_pasted_sales("Caleb Williams PSA 10\n$425.00\nBuy It Now\n", today=TODAY)

    def test_empty_text_is_refused(self):
        with pytest.raises(ImportRefused):
            parse_pasted_sales("", today=TODAY)

    def test_auction_house_wording_counts_as_evidence(self):
        sales = parse_pasted_sales("Price realized: $1,250.00 on 2026-08-01\n", today=TODAY)

        assert len(sales) == 1


class TestPriceExtraction:
    def test_shipping_is_never_read_as_a_sale_price(self):
        sales = parse_pasted_sales(EBAY_SOLD, today=TODAY)

        assert [s.price for s in sales] == [344.00, 375.00]

    def test_bid_counts_are_not_prices(self):
        text = "Sold  Aug 18, 2026\n$310.00\n12 bids\n"
        sales = parse_pasted_sales(text, today=TODAY)

        assert [s.price for s in sales] == [310.00]

    def test_best_offer_lines_are_skipped_as_ambiguous(self):
        """'$399.99 or Best Offer' doesn't say what it sold for."""
        text = "Sold  Aug 18, 2026\n$399.99 or Best Offer\n"

        assert parse_pasted_sales(text, today=TODAY) == []

    def test_a_line_with_two_prices_is_skipped_rather_than_guessed(self):
        text = "Sold  Aug 18, 2026\n$344.00 $5.99\n"

        assert parse_pasted_sales(text, today=TODAY) == []

    def test_thousands_separators_parse(self):
        sales = parse_pasted_sales("Sold  Aug 15, 2026\n$1,250.00\n", today=TODAY)

        assert sales[0].price == 1250.00


class TestDateExtraction:
    @pytest.mark.parametrize("line,expected", [
        ("Sold  2026-08-15", "2026-08-15"),
        ("Sold  Aug 15, 2026", "2026-08-15"),
        ("Sold  August 15 2026", "2026-08-15"),
        ("Sold  15 Aug 2026", "2026-08-15"),
        ("Sold  08/15/2026", "2026-08-15"),
    ])
    def test_common_date_shapes(self, line, expected):
        sales = parse_pasted_sales(f"{line}\n$100.00\n", today=TODAY)

        assert sales[0].date == expected
        assert sales[0].year_inferred is False

    def test_missing_year_assumes_this_year_when_that_is_past(self):
        sales = parse_pasted_sales("Sold  Aug 18\n$100.00\n", today=TODAY)

        assert sales[0].date == "2026-08-18"
        assert sales[0].year_inferred is True

    def test_missing_year_steps_back_rather_than_dating_a_sale_in_the_future(self):
        """A future date would make a stale sale look fresh, and freshness
        is what decides whether it counts at all."""
        sales = parse_pasted_sales("Sold  Dec 20\n$100.00\n", today=TODAY)

        assert sales[0].date == "2025-12-20"
        assert sales[0].year_inferred is True

    def test_an_impossible_date_is_not_invented(self):
        assert parse_pasted_sales("Sold  Feb 31, 2026\n$100.00\n", today=TODAY) == []


class TestPairing:
    def test_a_price_far_from_any_date_is_not_paired(self):
        """A wide window starts pairing one listing's date with the next
        listing's price."""
        text = "Sold  Aug 15, 2026\n" + "filler\n" * 10 + "$344.00\n"

        assert parse_pasted_sales(text, today=TODAY) == []

    def test_each_date_claims_only_one_price(self):
        text = "Sold  Aug 15, 2026\n$344.00\n$999.00\n"
        sales = parse_pasted_sales(text, today=TODAY)

        assert [s.price for s in sales] == [344.00]

    def test_multiple_listings_parse_independently(self):
        sales = parse_pasted_sales(EBAY_SOLD, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [("2026-08-15", 344.00), ("2026-07-28", 375.00)]
