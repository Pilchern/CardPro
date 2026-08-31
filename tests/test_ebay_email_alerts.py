import inspect
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest import mock

from src import ebay_email_alerts

SAMPLE_ALERT_HTML = """
<html><body>
<table>
<tr><td>
  <a href="https://www.ebay.com/itm/123456789012?hash=abc123&var=xyz">
    1986 Fleer Michael Jordan Rookie PSA 9
  </a>
  <div class="price">$4,999.99</div>
</td></tr>
<tr><td>
  <a href="https://www.ebay.com/itm/987654321098?hash=def456">Walter Payton Topps Rookie Raw</a>
  <span>Current price: $150.00</span>
</td></tr>
<tr><td>
  <a href="https://www.ebay.com/some/tracking/redirect?url=https%3A%2F%2Fwww.ebay.com%2Fitm%2F555555555555">
    Scottie Pippen Auto /25
  </a>
  <p>Buy It Now $899.00</p>
</td></tr>
</table>
<a href="https://www.ebay.com/help">Unrelated footer link, not an item</a>
</body></html>
"""


def test_extract_listings_finds_all_item_links():
    results = ebay_email_alerts.extract_listings_from_html(SAMPLE_ALERT_HTML)
    assert len(results) == 3


def test_extract_listings_gets_title_url_and_price():
    results = ebay_email_alerts.extract_listings_from_html(SAMPLE_ALERT_HTML)
    jordan = next(r for r in results if "Jordan" in r["title"])
    assert jordan["price"] == 4999.99
    assert "123456789012" in jordan["url"]


def test_extract_listings_ignores_non_item_links():
    results = ebay_email_alerts.extract_listings_from_html(SAMPLE_ALERT_HTML)
    assert not any("help" in r["url"] for r in results)


def test_extract_listings_handles_tracking_wrapped_urls():
    results = ebay_email_alerts.extract_listings_from_html(SAMPLE_ALERT_HTML)
    pippen = next(r for r in results if "Pippen" in r["title"])
    assert "555555555555" in pippen["url"]
    assert pippen["price"] == 899.00


def test_extract_listings_dedupes_same_url():
    html = SAMPLE_ALERT_HTML.replace(
        "</table>",
        '<tr><td><a href="https://www.ebay.com/itm/123456789012?hash=different">Duplicate</a>'
        '<div>$1.00</div></td></tr></table>',
    )
    results = ebay_email_alerts.extract_listings_from_html(html)
    urls = [r["url"] for r in results]
    assert len(urls) == len(set(urls))


def test_extract_listings_no_matches_on_empty_html():
    assert ebay_email_alerts.extract_listings_from_html("<html><body>no items here</body></html>") == []


SHIPPING_HTML = """
<html><body>
<a href="https://www.ebay.com/itm/111111111111">Caleb Williams Prizm Rookie</a>
<div>$100.00</div>
<div>+$5.99 shipping</div>
<a href="https://www.ebay.com/itm/222222222222">Rome Odunze Optic Rookie</a>
<div>$50.00</div>
<div>Free shipping</div>
<a href="https://www.ebay.com/itm/333333333333">Luther Burden Rookie Card</a>
<div>$30.00</div>
</body></html>
"""


def test_extract_listings_finds_paid_shipping():
    results = ebay_email_alerts.extract_listings_from_html(SHIPPING_HTML)
    williams = next(r for r in results if "Williams" in r["title"])
    assert williams["shipping_price"] == 5.99


def test_extract_listings_finds_free_shipping_as_zero():
    results = ebay_email_alerts.extract_listings_from_html(SHIPPING_HTML)
    odunze = next(r for r in results if "Odunze" in r["title"])
    assert odunze["shipping_price"] == 0.0


def test_extract_listings_shipping_is_none_when_not_mentioned():
    results = ebay_email_alerts.extract_listings_from_html(SHIPPING_HTML)
    burden = next(r for r in results if "Burden" in r["title"])
    assert burden["shipping_price"] is None


