"""Loads .env secrets plus the two editable JSON config files."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.ebay_email_alerts import DEFAULT_MAILBOX

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"


@dataclass
class Config:
    # Optional: eBay access isn't required to run. If EBAY_CLIENT_ID/SECRET
    # aren't set (e.g. the developer account is declined/pending), the
    # scraper just skips eBay and sends Craigslist links only -- see
    # main.py's ebay_enabled check.
    ebay_client_id: Optional[str]
    ebay_client_secret: Optional[str]
    gmail_address: str
    gmail_app_password: str
    email_to: str

    players: list[str]
    player_tiers: dict[str, str]  # player name -> "legend" | "young_core"; unlisted defaults to "legend"

    discount_threshold_pct: float
    min_savings_dollars: float
    ebay_category_id: str
    ebay_marketplace_id: str
    ebay_active_listing_limit: int
    ebay_sold_lookback_days: int
    ebay_min_comps_required: int

    craigslist_site: str
    craigslist_category: str

    # eBay-via-email-alerts: an alternate to the (declined) eBay API. See
    # ebay_email_alerts.py and price_history.py.
    ebay_alerts_enabled: bool
    ebay_alerts_sender_contains: str
    ebay_alerts_lookback_days: int
    ebay_alerts_mailbox: str  # IMAP folder searched -- defaults to All Mail, see ebay_email_alerts.DEFAULT_MAILBOX
    ebay_alert_price_history_path: Path
    ebay_alert_price_history_max_age_days: int

    seen_listings_path: Path
    prune_after_days: int

    email_subject_prefix: str


def load_config() -> Config:
    load_dotenv(ROOT_DIR / ".env")

    # Only Gmail is truly required -- eBay is optional (see Config docstring).
    missing = [name for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required .env values: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )

    with open(CONFIG_DIR / "watchlist.json") as f:
        watchlist = json.load(f)
    with open(CONFIG_DIR / "settings.json") as f:
        settings = json.load(f)

    # Treat the unfilled-in placeholder from .env.example the same as "not set".
    ebay_client_id = os.environ.get("EBAY_CLIENT_ID") or None
    if ebay_client_id == "your_ebay_client_id":
        ebay_client_id = None
    ebay_client_secret = os.environ.get("EBAY_CLIENT_SECRET") or None
    if ebay_client_secret == "your_ebay_client_secret":
        ebay_client_secret = None

    return Config(
        ebay_client_id=ebay_client_id,
        ebay_client_secret=ebay_client_secret,
        gmail_address=os.environ["GMAIL_ADDRESS"],
        gmail_app_password=os.environ["GMAIL_APP_PASSWORD"],
        email_to=os.environ.get("EMAIL_TO") or os.environ["GMAIL_ADDRESS"],
        players=watchlist["players"],
        player_tiers=watchlist.get("player_tiers", {}),
        discount_threshold_pct=float(settings["discount_threshold_pct"]),
        min_savings_dollars=float(settings["min_savings_dollars"]),
        ebay_category_id=settings["ebay"]["category_id"],
        ebay_marketplace_id=settings["ebay"]["marketplace_id"],
        ebay_active_listing_limit=int(settings["ebay"]["active_listing_limit_per_player"]),
        ebay_sold_lookback_days=int(settings["ebay"]["sold_lookback_days"]),
        ebay_min_comps_required=int(settings["ebay"]["min_comps_required"]),
        craigslist_site=settings["craigslist"]["site"],
        craigslist_category=settings["craigslist"]["category"],
        ebay_alerts_enabled=bool(settings["ebay_alerts"]["enabled"]),
        ebay_alerts_sender_contains=settings["ebay_alerts"]["sender_contains"],
        ebay_alerts_lookback_days=int(settings["ebay_alerts"]["lookback_days"]),
        ebay_alerts_mailbox=settings["ebay_alerts"].get("mailbox", DEFAULT_MAILBOX),
        ebay_alert_price_history_path=ROOT_DIR / settings["ebay_alerts"]["price_history_path"],
        ebay_alert_price_history_max_age_days=int(settings["ebay_alerts"]["price_history_max_age_days"]),
        seen_listings_path=ROOT_DIR / settings["dedupe"]["seen_listings_path"],
        prune_after_days=int(settings["dedupe"]["prune_after_days"]),
        email_subject_prefix=settings["email"]["subject_prefix"],
    )
