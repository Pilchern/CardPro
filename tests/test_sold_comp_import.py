"""The sold-comp store holds the only figures CardPro may call market value.

This parser is what fills it, so its failure mode has to be "refused to
guess", never "quietly imported something wrong". A wrong sold comp is worse
than a missing one: it makes every listing it touches look mispriced with
the engine's full confidence behind it.
"""
from datetime import date

import pytest

from src.sold_comp_import import (
    DATE_FIRST,
    PRICE_FIRST,
    ImportRefused,
    parse_pasted_sales,
)

TODAY = date(2026, 8, 21)

# A tabular results page -- 130point, an auction house, a spreadsheet -- where
# the date is a column to the LEFT of the price, or the line above it.
DATE_FIRST_PAGE = """\
2024 Panini Prizm Caleb Williams Silver Prizm RC #301 PSA 10 GEM MINT
Pre-Owned - Panini
Sold  Aug 15, 2026
$344.00
+$5.99 shipping

Caleb Williams 2024 Prizm Silver RC PSA 10
Sold  Jul 28, 2026
$375.00
Free shipping
"""

# An item-card layout, where the price is printed above the sold date. This is
# the shape the parser inherited from PR #1 could not read: it only ever
# looked backwards for a date, so every price here would have been paired with
# the PREVIOUS row's date and the last one dropped.
PRICE_FIRST_PAGE = """\
2024 Panini Prizm Caleb Williams Silver Prizm RC #301 PSA 10 GEM MINT
Pre-Owned
$344.00
+$5.99 shipping
Sold  Aug 15, 2026

Caleb Williams 2024 Prizm Silver RC PSA 10
Pre-Owned
$375.00
Free shipping
Sold  Jul 28, 2026
"""


class TestRefusal:
    def test_active_listing_page_is_refused_not_silently_empty(self):
        """Pasting an active-search page would put ASKING prices into the one
        store that must contain nothing but real sales."""
        with pytest.raises(ImportRefused, match="no sign of being about SOLD"):
            parse_pasted_sales("Caleb Williams PSA 10\n$425.00\nBuy It Now\n", today=TODAY)

    def test_empty_text_is_refused(self):
        with pytest.raises(ImportRefused):
            parse_pasted_sales("", today=TODAY)

    def test_none_is_refused_rather_than_crashing(self):
        with pytest.raises(ImportRefused):
            parse_pasted_sales(None, today=TODAY)

    def test_auction_house_wording_counts_as_evidence(self):
        sales = parse_pasted_sales("Price realized: $1,250.00 on 2026-08-01\n", today=TODAY)

        assert [(s.date, s.price) for s in sales] == [("2026-08-01", 1250.00)]