def test_extract_listings_does_not_bleed_shipping_across_listings():
    results = ebay_email_alerts.extract_listings_from_html(SHIPPING_HTML)
    williams = next(r for r in results if "Williams" in r["title"])
    odunze = next(r for r in results if "Odunze" in r["title"])
    assert williams["shipping_price"] == 5.99
    assert odunze["shipping_price"] == 0.0


FLAT_NO_WRAPPER_HTML = """
<html><body>
<a href="https://www.ebay.com/itm/123456789012">1986 Fleer Michael Jordan Rookie PSA 9</a>
<div>$4,999.99</div>
<a href="https://www.ebay.com/itm/999999999999">Some Unrelated Player card</a>
<div>$25.00</div>
</body></html>
"""


def test_extract_listings_does_not_bleed_price_across_listings_with_no_wrapper():
    """Regression test: listings with no per-item wrapper container
    (title link, then a price element as its flat sibling, repeated) must
    each get their OWN nearby price, not the first price found in the
    email overall."""
    results = ebay_email_alerts.extract_listings_from_html(FLAT_NO_WRAPPER_HTML)
    assert len(results) == 2
    jordan = next(r for r in results if "Jordan" in r["title"])
    unrelated = next(r for r in results if "Unrelated" in r["title"])
    assert jordan["price"] == 4999.99
    assert unrelated["price"] == 25.00


def test_get_html_body_multipart():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("plain text version", "plain"))
    msg.attach(MIMEText("<html><body>html version</body></html>", "html"))
    body = ebay_email_alerts.get_html_body(msg)
    assert body is not None
    assert "html version" in body


def test_get_html_body_plain_only_returns_none():
    msg = MIMEText("just plain text, no html part", "plain")
    assert ebay_email_alerts.get_html_body(msg) is None


def test_fetch_alert_messages_uses_imap_search_and_fetch():
    fake_imap = mock.MagicMock()
    fake_imap.__enter__.return_value = fake_imap
    fake_imap.search.return_value = ("OK", [b"1 2"])

    raw_email = MIMEText("<html><body>hi</body></html>", "html").as_bytes()
    fake_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {123}", raw_email)])

    with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=fake_imap):
        messages = ebay_email_alerts.fetch_alert_messages("user@gmail.com", "app-password", "ebay.com", 2)

    fake_imap.login.assert_called_once_with("user@gmail.com", "app-password")
    # quoted -- see fetch_alert_messages: imaplib doesn't quote mailbox
    # names itself, and this default contains a space
    fake_imap.select.assert_called_once_with('"[Gmail]/All Mail"', readonly=True)
    assert len(messages) == 2  # one per message number returned by search


def test_fetch_alert_messages_uses_custom_mailbox_when_given():
    fake_imap = mock.MagicMock()
    fake_imap.__enter__.return_value = fake_imap
    fake_imap.search.return_value = ("OK", [b""])

    with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=fake_imap):
        ebay_email_alerts.fetch_alert_messages("user@gmail.com", "app-password", "ebay.com", 2, mailbox="INBOX")

    fake_imap.select.assert_called_once_with('"INBOX"', readonly=True)


def test_fetch_alert_messages_returns_empty_on_no_results():
    fake_imap = mock.MagicMock()
    fake_imap.__enter__.return_value = fake_imap
    fake_imap.search.return_value = ("OK", [b""])

    with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=fake_imap):
        messages = ebay_email_alerts.fetch_alert_messages("user@gmail.com", "app-password", "ebay.com", 2)

    assert messages == []


def test_fetch_alert_listings_end_to_end():
    fake_imap = mock.MagicMock()
    fake_imap.__enter__.return_value = fake_imap
    fake_imap.search.return_value = ("OK", [b"1"])

    raw_email = MIMEText(SAMPLE_ALERT_HTML, "html").as_bytes()
    fake_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {123}", raw_email)])

    with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=fake_imap):
        listings = ebay_email_alerts.fetch_alert_listings("user@gmail.com", "app-password", "ebay.com", 2)

    assert len(listings) == 3


def test_looks_truncated_detects_ellipsis_char():
    assert ebay_email_alerts.looks_truncated("1990 Fleer Frank Thomas PSA 1…") is True


