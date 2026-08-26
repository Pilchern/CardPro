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