class TestPriceExtraction:
    def test_shipping_is_never_read_as_a_sale_price(self):
        sales = parse_pasted_sales(DATE_FIRST_PAGE, today=TODAY)

        assert [s.price for s in sales] == [344.00, 375.00]

    def test_bid_counts_are_not_prices(self):
        sales = parse_pasted_sales("Sold  Aug 18, 2026\n$310.00\n12 bids\n", today=TODAY)

        assert [s.price for s in sales] == [310.00]

    def test_best_offer_lines_are_skipped_as_ambiguous(self):
        """'$399.99 or Best Offer' does not say what it sold for."""
        assert parse_pasted_sales("Sold  Aug 18, 2026\n$399.99 or Best Offer\n", today=TODAY) == []

    def test_a_line_with_two_prices_is_skipped_rather_than_guessed(self):
        assert parse_pasted_sales("Sold  Aug 18, 2026\n$344.00 $5.99\n", today=TODAY) == []

    def test_thousands_separators_parse(self):
        sales = parse_pasted_sales("Sold  Aug 15, 2026\n$1,250.00\n", today=TODAY)

        assert sales[0].price == 1250.00

    def test_a_zero_price_is_not_a_sale(self):
        """sold_comps.validation_error would reject it later; not reading it
        as a sale keeps the count in the preview honest."""
        assert parse_pasted_sales("Sold  Aug 15, 2026\n$0.00\n", today=TODAY) == []

    def test_the_source_line_is_kept_so_a_wrong_row_can_be_found(self):
        sales = parse_pasted_sales("Sold  Aug 15, 2026\n$344.00\n", today=TODAY)

        assert sales[0].source_line == "$344.00"


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
        """A future date makes a stale sale look fresh, and freshness is what
        decides whether a comp counts at all."""
        sales = parse_pasted_sales("Sold  Dec 20\n$100.00\n", today=TODAY)

        assert sales[0].date == "2025-12-20"
        assert sales[0].year_inferred is True

    def test_an_impossible_date_is_not_invented(self):
        assert parse_pasted_sales("Sold  Feb 31, 2026\n$100.00\n", today=TODAY) == []

    def test_a_full_date_is_not_read_by_the_year_less_pattern(self):
        """'Aug 15, 2026' must not be read as 'Aug 15' plus a guessed year --
        the row would still be dated correctly by luck this year and wrongly
        every other year, and would carry a year_inferred warning it does not
        deserve."""
        sales = parse_pasted_sales("Sold  Aug 15, 2025\n$100.00\n", today=TODAY)

        assert (sales[0].date, sales[0].year_inferred) == ("2025-08-15", False)


class TestPairing:
    def test_a_price_far_from_any_date_is_not_paired(self):
        text = "Sold  Aug 15, 2026\n" + "filler\n" * 10 + "$344.00\n"

        assert parse_pasted_sales(text, today=TODAY) == []

    def test_each_date_claims_only_one_price(self):
        sales = parse_pasted_sales("Sold  Aug 15, 2026\n$344.00\n$999.00\n", today=TODAY)

        assert [s.price for s in sales] == [344.00]

    def test_a_date_first_page_parses(self):
        sales = parse_pasted_sales(DATE_FIRST_PAGE, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 344.00),
            ("2026-07-28", 375.00),
        ]

    def test_a_price_first_page_parses_without_shifting_every_row(self):
        """The defect this parser was salvaged with. Pairing backwards only,
        it read ONE sale out of this page and dated it wrongly: $375.00 under
        Aug 15, which is the other row's date. $344.00 was dropped entirely
        and Jul 28 went unused."""
        sales = parse_pasted_sales(PRICE_FIRST_PAGE, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 344.00),
            ("2026-07-28", 375.00),
        ]

    def test_orientation_is_decided_once_for_the_whole_paste(self):
        """Per-row "whichever price is nearer" is what puts a row's date with
        its neighbour's price, because in an evenly spaced list every anchor
        sits the same distance from both."""
        assert _orientation_of(PRICE_FIRST_PAGE) == PRICE_FIRST
        assert _orientation_of(DATE_FIRST_PAGE) == DATE_FIRST

    def test_a_page_that_cannot_say_which_way_round_it_is_refuses(self):
        """Every anchor equidistant from a price on both sides. Guessing
        would file half the page under the wrong row's date, and the
        parser is exactly as unsure here as it is possible to be."""
        text = (
            "$344.00\n"
            "Sold  Aug 15, 2026\n"
            "$375.00\n"
            "Sold  Jul 28, 2026\n"
            "$399.00\n"
        )
        with pytest.raises(ImportRefused, match="Cannot tell whether"):
            parse_pasted_sales(text, today=TODAY)

    def test_a_row_whose_price_was_thrown_out_leaves_a_gap(self):
        """Its date must not be handed to the next row's price."""
        text = (
            "Sold  Aug 15, 2026\n"
            "$399.99 or Best Offer\n"
            "\n"
            "Sold  Jul 28, 2026\n"
            "$375.00\n"
        )
        sales = parse_pasted_sales(text, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [("2026-07-28", 375.00)]

    def test_one_line_carrying_both_parses_under_either_orientation(self):
        text = "Sold  2026-08-15  $344.00\nSold  2026-07-28  $375.00\n"
        sales = parse_pasted_sales(text, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 344.00),
            ("2026-07-28", 375.00),
        ]