def test_fetch_alert_listings_warns_when_emails_found_but_nothing_extracted(caplog):
    """Emails present + zero listings extracted almost always means eBay
    changed their template and the HTML parse broke -- not that there was
    legitimately nothing new. This must be distinguishable from the normal
    "no alert emails at all" case, which should NOT warn (see next test)."""
    fake_imap = mock.MagicMock()
    fake_imap.__enter__.return_value = fake_imap
    fake_imap.search.return_value = ("OK", [b"1"])

    raw_email = MIMEText("<html><body>no listing links in here at all</body></html>", "html").as_bytes()
    fake_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {123}", raw_email)])

    with caplog.at_level("WARNING"):
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=fake_imap):
            listings = ebay_email_alerts.fetch_alert_listings("user@gmail.com", "app-password", "ebay.com", 2)

    assert listings == []
    assert any("changed their email template" in record.message for record in caplog.records)


def test_fetch_alert_listings_no_warning_when_legitimately_no_emails(caplog):
    fake_imap = mock.MagicMock()
    fake_imap.__enter__.return_value = fake_imap
    fake_imap.search.return_value = ("OK", [b""])

    with caplog.at_level("WARNING"):
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=fake_imap):
            listings = ebay_email_alerts.fetch_alert_listings("user@gmail.com", "app-password", "ebay.com", 2)

    assert listings == []
    assert not any("changed their email template" in record.message for record in caplog.records)


def test_looks_truncated_detects_triple_dot():
    assert ebay_email_alerts.looks_truncated("1990 Fleer Frank Thomas PSA 1...") is True


def test_looks_truncated_false_for_complete_title():
    assert ebay_email_alerts.looks_truncated("1990 Fleer Frank Thomas PSA 10") is False


