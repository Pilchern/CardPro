"""Standalone Gmail SMTP sanity check -- doesn't touch eBay, so it works
even before your eBay dev account is approved. Sends one real test email
using GMAIL_ADDRESS / GMAIL_APP_PASSWORD / EMAIL_TO from .env.

Usage:
    python -m scripts.test_email
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.emailer import send_email

ROOT_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")

    missing = [name for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing .env values: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill in the Gmail section "
            f"(EBAY_* keys aren't needed for this test)."
        )

    gmail_address = os.environ["GMAIL_ADDRESS"]
    to_address = os.environ.get("EMAIL_TO") or gmail_address

    send_email(
        subject="[Card Deals] Test email",
        body=(
            "This is a test send from scripts/test_email.py.\n\n"
            "If you got this, Gmail SMTP + your App Password are working "
            "correctly and the real daily report will deliver fine once "
            "eBay access is approved."
        ),
        gmail_address=gmail_address,
        gmail_app_password=os.environ["GMAIL_APP_PASSWORD"],
        to_address=to_address,
    )
    print(f"Sent test email to {to_address}. Check your inbox (and spam folder).")


if __name__ == "__main__":
    main()