class TestThingsThatAreNotSales:
    """Every case in this class was observed importing something wrong, or
    dropping something real, before the pairing was anchored on the evidence
    that a sale happened rather than on line distance."""

    def test_a_month_prefix_inside_a_word_is_not_a_date(self):
        """"mar[a-z]*" made "Dan Marino 13" the 13th of March. A phantom
        date does not merely add a bad row -- it can outvote the real dates
        on orientation and re-date the entire page five months early."""
        page = (
            "1984 Topps Dan Marino 13 Rookie RC PSA 9 MINT\n"
            "Pre-Owned\n"
            "$1,450.00\n"
            "Free shipping\n"
            "Sold  Aug 15, 2026\n"
            "\n"
            "1984 Topps Dan Marino 13 RC PSA 9\n"
            "Pre-Owned\n"
            "$1,299.99\n"
            "Free shipping\n"
            "Sold  Jul 28, 2026\n"
        )
        sales = parse_pasted_sales(page, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 1450.00),
            ("2026-07-28", 1299.99),
        ]

    @pytest.mark.parametrize("line", [
        "1984 Topps Dan Marino 13 Rookie RC PSA 9",
        "1989 Upper Deck 1 Ken Griffey Jr RC PSA 10",
        "2024 Select Caleb Williams Decker 12 SP",
    ])
    def test_titles_that_used_to_parse_as_dates(self, line):
        from src import sold_comp_import

        assert sold_comp_import._extract_date(line, TODAY) is None

    @pytest.mark.parametrize("line,expected", [
        ("Sold  Sept 15, 2026", "2026-09-15"),
        ("Sold  September 15, 2026", "2026-09-15"),
        ("Sold  Mar 3, 2026", "2026-03-03"),
    ])
    def test_real_month_names_still_parse(self, line, expected):
        from src import sold_comp_import

        assert sold_comp_import._extract_date(line, TODAY)[0] == expected

    def test_a_sponsored_active_row_neither_lands_nor_shifts_the_rows_after_it(self):
        """eBay injects sponsored Buy It Now items into sold results. This
        page used to yield the $599 ASKING price carrying the $375 sale's
        date, with the real $375 sale dropped."""
        page = (
            "2024 Prizm Caleb Williams Silver PSA 10\n"
            "$344.00\n"
            "Sold  Aug 15, 2026\n"
            "\n"
            "SPONSORED\n"
            "2024 Prizm Caleb Williams Silver PSA 10 - BUY NOW\n"
            "$599.00\n"
            "Buy It Now\n"
            "\n"
            "2024 Prizm Caleb Williams Silver PSA 10\n"
            "$375.00\n"
            "Sold  Jul 28, 2026\n"
        )
        sales = parse_pasted_sales(page, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 344.00),
            ("2026-07-28", 375.00),
        ]

    def test_an_active_page_is_refused_even_though_it_says_sold_on_every_row(self):
        """eBay prints "26 sold" on active Buy It Now rows, and delivery
        estimates sit exactly where a sold date sits. Together they turned a
        page on which nothing had sold into two sold comps."""
        page = (
            "2024 Panini Prizm Caleb Williams Silver Prizm RC #301 PSA 10 GEM MINT\n"
            "Brand New\n"
            "$425.00\n"
            "Buy It Now\n"
            "Free shipping\n"
            "26 sold\n"
            "Est. delivery Sat, Aug 29\n"
            "\n"
            "2024 Panini Prizm Caleb Williams #301 PSA 10\n"
            "Brand New\n"
            "$449.99\n"
            "Buy It Now\n"
            "26 sold\n"
            "Est. delivery Mon, Sep 1\n"
        )
        with pytest.raises(ImportRefused, match="no sign of being about SOLD"):
            parse_pasted_sales(page, today=TODAY)

    def test_live_auction_bids_are_never_read_as_sales(self):
        """Design principle 5: never confuse a bid with a sale. The bid
        count is on its own line, so the same-line guard never saw it."""
        page = (
            "2024 Prizm Caleb Williams Silver PSA 10\n"
            "$310.00\n"
            "12 bids\n"
            "2d 4h left\n"
            "Est. delivery Sat, Aug 29\n"
            "\n"
            "2024 Prizm Caleb Williams Silver PSA 10\n"
            "$255.00\n"
            "7 bids\n"
            "Est. delivery Fri, Aug 28\n"
        )
        with pytest.raises(ImportRefused):
            parse_pasted_sales(page, today=TODAY)

    def test_a_dollar_figure_in_a_seller_title_is_not_a_sale_price(self):
        """"$1 START NO RESERVE" imported as a $1.00 sold comp, and took the
        first row's date with it -- so the real $344 sale was re-dated to
        the next listing's date and the $375 sale dropped."""
        page = (
            "2024 Prizm Caleb Williams Silver $1 START NO RESERVE PSA 10\n"
            "Pre-Owned\n"
            "$344.00\n"
            "Sold  Aug 15, 2026\n"
            "\n"
            "2024 Prizm Caleb Williams Silver PSA 10\n"
            "Pre-Owned\n"
            "$375.00\n"
            "Sold  Jul 28, 2026\n"
        )
        sales = parse_pasted_sales(page, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 344.00),
            ("2026-07-28", 375.00),
        ]

    @pytest.mark.parametrize("line", ["AU $560.00", "C $475.00", "CA $475.00", "NZ $99.00"])
    def test_a_currency_symbol_is_not_a_currency(self, line):
        """AU$560 is about US$370. Read at face value it inflates the comp
        by half, with the engine's full confidence behind it."""
        from src import sold_comp_import

        assert sold_comp_import._extract_price(line) is None

    @pytest.mark.parametrize("line,expected", [("US $344.00", 344.00), ("$344.00", 344.00)])
    def test_dollars_that_are_dollars_still_parse(self, line, expected):
        from src import sold_comp_import

        assert sold_comp_import._extract_price(line) == expected

    def test_half_a_number_is_not_a_number(self):
        """"$344.5" read as $344.00 (the half-dollar dropped) and the
        European "$1.299,00" as $1.29."""
        from src import sold_comp_import

        assert sold_comp_import._extract_price("$344.5") == 344.5
        assert sold_comp_import._extract_price("$1.299,00") is None

    def test_a_tabular_page_whose_header_says_sold_once_still_parses(self):
        """130point -- the source this module's docstrings point at -- puts
        the sale wording in a column header, not on every row."""
        page = (
            "Date Sold        Price\n"
            "2026-08-15       $344.00\n"
            "2026-07-28       $375.00\n"
        )
        sales = parse_pasted_sales(page, today=TODAY)

        assert [(s.date, s.price) for s in sales] == [
            ("2026-08-15", 344.00),
            ("2026-07-28", 375.00),
        ]

    @pytest.mark.parametrize("value", [123, b"text", [], {}])
    def test_something_that_is_not_text_refuses_rather_than_crashing(self, value):
        with pytest.raises(ImportRefused):
            parse_pasted_sales(value, today=TODAY)


def _orientation_of(text):
    """What _orientation() makes of this page, with its inputs built the way
    the parser builds them so the test cannot drift from it."""
    from src import sold_comp_import as module

    prices, dates = [], []
    for index, line in enumerate(line.strip() for line in text.splitlines()):
        if not line:
            continue
        found = module._extract_date(line, TODAY)
        if found is not None and not module.NOT_A_SALE_DATE_RE.search(line):
            dates.append(module._Event(index, line, found))
        price = module._extract_price(line)
        if price is not None:
            prices.append(module._Event(index, line, price, module._looks_like_only_a_price(line)))
    return module._orientation(module._anchors(dates), prices)