def test_no_src_module_sends_a_user_agent_header():
    """Principle #1: never build tooling to defeat a site's anti-bot
    measures. A spoofed User-Agent exists for exactly one purpose -- making
    an automated request look like a person in a browser -- so its presence
    anywhere in src/ is the regression, whatever module it turns up in.
    fetch_full_title() carried one until it was removed; this is the guard
    that stops it (or an equivalent elsewhere) coming back.
    """
    src_dir = Path(__file__).resolve().parent.parent / "src"
    pattern = re.compile(r"user[-_ ]?agent", re.IGNORECASE)
    offenders = sorted(
        path.name
        for path in src_dir.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == [], "spoofed browser identity found in: {}".format(", ".join(offenders))


def test_truncated_title_is_not_repairable_by_any_module_function():
    """The recovery path is gone on purpose, not by accident. Nothing in
    this module fetches an eBay page any more; looks_truncated() reporting
    the problem is the whole of the behaviour."""
    assert not hasattr(ebay_email_alerts, "fetch_full_title")
    assert not hasattr(ebay_email_alerts, "_FULL_TITLE_HEADERS")


# --- listing-type detection -------------------------------------------------
# An auction's current bid is not a price (a non-negotiable for this project),
# so the parser has to distinguish the two -- and has to be able to say "I
# can't tell" rather than defaulting to Buy It Now.

AUCTION_HTML = """
<html><body>
  <div><a href="https://www.ebay.com/itm/111">Caleb Williams 2024 Prizm RC</a>
       <span>$45.00</span><span>7 bids</span><span>Time left: 2d 04h</span></div>
</body></html>
"""

FIXED_PRICE_HTML = """
<html><body>
  <div><a href="https://www.ebay.com/itm/222">Rome Odunze 2024 Prizm RC</a>
       <span>$60.00</span><span>Buy It Now</span><span>or Best Offer</span></div>
</body></html>
"""

NO_EVIDENCE_HTML = """
<html><body>
  <div><a href="https://www.ebay.com/itm/333">Connor Bedard Young Guns</a><span>$120.00</span></div>
</body></html>
"""

TWO_LISTINGS_HTML = """
<html><body>
  <div><a href="https://www.ebay.com/itm/444">Auction card</a><span>$10.00</span><span>3 bids</span></div>
  <div><a href="https://www.ebay.com/itm/555">Fixed card</a><span>$20.00</span><span>Buy It Now</span></div>
</body></html>
"""


def test_auction_detected_with_bid_count_and_time_left():
    listing = ebay_email_alerts.extract_listings_from_html(AUCTION_HTML)[0]
    assert listing["listing_type"] == ebay_email_alerts.LISTING_TYPE_AUCTION
    assert listing["bid_count"] == 7
    assert listing["time_left_text"] is not None


def test_fixed_price_detected_with_best_offer():
    listing = ebay_email_alerts.extract_listings_from_html(FIXED_PRICE_HTML)[0]
    assert listing["listing_type"] == ebay_email_alerts.LISTING_TYPE_FIXED
    assert listing["has_best_offer"] is True
    assert listing["bid_count"] is None


def test_no_evidence_yields_unknown_not_fixed_price():
    listing = ebay_email_alerts.extract_listings_from_html(NO_EVIDENCE_HTML)[0]
    assert listing["listing_type"] == ebay_email_alerts.LISTING_TYPE_UNKNOWN
    assert listing["has_best_offer"] is False


def test_bid_evidence_wins_over_buy_it_now_on_the_same_listing():
    # eBay auctions can carry a Buy It Now price until the first bid; once
    # bidding starts the number shown is a current bid. Resolving toward
    # "auction" is the safe direction.
    html = """
    <html><body><div><a href="https://www.ebay.com/itm/666">Card</a>
      <span>$30.00</span><span>Buy It Now</span><span>2 bids</span></div></body></html>
    """
    assert ebay_email_alerts.extract_listings_from_html(html)[0]["listing_type"] == (
        ebay_email_alerts.LISTING_TYPE_AUCTION
    )


def test_listing_type_does_not_leak_between_adjacent_listings():
    auction, fixed = ebay_email_alerts.extract_listings_from_html(TWO_LISTINGS_HTML)
    assert auction["listing_type"] == ebay_email_alerts.LISTING_TYPE_AUCTION
    assert auction["bid_count"] == 3
    assert fixed["listing_type"] == ebay_email_alerts.LISTING_TYPE_FIXED
    assert fixed["bid_count"] is None


def test_imap_connection_has_a_timeout():
    """Without one, a server that stalls mid-FETCH hangs the run past the
    point where anything can report it: no exception, so main's failure
    handler never fires, and the job burns until GitHub's six-hour cap. It
    is the only path where "never go silent" did not hold."""
    assert ebay_email_alerts.IMAP_TIMEOUT_SECONDS > 0
    source = inspect.getsource(ebay_email_alerts.fetch_alert_messages)
    assert "timeout=IMAP_TIMEOUT_SECONDS" in source


class TestFailuresAreNotQuietDays:
    """A mailbox that cannot be read must never render as a mailbox with
    nothing in it. Both used to produce the same empty list."""

    def _imap(self, search_status="OK", search_data=None, fetch_status="OK"):
        imap = mock.MagicMock()
        imap.__enter__.return_value = imap
        imap.search.return_value = (search_status, search_data if search_data is not None else [b""])
        imap.fetch.return_value = (fetch_status, [(b"1", b"From: a@b\r\n\r\nhi")])
        return imap

    def test_a_search_error_raises_rather_than_reporting_an_empty_inbox(self):
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=self._imap("NO")):
            try:
                ebay_email_alerts.fetch_alert_messages("a@b", "pw", "ebay", 2)
            except ebay_email_alerts.AlertFetchFailed:
                return
            raise AssertionError("a server error was reported as an empty inbox")

    def test_a_genuinely_empty_mailbox_is_still_just_empty(self):
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=self._imap("OK", [b""])):
            assert ebay_email_alerts.fetch_alert_messages("a@b", "pw", "ebay", 2) == []

    def test_a_skipped_message_is_counted_not_swallowed(self):
        counters = {}
        imap = self._imap("OK", [b"1 2"], fetch_status="NO")
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=imap):
            messages = ebay_email_alerts.fetch_alert_messages(
                "a@b", "pw", "ebay", 2, counters=counters
            )
        assert messages == []
        assert counters["fetch_failures"] == 2

    def test_the_template_canary_is_handed_to_the_caller_not_only_logged(self):
        counters = {}
        imap = self._imap("OK", [b"1"])
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=imap):
            with mock.patch.object(ebay_email_alerts, "get_html_body", return_value="<html></html>"):
                ebay_email_alerts.fetch_alert_listings("a@b", "pw", "ebay", 2, counters=counters)
        assert "changed their email template" in counters["template_warning"]

    def test_no_canary_when_listings_were_actually_extracted(self):
        counters = {}
        imap = self._imap("OK", [b"1"])
        with mock.patch.object(ebay_email_alerts.imaplib, "IMAP4_SSL", return_value=imap):
            with mock.patch.object(ebay_email_alerts, "get_html_body", return_value="<html></html>"):
                with mock.patch.object(
                    ebay_email_alerts, "extract_listings_from_html",
                    return_value=[{"title": "t", "url": "u", "price": 1.0}],
                ):
                    ebay_email_alerts.fetch_alert_listings("a@b", "pw", "ebay", 2, counters=counters)
        assert "template_warning" not in counters


