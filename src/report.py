"""Builds the plain-text email body from a ranked list of flagged eBay
deals, plus a Craigslist quick-check section (see craigslist_links.py --
Craigslist isn't scraped, just linked).

Ranking is by dollar amount saved, not percent under market -- percent
alone lets trivial deals through (50% off a $5 common is still just
$2.50), so it's a worse "is this worth your time" signal than the actual
dollar figure. Percent is still shown for context.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.card_identity import CardIdentity
from src.models import Listing

SOURCE_LABELS = {"ebay": "eBay", "ebay-alert": "eBay (saved-search alert)"}


def rank_deals(deals: list[Listing]) -> list[Listing]:
    return sorted(deals, key=lambda d: d.dollar_savings or 0, reverse=True)


def build_report(
    deals: list[Listing],
    threshold_pct: float,
    run_date: date,
    craigslist_links: Optional[dict[str, str]] = None,
    ebay_enabled: bool = True,
    min_savings_dollars: float = 0,
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
            f"{threshold_pct:.0f}% under comp median AND ${min_savings_dollars:,.2f} "
            f"saved thresholds today.\n\n"
            f"This is an automated 'still running' confirmation, not an error."
        )
        return subject, body + cl_section

    subject = f"{len(ranked)} card deal{'s' if len(ranked) != 1 else ''} found ({date_str})"

    lines = [
        f"Card deal scan for {date_str} -- {len(ranked)} listing(s) below market, ranked by $ saved:\n"
        f"(tags: YOUNG CORE = betting on this player's long-term growth, not just today's price; "
        f"ROOKIE CARD = title says RC/Rookie)\n"
    ]
    for i, deal in enumerate(ranked, start=1):
        grading = f"{deal.grader} {deal.grade}" if deal.card_type == "graded" else "raw/ungraded"
        if deal.title_truncated:
            grading += " (grade uncertain -- eBay truncated the title, actual grade may differ)"
        tag_parts = []
        if deal.player_tier == "young_core":
            tag_parts.append("YOUNG CORE")
        if deal.is_rookie_card:
            tag_parts.append("ROOKIE CARD")
        if deal.card_identity and deal.card_identity.is_autograph.value:
            tag_parts.append("AUTO")
        if deal.card_identity and deal.card_identity.is_memorabilia.value:
            tag_parts.append("MEM")
        tags = f"  [{' + '.join(tag_parts)}]" if tag_parts else ""
        fallback_note = " [comp = active-listing proxy, not real sold data]" if deal.comp_is_fallback else ""
        card_line = _build_card_identity_line(deal.card_identity)
        lines.append(
            f"{i}. {deal.player} -- {grading}{tags}\n"
            f"   ${deal.dollar_savings:,.2f} saved ({deal.pct_under_market:.0f}% under market)   |   "
            f"{SOURCE_LABELS.get(deal.source, deal.source)}\n"
            f"   Price: ${deal.price:,.2f}   Comp median: ${deal.comp_median:,.2f} "
            f"(n={deal.comp_sample_size}){fallback_note}\n"
            f"{card_line}"
            f"   {deal.title}\n"
            f"   {deal.url}\n"
        )
    lines.append(
        f"\nThreshold: flagging listings {threshold_pct:.0f}%+ under their comp median AND "
        f"at least ${min_savings_dollars:,.2f} saved."
    )
    return subject, "\n".join(lines) + cl_section


def _build_card_identity_line(identity: Optional[CardIdentity]) -> str:
    """One "Card: ..." line built from whichever identity fields are
    actually known -- omitted entirely if nothing was extracted, since an
    empty "Card:" line would be noise, not information. See card_identity.py:
    every field here is either a confident extraction or unknown, never a
    guess, so this line only ever shows what we're actually sure of.
    """
    if identity is None:
        return ""
    parts = []
    if identity.year.value is not None:
        parts.append(str(identity.year.value))
    if identity.manufacturer.value is not None:
        parts.append(identity.manufacturer.value)
    if identity.set_name.value is not None:
        parts.append(identity.set_name.value)
    if identity.parallel.value is not None:
        parts.append(identity.parallel.value)
    if identity.card_number.value is not None:
        parts.append(f"#{identity.card_number.value}")
    if identity.serial_number.value is not None:
        parts.append(f"({identity.serial_number.value})")
    if not parts:
        return ""
    return f"   Card: {' '.join(parts)}\n"


def _build_craigslist_section(craigslist_links: Optional[dict[str, str]]) -> str:
    if not craigslist_links:
        return ""
    lines = [
        "\n\n---\nCraigslist quick check (not auto-scanned -- eyeball these yourself):"
    ]
    for player, url in craigslist_links.items():
        lines.append(f"  {player}: {url}")
    return "\n".join(lines)
