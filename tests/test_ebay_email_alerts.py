from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    fake_imap.select.assert_called_once_with("INBOX", readonly=True)
    assert len(messages) == 2  # one per message number returned by search


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