class TestTheFullestTitleAvailable:
    """THE bottleneck, measured on the first 350 titles a live run stored:
    98% arrived truncated, at a median of 30 characters -- cut off mid-word
    before the set name. The set, the parallel, the card number and the
    grade all live past that cut, which is why set_name resolved for a sixth
    of listings. eBay truncates the VISIBLE link text; the same anchor's
    title attribute and its image's alt are written for tooltips and screen
    readers and are frequently the whole thing."""

    # A real truncation is a prefix -- eBay simply cuts the string -- so the
    # fixture has to be one too, or it is testing something that never happens.
    FULL = "2024 Panini Caleb Williams Prizm Silver RC #301 PSA 10"
    CUT = "2024 Panini Caleb Williams Pr…"

    def _extract(self, html):
        return ebay_email_alerts.extract_listings_from_html(html)[0]["title"]

    def test_the_title_attribute_is_preferred_over_the_truncated_text(self):
        html = '<a href="https://www.ebay.com/itm/1" title="{}">{}</a>'.format(self.FULL, self.CUT)
        assert self._extract(html) == self.FULL

    def test_an_image_alt_works_too(self):
        html = '<a href="https://www.ebay.com/itm/1"><img alt="{}">{}</a>'.format(self.FULL, self.CUT)
        assert self._extract(html) == self.FULL

    def test_an_aria_label_works_too(self):
        html = '<a href="https://www.ebay.com/itm/1" aria-label="{}">{}</a>'.format(
            self.FULL, self.CUT
        )
        assert self._extract(html) == self.FULL

    def test_a_candidate_that_is_not_this_listing_is_refused(self):
        # Without the stem check a generic alt would silently replace a real
        # title, which is worse than a short one.
        html = '<a href="https://www.ebay.com/itm/1" title="Shop eBay for great deals now">{}</a>'.format(
            self.CUT
        )
        assert self._extract(html) == self.CUT

    def test_a_shorter_candidate_never_wins(self):
        html = '<a href="https://www.ebay.com/itm/1" title="2024 Panini">{}</a>'.format(self.CUT)
        assert self._extract(html) == self.CUT

    def test_no_attributes_leaves_the_visible_text_alone(self):
        html = '<a href="https://www.ebay.com/itm/1">{}</a>'.format(self.CUT)
        assert self._extract(html) == self.CUT

    def test_an_untruncated_but_short_visible_title_can_still_be_extended(self):
        html = '<a href="https://www.ebay.com/itm/1" title="{}">2024 Panini Caleb</a>'.format(
            self.FULL
        )
        assert self._extract(html) == self.FULL

    def test_whitespace_in_an_attribute_is_normalised(self):
        html = '<a href="https://www.ebay.com/itm/1" title="2024  Panini\n Caleb Williams Prizm Silver">{}</a>'.format(
            self.CUT
        )
        assert self._extract(html) == "2024 Panini Caleb Williams Prizm Silver"

    def test_a_title_split_across_spans_is_still_recovered(self):
        # HTML emails are span-and-table soup, so the visible title routinely
        # arrives in pieces. Joining those pieces with nothing welds two words
        # together ("Panini Caleb" -> "PaniniCaleb"), the stem built from the
        # weld matches no real title, and every recovery was refused.
        html = (
            '<a href="https://www.ebay.com/itm/1" title="{}">'
            "<span>2024 Panini</span><span>Caleb Williams Pr\u2026</span></a>"
        ).format(self.FULL)
        assert self._extract(html) == self.FULL

    def test_a_title_split_across_block_elements_is_still_recovered(self):
        html = (
            '<a href="https://www.ebay.com/itm/1" title="{}">'
            "<div>2024 Panini</div><div>Caleb Williams Pr\u2026</div></a>"
        ).format(self.FULL)
        assert self._extract(html) == self.FULL

    def test_a_nested_candidate_is_still_refused_when_it_is_another_listing(self):
        # The nesting fix must not buy recovery by weakening the check that
        # keeps a generic string out of a real title. This decoy is longer
        # than the visible text, so only the stem check can stop it.
        html = (
            '<a href="https://www.ebay.com/itm/1" '
            'title="Shop eBay for great deals on trading cards and collectibles today">'
            "<span>2024 Panini</span><span>Caleb Williams Pr\u2026</span></a>"
        )
        assert self._extract(html) == "2024 Panini Caleb Williams Pr\u2026"

    def test_the_visible_text_of_a_split_title_keeps_its_word_break(self):
        # Nothing fuller exists here, so the visible text is the title -- and
        # it must not arrive with two words welded together, which no
        # downstream parser could split again.
        html = (
            '<a href="https://www.ebay.com/itm/1">'
            "<span>2024 Panini</span><span>Caleb Williams Pr\u2026</span></a>"
        )
        assert self._extract(html) == "2024 Panini Caleb Williams Pr\u2026"

    def test_a_prefixed_template_still_matches(self):
        # Some templates put "New listing" in front of one copy and not the
        # other; a strict prefix check would throw the real title away.
        html = '<a href="https://www.ebay.com/itm/1" title="New listing {}">{}</a>'.format(
            self.FULL, self.CUT
        )
        assert self._extract(html).endswith("#301 PSA 10")


