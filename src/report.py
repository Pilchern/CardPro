"""Builds the plain-text email body from a ranked list of flagged eBay
deals, plus a Craigslist quick-check section (see craigslist_links.py --
Craigslist isn't scraped, just linked)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.models import Listing

SOURCE_LABELS = {"ebay": "eBay", "ebay-alert": "eBay (saved-search alert)"}


def rank_deals(deals: list[Listing]) -> list[Listing]:
    return sorted(deals, key=lambda d: d.pct_under_market or 0, reverse=True)


def build_report(
    deals: list[Listing],
    threshold_pct: float,
    run_date: date,
    craigslist_links: Optional[dict[str, str]] = None,
    ebay_enabled: bool = True,
) -> tuple[str, str]:
    """Returns (subject, body)."""
    ranked = rank_deals(deals)
    date_str = run_date.strftime("%B %d, %Y")
    cl_section = _build_craigslist_section(craigslist_links)

    if not ebay_enabled:
        subject = f"eBay not configured ({date_str})"
        body = (
            f"Card deal scan for {date_str}: eBay wasn't scanned today because neither "
            f"EBAY_CLIENT_ID/EBAY_CLIENT_SECRET nor ebay_alerts.enabled are set up in "
            f".env / config/settings.json. This is expected, not an error -- once "
            f"either eBay path is configured, deals will resume showing up here "
            f"automatically."
        )
        return subject, body + cl_section

    if not ranked:
        subject = f"No deals today ({date_str})"
        body = (
            f"Card deal scan for {date_str}: nothing cleared the "
            f"{threshold_pct:.0f}% under comp median threshold today.\n\n"
            f"This is an automated 'still running' confirmation, not an error."
        )
        return subject, body + cl_section

    subject = f"{len(ranked)} card deal{'s' if len(ranked) != 1 else ''} found ({date_str})"

    lines = [f"Card deal scan for {date_str} -- {len(ranked)} listing(s) below market:\n"]
    for i, deal in enumerate(ranked, start=1):
        grading = f"{deal.grader} {deal.grade}" if deal.card_type == "graded" else "raw/ungraded"
        if deal.title_truncated:
            grading += " (grade uncertain -- eBay truncated the title, actual grade may differ)"
        fallback_note = " [comp = active-listing proxy, not real sold data]" if deal.comp_is_fallback else ""
        lines.append(
            f"{i}. {deal.player} -- {grading}\n"
            f"   Price: ${deal.price:,.2f}   Comp median: ${deal.comp_median:,.2f} "
            f"(n={deal.comp_sample_size}){fallback_note}\n"
            f"   {deal.pct_under_market:.0f}% under market   |   {SOURCE_LABELS.get(deal.source, deal.source)}\n"
            f"   {deal.title}\n"
            f"   {deal.url}\n"
        )
    lines.append(f"\nThreshold: flagging listings {threshold_pct:.0f}%+ under their comp median.")
    return subject, "\n".join(lines) + cl_section


def _build_craigslist_section(craigslist_links: Optional[dict[str, str]]) -> str:
    if not craigslist_links:
        return ""
    lines = [
        "\n\n---\nCraigslist quick check (not auto-scanned -- eyeball these yourself):"
    ]
    for player, url in craigslist_links.items():
        lines.append(f"  {player}: {url}")
    return "\n".join(lines)
