from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest import mock

import requests

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


def test_fetch_full_title_strips_ebay_suffix():
    fake_resp = mock.Mock(status_code=200, text="<html><head><title>1990 Fleer Frank Thomas PSA 10 | eBay</title></head></html>")
    with mock.patch.object(ebay_email_alerts.requests, "get", return_value=fake_resp):
        title = ebay_email_alerts.fetch_full_title("https://www.ebay.com/itm/800530598774")
    assert title == "1990 Fleer Frank Thomas PSA 10"


def test_fetch_full_title_returns_none_on_network_error():
    with mock.patch.object(ebay_email_alerts.requests, "get", side_effect=requests.RequestException("boom")):
        assert ebay_email_alerts.fetch_full_title("https://www.ebay.com/itm/1") is None


def test_fetch_full_title_returns_none_on_non_200():
    fake_resp = mock.Mock(status_code=403, text="blocked")
    with mock.patch.object(ebay_email_alerts.requests, "get", return_value=fake_resp):
        assert ebay_email_alerts.fetch_full_title("https://www.ebay.com/itm/1") is None


def test_fetch_full_title_returns_none_when_no_title_tag():
    fake_resp = mock.Mock(status_code=200, text="<html><body>no title here</body></html>")
    with mock.patch.object(ebay_email_alerts.requests, "get", return_value=fake_resp):
        assert ebay_email_alerts.fetch_full_title("https://www.ebay.com/itm/1") is None


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