class TestTitleStem:
    def test_it_drops_the_partial_word_the_cut_left(self):
        assert ebay_email_alerts._title_stem("2024 Panini Caleb Williams Pr…") == (
            "2024 panini caleb williams"
        )

    def test_it_handles_three_dots_as_well_as_an_ellipsis(self):
        assert ebay_email_alerts._title_stem("2024 Panini Caleb Williams Pr...") == (
            "2024 panini caleb williams"
        )

    def test_an_untruncated_title_is_its_own_stem(self):
        assert ebay_email_alerts._title_stem("2026 Topps #201 Kyle Teel") == "2026 topps #201 kyle teel"


def test_the_truncation_rate_is_counted_for_the_report():
    counters = {}
    html = (
        '<a href="https://www.ebay.com/itm/1">2024 Panini Caleb Williams Pr…</a>'
        '<a href="https://www.ebay.com/itm/2">2026 Topps #201 Kyle Teel</a>'
    )
    ebay_email_alerts.extract_listings_from_html(html, counters=counters)
    assert counters["titles_seen"] == 2
    assert counters["titles_truncated"] == 1


def test_a_recovered_title_is_not_counted_as_truncated():
    # The whole point of the recovery: the number has to move when it works.
    counters = {}
    html = (
        '<a href="https://www.ebay.com/itm/1" title="2024 Panini Caleb Williams Prizm Silver #301">'
        "2024 Panini Caleb Williams Pr…</a>"
    )
    ebay_email_alerts.extract_listings_from_html(html, counters=counters)
    assert counters["titles_truncated"] == 0


