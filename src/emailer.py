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


def send_email(subject: str, body: str, gmail_address: str, gmail_app_password: str, to_address: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = to_address
    msg.set_content(body)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(gmail_address, gmail_app_password)
        server.send_message(msg)

    logger.info("Sent email %r to %s", subject, to_address)
