"""The HTML email.

Two things are worth testing about a renderer and neither of them is
typography. The first is that it says exactly what the plain-text email
says: the two parts of a multipart message are supposed to be the same
report, and a reader has no way to tell which half is wrong if they differ.
The second is that seller-authored text cannot escape into markup.

The layout itself is not asserted here beyond the handful of rules that
broke once and would break silently again -- the dark-mode background in
particular, which failed while every colour in the email was correct.
"""
from __future__ import annotations

import re
from datetime import date

from src import card_identity, report, report_html
from tests.test_report import make_listing, make_target_hit

RUN_DATE = date(2026, 8, 30)

AUTO_TITLE = "2024 Panini Prizm Caleb Williams Auto RC #301 /99"


def build(deals, **kwargs):
    kwargs.setdefault("min_savings_dollars", 3)
    return report.build_model(deals, 30, RUN_DATE, None, True, kwargs.pop("min_savings_dollars"),
                              **kwargs)


def html_of(deals, **kwargs):
    return report_html.render(build(deals, **kwargs))


def browse_listing(**overrides):
    """A card with no comp -- the ordinary case in this project's data, and
    the one that lands in the no-valuation-claim sections."""
    defaults = dict(
        is_opportunity=False, comp_match=None, market_value=None, pct_under_market=None,
        dollar_savings=None, title=AUTO_TITLE,
        card_identity=card_identity.extract_card_identity(AUTO_TITLE),
        is_rookie_card=True, price=60.0, card_type="raw", grader=None, grade=None,
    )
    defaults.update(overrides)
    return make_listing(**defaults)


