"""Sends the report via Gmail SMTP using an App Password.

Plain smtplib + SSL -- no third-party email service. Requires 2-Step
Verification enabled on the Gmail account and an App Password generated at
https://myaccount.google.com/apppasswords (a normal Gmail password will be
rejected by smtp.gmail.com).
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

#: Socket timeout, for the same reason as ebay_email_alerts.IMAP_TIMEOUT_SECONDS:
#: a stalled SMTP socket with no timeout hangs the run past the point where
#: anything can report the failure.
SMTP_TIMEOUT_SECONDS = 30


def send_email(subject: str, body: str, gmail_address: str, gmail_app_password: str,
               to_address: str, html_body: str = None) -> None:
    """Send the report. With ``html_body`` it goes as ``multipart/alternative``.

    The plain-text part is never dropped, and not out of politeness: it is
    the part that still says everything when images are off, when the client
    is a terminal, when a corporate filter strips the markup, or when the
    HTML renderer has a bug. A mail client picks the last part it can
    display -- so the HTML goes second -- and both parts are rendered from
    one model (see src/report_html.py), which is what makes "whichever one
    you get is the same report" true rather than hopeful.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = to_address
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.login(gmail_address, gmail_app_password)
        server.send_message(msg)

    logger.info("Sent %s email %r to %s", "HTML" if html_body else "plain-text",
                subject, to_address)