def test_a_refused_recovery_is_counted_separately_from_a_truncation():
    """A truncation rate on its own cannot be acted on: 98% could mean eBay
    sends no fuller title, or that a fuller one was sitting in the HTML and
    our own match check threw it away. Those need different work."""
    counters = {}
    html = (
        # A fuller copy is there and matches -- recovered, nothing refused.
        '<a href="https://www.ebay.com/itm/1" title="2024 Panini Caleb Williams Prizm Silver #301">'
        "2024 Panini Caleb Williams Pr\u2026</a>"
        # A longer copy is there but belongs to no listing of ours -- refused.
        '<a href="https://www.ebay.com/itm/2" '
        'title="Shop eBay for great deals on trading cards and collectibles today">'
        "2024 Panini Caleb Williams Pr\u2026</a>"
        # Nothing fuller anywhere -- truncated, but nothing was refused.
        '<a href="https://www.ebay.com/itm/3">2024 Panini Caleb Williams Pr\u2026</a>'
    )
    ebay_email_alerts.extract_listings_from_html(html, counters=counters)
    assert counters["titles_seen"] == 3
    assert counters["titles_truncated"] == 2
    assert counters["titles_recovery_refused"] == 1


def test_nothing_is_refused_when_a_later_candidate_gets_through():
    # A near-miss that cost us nothing is not worth reporting -- it would
    # send tomorrow's reader after a title we already have in full.
    counters = {}
    html = (
        '<a href="https://www.ebay.com/itm/1" '
        'title="Shop eBay for great deals on trading cards and collectibles today">'
        '<img alt="2024 Panini Caleb Williams Prizm Silver RC #301 PSA 10">'
        "2024 Panini Caleb Williams Pr\u2026</a>"
    )
    ebay_email_alerts.extract_listings_from_html(html, counters=counters)
    assert counters["titles_recovery_refused"] == 0


# --- One item, several links ------------------------------------------------
# eBay's alert template gives each listing more than one anchor: the photo,
# the title, sometimes a button, in separate table cells. The photo link is
# rover-wrapped and carries the untruncated title in its alt text; the title
# link sits next to the price. Reading only the first anchor found meant a
# live run produced 1382 listings with a full title and price None on every
# single one -- nothing to value, nothing to report, an empty email. The
# listing is one listing, so the anchors are merged and neither one's own
# second link counts as the boundary with the next listing.

PHOTO_AND_TITLE_ROW = """
<table><tr>
  <td><a href="https://www.ebay.com/rover/1/711-53200/2?mpre=https%3A%2F%2Fwww.ebay.com%2Fitm%2F123456789012">
      <img alt="2024 Panini Prizm Caleb Williams Silver Prizm RC #301" src="photo.jpg"></a></td>
  <td><a href="https://www.ebay.com/itm/123456789012?hash=abc">2024 Panini Prizm Caleb Willi…</a>
      <div>$4.99</div><div>+ $1.25 shipping</div></td>
</tr></table>
"""


def test_photo_and_title_links_are_one_listing():
    results = ebay_email_alerts.extract_listings_from_html(PHOTO_AND_TITLE_ROW)
    assert len(results) == 1
    assert results[0]["url"] == "https://www.ebay.com/itm/123456789012"


def test_price_survives_a_second_link_to_the_same_item():
    # The regression itself: the photo anchor wins on document order, and the
    # title anchor beside the price used to end its search instead of feeding
    # it.
    listing = ebay_email_alerts.extract_listings_from_html(PHOTO_AND_TITLE_ROW)[0]
    assert listing["price"] == 4.99
    assert listing["shipping_price"] == 1.25


def test_the_fuller_title_is_kept_when_the_anchors_disagree():
    listing = ebay_email_alerts.extract_listings_from_html(PHOTO_AND_TITLE_ROW)[0]
    assert listing["title"] == "2024 Panini Prizm Caleb Williams Silver Prizm RC #301"


def test_a_second_link_to_the_same_item_cannot_replace_the_title():
    # Longer is not enough. A "more from this seller" link points at the same
    # item and would win on length alone, so it still has to carry the part
    # of the title we can be sure of.
    html = (
        '<table><tr><td><a href="https://www.ebay.com/itm/123456789012">'
        "2024 Panini Prizm Caleb Williams Silver RC #301</a><div>$4.99</div>"
        '<a href="https://www.ebay.com/itm/123456789012?ssn=x">'
        "See every other card this seller has listed right now</a></td></tr></table>"
    )
    listing = ebay_email_alerts.extract_listings_from_html(html)[0]
    assert listing["title"] == "2024 Panini Prizm Caleb Williams Silver RC #301"


