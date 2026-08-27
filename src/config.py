"""Loads .env secrets plus the two editable JSON config files."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src import focus, targets
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
    target_cards: list  # list[targets.TargetCard] -- explicit acquisition targets, see src/targets.py

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

    # --- CardPro 2.0 ---------------------------------------------------------
    # Comp-engine quality gates. These decide whether a number is a valuation
    # or just a restatement of the price -- see config/settings.json's
    # "valuation" comment and src/comps.py.
    valuation_min_comps_required: int
    valuation_half_life_days: int
    valuation_stale_after_days: int
    valuation_max_dispersion: float
    valuation_mad_threshold: float
    valuation_min_distinct_comp_dates: int
    valuation_min_comp_span_days: int
    require_flag_eligible_comp: bool

    # Hand-entered sold prices -- the only real market data available. See
    # config/settings.json's "sold_comps" comment and src/sold_comps.py.
    sold_comps_path: Path

    # Resale assumptions (NOT facts -- surfaced in the report alongside the
    # numbers they produce). See src/economics.py.
    fee_marketplace_pct: float
    fee_marketplace_fixed: float
    fee_payment_pct: float
    outbound_shipping: float
    supplies_cost: float
    sales_tax_pct: float
    resale_haircut_pct: float

    auction_required_margin_pct: float
    auction_ending_soon_hours: int

    immediate_alert_min_savings_dollars: float
    immediate_alert_min_discount_pct: float

    # Cheap-card rules. Being cheap is not the same as being junk, and a flat
    # dollar floor cannot tell the difference -- see config/settings.json's
    # "cheap_cards" comment and src/desirability.py.
    cheap_cards_enabled: bool
    cheap_price_ceiling: float
    # What counts as pocket change for the CHEAP FINDS section, and the
    # higher ceiling a genuinely scarce card is allowed to reach. Neither
    # makes any claim about value -- see config/settings.json "report".
    cheap_find_ceiling: float
    cool_cards_price_ceiling: float
    cheap_min_discount_pct: float
    cheap_min_savings_dollars: float
    cheap_require_desirable_attribute: bool

    # What reaches the email and how long it may be -- the price ceiling you
    # shop under, the exception that lets a genuinely exceptional dearer card
    # through, and the cap on how many listings print. See src/focus.py and
    # config/settings.json's "focus" comment.
    focus_rules: focus.FocusRules


def _section(settings: dict, name: str) -> dict:
    """A settings section, or {} when absent.

    Missing sections fall back to the defaults below rather than raising:
    a config file written before these settings existed must keep working,
    which is the same backwards-compatibility rule applied to stored data.
    """
    value = settings.get(name)
    return value if isinstance(value, dict) else {}


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

    valuation = _section(settings, "valuation")
    economics = _section(settings, "economics")
    auctions = _section(settings, "auctions")
    alerts = _section(settings, "alerts")
    cheap = _section(settings, "cheap_cards")
    sold = _section(settings, "sold_comps")
    focus_settings = _section(settings, "focus")

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
        target_cards=targets.load_targets(watchlist.get("target_cards", [])),
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
        valuation_min_comps_required=int(valuation.get("min_comps_required", settings["ebay"]["min_comps_required"])),
        valuation_half_life_days=int(valuation.get("half_life_days", 30)),
        valuation_stale_after_days=int(valuation.get("stale_after_days", 45)),
        valuation_max_dispersion=float(valuation.get("max_dispersion", 0.5)),
        valuation_mad_threshold=float(valuation.get("mad_threshold", 3.5)),
        valuation_min_distinct_comp_dates=int(valuation.get("min_distinct_comp_dates", 3)),
        valuation_min_comp_span_days=int(valuation.get("min_comp_span_days", 7)),
        sold_comps_path=ROOT_DIR / sold.get("path", "config/sold_comps.json"),
        require_flag_eligible_comp=bool(valuation.get("require_flag_eligible_comp", True)),
        fee_marketplace_pct=float(economics.get("marketplace_fee_pct", 13.25)),
        fee_marketplace_fixed=float(economics.get("marketplace_fixed_fee", 0.30)),
        fee_payment_pct=float(economics.get("payment_fee_pct", 0.0)),
        outbound_shipping=float(economics.get("outbound_shipping", 5.0)),
        supplies_cost=float(economics.get("supplies", 1.0)),
        sales_tax_pct=float(economics.get("sales_tax_pct", 0.0)),
        resale_haircut_pct=float(economics.get("resale_haircut_pct", 5.0)),
        auction_required_margin_pct=float(auctions.get("required_margin_pct", 25.0)),
        auction_ending_soon_hours=int(auctions.get("ending_soon_hours", 24)),
        immediate_alert_min_savings_dollars=float(alerts.get("immediate_alert_min_savings_dollars", 150.0)),
        immediate_alert_min_discount_pct=float(alerts.get("immediate_alert_min_discount_pct", 40.0)),
        cheap_cards_enabled=bool(cheap.get("enabled", True)),
        cheap_price_ceiling=float(cheap.get("price_ceiling", 10.0)),
        cheap_find_ceiling=float(_section(settings, "report").get("cheap_find_ceiling", 15.0)),
        cool_cards_price_ceiling=float(
            _section(settings, "focus").get("cool_cards_price_ceiling", 100.0)
        ),
        cheap_min_discount_pct=float(cheap.get("min_discount_pct", 50.0)),
        cheap_min_savings_dollars=float(cheap.get("min_savings_dollars", 3.0)),
        cheap_require_desirable_attribute=bool(cheap.get("require_desirable_attribute", True)),
        # A config file written before focus existed has no "focus" section,
        # and must not silently acquire a $40 ceiling and a length cap on
        # upgrade -- so the fallback here is focus.OFF (everything, as
        # before), not focus.FocusRules(). The shipped settings.json turns
        # it on explicitly.
        focus_rules=(
            focus.FocusRules(
                enabled=bool(focus_settings.get("enabled", True)),
                price_ceiling=float(focus_settings.get("price_ceiling", 40.0)),
                exceptional_min_discount_pct=float(
                    focus_settings.get("exceptional_min_discount_pct", 50.0)
                ),
                exceptional_min_savings_dollars=float(
                    focus_settings.get("exceptional_min_savings_dollars", 100.0)
                ),
                require_auction_bidding_room=bool(
                    focus_settings.get("require_auction_bidding_room", True)
                ),
                max_listings=int(focus_settings.get("max_listings", 40)),
                max_per_section=int(focus_settings.get("max_per_section", 10)),
                cool_cards_price_ceiling=float(
                    focus_settings.get("cool_cards_price_ceiling", 100.0)
                ),
            )
            if focus_settings
            else focus.OFF
        ),
    )
