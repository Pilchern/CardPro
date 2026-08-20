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

COMP_LEVEL_LABELS = {
    "exact": "exact card match",
    "near_exact": "near-exact match",
    "family": "same year/set",
    "price_tier": "price-tier estimate",
}

# Action labels are derived straight from comp_confidence (comps.py's
# CONFIDENCE_BY_LEVEL) -- never a separate judgment call, and never an
# instruction to buy. They exist to help you scan a long list and decide
# what to open first, not to replace looking at the listing yourself.
ACTION_LABELS = {"high": "LOOK NOW", "medium": "WATCH", "low": "LOW CONFIDENCE"}


def _action_label(deal: Listing) -> Optional[str]:
    return ACTION_LABELS.get(deal.comp_confidence)


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
        f"(actions -- based only on how well-matched the comp is, not a buy recommendation: "
        f"LOOK NOW = exact card match backing the price; WATCH = near-exact match (grade/grader "
        f"may differ from the comps); LOW CONFIDENCE = broader match (same year/set only, or just "
        f"a price-tier estimate) -- double-check manually)\n"
    ]
    top_picks_section = _build_top_picks_section(ranked)
    if top_picks_section:
        lines.append(top_picks_section)
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
        confidence_note = ""
        if deal.comp_confidence and deal.comp_level_matched:
            level_label = COMP_LEVEL_LABELS.get(deal.comp_level_matched, deal.comp_level_matched)
            confidence_note = f", {deal.comp_confidence.upper()} confidence -- {level_label}"
        card_line = _build_card_identity_line(deal.card_identity)
        price_line = _build_price_line(deal)
        action = _action_label(deal)
        action_prefix = f"[{action}] " if action else ""
        lines.append(
            f"{i}. {action_prefix}{deal.player} -- {grading}{tags}\n"
            f"   ${deal.dollar_savings:,.2f} saved ({deal.pct_under_market:.0f}% under market)   |   "
            f"{SOURCE_LABELS.get(deal.source, deal.source)}\n"
            f"   {price_line}   Comp median: ${deal.comp_median:,.2f} "
            f"(n={deal.comp_sample_size}{confidence_note}){fallback_note}\n"
            f"{card_line}"
            f"   {deal.title}\n"
            f"   {deal.url}\n"
        )
    lines.append(
        f"\nThreshold: flagging listings {threshold_pct:.0f}%+ under their comp median AND "
        f"at least ${min_savings_dollars:,.2f} saved."
    )
    return subject, "\n".join(lines) + cl_section


def _build_price_line(deal: Listing) -> str:
    """"Price: $X" alone when shipping is unknown (same as before shipping
    tracking existed -- never silently assumed as $0), or a "price +
    shipping = total" breakdown when it's known. dollar_savings/
    pct_under_market are already computed against total_cost (see
    main.flag_deals/flag_deals_hierarchical), so this just makes that
    number's basis visible instead of leaving "Price: $X" looking like the
    full story when it isn't.
    """
    if deal.shipping_price is None:
        return f"Price: ${deal.price:,.2f} (shipping unknown -- actual cost may be higher)"
    if deal.shipping_price == 0:
        return f"Price: ${deal.price:,.2f} + free shipping"
    return f"Price: ${deal.price:,.2f} + ${deal.shipping_price:,.2f} shipping = ${deal.total_cost:,.2f} total"


TOP_PICKS_MIN_DEALS = 4  # below this, the numbered list below is already short enough to skim directly


def _build_top_picks_section(ranked: list[Listing]) -> str:
    """A compact, at-a-glance summary of the top 3 deals by $ saved, shown
    above the full numbered list -- only when the list is long enough that
    skimming actually helps (see TOP_PICKS_MIN_DEALS). Purely a readability
    aid: it repeats data already in the full list below, never a separate
    judgment call.
    """
    if len(ranked) < TOP_PICKS_MIN_DEALS:
        return ""
    lines = ["TOP PICKS:"]
    for deal in ranked[:3]:
        grading = f"{deal.grader} {deal.grade}" if deal.card_type == "graded" else "raw/ungraded"
        action = _action_label(deal)
        action_suffix = f" -- {action}" if action else ""
        lines.append(
            f"  * {deal.player} -- {grading} -- ${deal.dollar_savings:,.2f} saved "
            f"({deal.pct_under_market:.0f}% under market){action_suffix}"
        )
    return "\n".join(lines) + "\n"


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