def test_the_first_price_found_is_not_overwritten_by_a_later_link():
    html = (
        '<table><tr><td><a href="https://www.ebay.com/itm/123456789012">Caleb Williams RC</a>'
        "<div>$4.99</div></td>"
        '<td><a href="https://www.ebay.com/itm/123456789012?hash=b">Shipping calculator</a>'
        "<div>$99.00</div></td></tr></table>"
    )
    listing = ebay_email_alerts.extract_listings_from_html(html)[0]
    assert listing["price"] == 4.99


def test_bid_evidence_from_either_link_makes_it_an_auction():
    # A current bid is not an asking price, so the evidence has to survive
    # being on the anchor that did not win.
    html = (
        '<table><tr>'
        '<td><a href="https://www.ebay.com/rover/1?mpre=https%3A%2F%2Fwww.ebay.com%2Fitm%2F999888777666">'
        '<img alt="2024 Topps Chrome Kyle Teel Refractor Auto"></a></td>'
        '<td><a href="https://www.ebay.com/itm/999888777666">2024 Topps Chrome Kyle…</a>'
        "<div>$0.99</div><div>3 bids</div><div>Time left: 4h 12m</div></td>"
        "</tr></table>"
    )
    listing = ebay_email_alerts.extract_listings_from_html(html)[0]
    assert listing["listing_type"] == ebay_email_alerts.LISTING_TYPE_AUCTION
    assert listing["bid_count"] == 3
    assert listing["price"] == 0.99


def test_a_rover_wrapped_neighbour_still_bounds_the_search():
    # The other half of the same rule: a DIFFERENT item's link is a boundary
    # even when it is percent-encoded, or the cheap card on the left would
    # borrow the slab on the right's price.
    html = (
        '<table><tr><td><a href="https://www.ebay.com/itm/111111111111">Caleb Williams RC</a></td>'
        '<td><a href="https://www.ebay.com/rover/1?mpre=https%3A%2F%2Fwww.ebay.com%2Fitm%2F222222222222">'
        "1986 Fleer Michael Jordan PSA 9</a><div>$4,999.99</div></td></tr></table>"
    )
    williams = next(r for r in ebay_email_alerts.extract_listings_from_html(html) if "Williams" in r["title"])
    assert williams["price"] is None


def test_a_photo_link_with_no_title_does_not_become_a_listing_of_its_own():
    html = (
        '<table><tr><td><a href="https://www.ebay.com/itm/123456789012"><img src="photo.jpg"></a>'
        '<a href="https://www.ebay.com/itm/123456789012">Caleb Williams RC</a>'
        "<div>$4.99</div></td></tr></table>"
    )
    results = ebay_email_alerts.extract_listings_from_html(html)
    assert len(results) == 1
    assert results[0]["title"] == "Caleb Williams RC"
    assert results[0]["price"] == 4.99


def test_merged_listings_are_counted_once():
    counters = {}
    ebay_email_alerts.extract_listings_from_html(PHOTO_AND_TITLE_ROW, counters=counters)
    assert counters["titles_seen"] == 1
    # The winning title is whole, so this row is not part of the truncation
    # rate the health footer reports.
    assert counters["titles_truncated"] == 0


def test_a_refusal_on_the_losing_link_is_not_reported():
    # The photo link's alt was refused, but the title link carried the whole
    # title anyway. Reporting that near-miss would send tomorrow's reader
    # after something we already have.
    counters = {}
    html = (
        '<table><tr><td><a href="https://www.ebay.com/itm/123456789012" '
        'title="Shop eBay for great deals on trading cards and collectibles">'
        '<img alt="eBay"></a></td>'
        '<td><a href="https://www.ebay.com/itm/123456789012?hash=b">'
        "2024 Panini Prizm Caleb Williams Silver Prizm RC #301</a>"
        "<div>$4.99</div></td></tr></table>"
    )
    listing = ebay_email_alerts.extract_listings_from_html(html, counters=counters)[0]
    assert listing["title"] == "2024 Panini Prizm Caleb Williams Silver Prizm RC #301"
    assert counters["titles_recovery_refused"] == 0
