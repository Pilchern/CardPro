"""Loads .env secrets plus the two editable JSON config files."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"


@dataclass
class Config:
    ebay_client_id: str
    ebay_client_secret: str
    gmail_address: str
    gmail_app_password: str
    email_to: str

    players: list[str]

    discount_threshold_pct: float
    ebay_category_id: str
    ebay_marketplace_id: str
    ebay_active_listing_limit: int
    ebay_sold_lookback_days: int
    ebay_min_comps_required: int

    craigslist_site: str
    craigslist_category: str
    craigslist_headless: bool

    seen_listings_path: Path
    prune_after_days: int

    email_subject_prefix: str


def load_config() -> Config:
    load_dotenv(ROOT_DIR / ".env")

    missing = [
        name
        for name in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env values: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )

    with open(CONFIG_DIR / "watchlist.json") as f:
        watchlist = json.load(f)
    with open(CONFIG_DIR / "settings.json") as f:
        settings = json.load(f)

    return Config(
        ebay_client_id=os.environ["EBAY_CLIENT_ID"],
        ebay_client_secret=os.environ["EBAY_CLIENT_SECRET"],
        gmail_address=os.environ["GMAIL_ADDRESS"],
        gmail_app_password=os.environ["GMAIL_APP_PASSWORD"],
        email_to=os.environ.get("EMAIL_TO") or os.environ["GMAIL_ADDRESS"],
        players=watchlist["players"],
        discount_threshold_pct=float(settings["discount_threshold_pct"]),
        ebay_category_id=settings["ebay"]["category_id"],
        ebay_marketplace_id=settings["ebay"]["marketplace_id"],
        ebay_active_listing_limit=int(settings["ebay"]["active_listing_limit_per_player"]),
        ebay_sold_lookback_days=int(settings["ebay"]["sold_lookback_days"]),
        ebay_min_comps_required=int(settings["ebay"]["min_comps_required"]),
        craigslist_site=settings["craigslist"]["site"],
        craigslist_category=settings["craigslist"]["category"],
        craigslist_headless=bool(settings["craigslist"].get("headless", True)),
        seen_listings_path=ROOT_DIR / settings["dedupe"]["seen_listings_path"],
        prune_after_days=int(settings["dedupe"]["prune_after_days"]),
        email_subject_prefix=settings["email"]["subject_prefix"],
    )