def strip_tags(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# ---------------------------------------------------------------------------
# the two emails say the same thing
# ---------------------------------------------------------------------------


def test_every_field_the_text_email_prints_is_in_the_html_email():
    # The guarantee the whole build_model/render split exists to provide. A
    # renderer that quietly dropped a Risks line would leave one half of a
    # multipart email making a claim the other half qualifies.
    #
    # Asserted against each card's OWN block rather than the whole document.
    # Against the document it passed while the renderer dropped every card's
    # last field: "Risks" is a word in the ACT NOW subtitle, and the shipping
    # caveat it carries is repeated verbatim in the Cost line above it. A
    # test that a broken renderer passes is worse than no test.
    model = build([
        make_listing(id="act", dollar_savings=400.0, pct_under_market=60.0),
        make_listing(id="auction", listing_type="auction", is_opportunity=False,
                     bid_count=2, time_left_text="0d 05h", max_rational_bid=150.0),
        browse_listing(id="cool"),
    ])
    seen_labels = set()
    for key, cards in model.sections.items():
        for card in cards:
            block = strip_tags(report_html.render_card(card, "#000000"))
            assert card.fields
            for label, value in card.fields:
                seen_labels.add(label)
                assert label.upper() in block.upper(), (key, label)
                assert " ".join(value.split()) in block, (key, label)
            assert card.player in block
            if card.title is not None:
                assert " ".join(card.title.split()) in block
    # The fixture has to be wide enough for the assertion above to mean
    # something: three block types, and the fields they do not share.
    assert {"Cost", "Current bid", "Max bid", "What it is", "Risks"} <= seen_labels


def test_the_html_carries_every_section_the_model_holds():
    model = build([
        make_listing(id="act", dollar_savings=400.0, pct_under_market=60.0),
        browse_listing(id="cool"),
    ])
    text = strip_tags(report_html.render(model))
    assert model.sections
    for key in model.sections:
        assert report.SECTION_TITLES[key] in text


def test_the_subject_is_the_models_subject_not_a_second_opinion():
    model = build([browse_listing(id="cool")])
    assert "<title>{}</title>".format(model.subject) in report_html.render(model)


def test_the_card_link_is_a_real_link_to_the_listing():
    deal = browse_listing(id="cool", url="https://www.ebay.com/itm/12345")
    assert 'href="https://www.ebay.com/itm/12345"' in html_of([deal])


def test_a_target_hit_keeps_its_label():
    deal = make_listing(id="target", is_opportunity=False, pct_under_market=1.0,
                        dollar_savings=2.0, target_hit=make_target_hit())
    assert "TARGET:" in strip_tags(html_of([deal]))


def test_the_not_configured_email_still_renders():
    model = report.build_model([], 30, RUN_DATE, None, False, 3)
    text = strip_tags(report_html.render(model))
    assert "eBay wasn't scanned today" in text


# ---------------------------------------------------------------------------
# seller text cannot become markup
# ---------------------------------------------------------------------------


def test_a_listing_title_containing_markup_is_escaped():
    # eBay titles are written by strangers. This is the one defect in a
    # renderer that is not merely ugly.
    nasty = 'Pippen <script>alert(1)</script> "lot of 3" & more'
    deal = browse_listing(id="x", title=nasty,
                          card_identity=card_identity.extract_card_identity(nasty))
    html = html_of([deal])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert strip_tags(html).count("alert(1)") >= 1


def test_a_player_name_containing_markup_is_escaped():
    deal = browse_listing(id="x", player="<b>Caleb</b>")
    html = html_of([deal])
    assert "<b>Caleb</b>" not in html
    assert "&lt;b&gt;Caleb&lt;/b&gt;" in html


def test_linkify_only_ever_runs_on_already_escaped_text():
    # It inserts markup, so it must not be reachable with raw input. The
    # contract is enforced by ordering in the helpers; this pins it.
    escaped = report_html.esc('see <https://x.test/a> "now"')
    linked = report_html.linkify(escaped)
    assert "<a href=" in linked
    assert "<https" not in linked


# ---------------------------------------------------------------------------
# the rules that broke once
# ---------------------------------------------------------------------------


def test_the_page_background_switches_in_dark_mode():
    # The dark palette reached <body> and nothing else: the wrapper table
    # painted its light background straight back over it, so every heading
    # was dark text on a light page in a dark-mode client while every
    # computed colour was "correct".
    html = html_of([browse_listing(id="cool")])
    assert "prefers-color-scheme: dark" in html
    assert html.count('class="page"') >= 3


def test_dark_mode_overrides_every_colour_class_the_body_uses():
    html = html_of([
        make_listing(id="act", dollar_savings=400.0, pct_under_market=60.0),
        browse_listing(id="cool"),
    ])
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    dark = style[style.index("prefers-color-scheme: dark"):]
    for name in re.findall(r'class="([a-z ]+)"', html):
        for klass in name.split():
            if klass in ("pad", "lab", "accentbar"):
                continue  # geometry, not colour
            assert "." + klass in dark, klass


def test_a_valuation_section_is_accented_and_a_browse_section_is_not():
    # The colour rule is a content rule: saturated means CardPro is standing
    # behind a number. Painting COOL CARDS like a deal would undo in CSS the
    # separation the whole valuation engine enforces in code.
    claim = report_html.SECTION_ACCENTS[report.SECTION_TOP_OPPORTUNITIES]
    for key in (report.SECTION_COOL_CARDS, report.SECTION_CHEAP_FINDS,
                report.SECTION_CHEAP_AUCTIONS, report.SECTION_INVESTMENT,
                report.SECTION_AUCTIONS, report.SECTION_WATCH,
                report.SECTION_NEEDS_REVIEW):
        assert report_html.SECTION_ACCENTS[key] != claim


def test_every_section_has_an_accent():
    for key in report.SECTION_ORDER:
        assert key in report_html.SECTION_ACCENTS, key


def test_no_remote_content_is_referenced():
    # Apple Mail blocks remote content by default; an email that needs it
    # looks broken on the phone this is actually read on.
    html = html_of([browse_listing(id="cool")])
    assert "<img" not in html
    assert "url(" not in html
    assert "@import" not in html


# ---------------------------------------------------------------------------
# prose helpers
# ---------------------------------------------------------------------------


def test_unfill_rejoins_a_wrapped_paragraph_and_keeps_the_breaks_between_them():
    text = "one two\nthree four\n\nsecond para\nkeeps going"
    assert report_html.unfill(text) == "one two three four\n\nsecond para keeps going"


def test_the_footer_keeps_its_headings_and_items_apart():
    html = report_html._footer_html("SYSTEM HEALTH\n  Emails scanned: 1\n    and more\n")
    assert "SYSTEM HEALTH" in strip_tags(html)
    assert "<ul" in html
    # The four-space line is a continuation of the item above it, not its own.
    assert html.count("<li") == 1
    assert "Emails scanned: 1 and more" in strip_tags(html)


def test_only_the_first_unindented_line_of_a_footer_block_is_a_heading():
    # Treating every unindented line as a heading turned the three wrapped
    # lines of the SEARCH COVERAGE explanation into three headings shouting
    # mid-sentence fragments at the bottom of the email.
    html = report_html._footer_html(
        "SEARCH COVERAGE -- 20 players\n"
        "One query per player is why 99% of what CardPro sees is cheap\n"
        "raw filler.\n"
        '  For each of these 5 players, add: "PSA"\n'
    )
    assert html.count("font-weight:800") == 1, "more than one heading"
    assert "One query per player is why 99% of what CardPro sees is cheap raw filler." \
        in strip_tags(html)
    assert html.count("<li") == 1


def test_the_economics_assumptions_keep_their_bullets():
    # Same heading-and-bullets shape as the footers, so it gets the same
    # converter. Run through the prose helper it collapsed into a single
    # paragraph with stray asterisks where the bullets had been.
    from tests.test_report import make_economics

    model = build([make_listing(id="e", dollar_savings=100.0, pct_under_market=50.0,
                                economics=make_economics())])
    assert model.assumptions
    html = report_html.render(model)
    assert "ECONOMICS ASSUMPTIONS" in strip_tags(html)
    assert "* No sales tax" not in strip_tags(html)
    assert "No sales tax is modelled" in strip_tags(html)


def test_a_new_footer_block_starts_a_new_heading():
    html = report_html._footer_html("SYSTEM HEALTH\n  ok\n\nCraigslist quick check:\n  a\n")
    assert html.count("font-weight:800") == 2
