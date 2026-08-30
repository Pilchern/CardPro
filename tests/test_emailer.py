"""The SMTP send path.

One test, and it is about the socket timeout rather than the message: a
stalled SMTP connection with no timeout hangs the daily run forever, which
means no report and no failure email -- see emailer.SMTP_TIMEOUT_SECONDS.
"""
import inspect

from src import emailer


def test_smtp_connection_has_a_timeout():
    assert emailer.SMTP_TIMEOUT_SECONDS > 0
    assert "timeout=SMTP_TIMEOUT_SECONDS" in inspect.getsource(emailer.send_email)


def test_a_report_with_html_goes_out_as_multipart_alternative(monkeypatch):
    """Both parts, and the HTML one last.

    A client shows the last part it can display, so ordering is what makes
    the HTML the one a phone renders -- and keeping the text part is what
    makes the email still readable when the markup is stripped, images are
    off, or the HTML renderer has a bug.
    """
    sent = {}

    class FakeServer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, *args):
            pass

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", lambda *a, **k: FakeServer())
    emailer.send_email("subj", "the text part", "a@b.com", "pw", "to@b.com",
                       html_body="<p>the html part</p>")

    msg = sent["msg"]
    assert msg.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in msg.iter_parts()]
    assert parts == ["text/plain", "text/html"]
    assert "the text part" in msg.get_body(("plain",)).get_content()
    assert "the html part" in msg.get_body(("html",)).get_content()


def test_without_html_the_email_stays_plain_text(monkeypatch):
    sent = {}

    class FakeServer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, *args):
            pass

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", lambda *a, **k: FakeServer())
    emailer.send_email("subj", "body", "a@b.com", "pw", "to@b.com")
    assert sent["msg"].get_content_type() == "text/plain"
