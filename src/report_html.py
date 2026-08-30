"""The HTML face of the daily report.

This module renders a ``report.ReportModel`` -- it decides nothing. Which
cards, in which sections, with which caveats and in which words: all of that
is settled in ``report.build_model`` and arrives here already made. What is
here is typography, and only typography.

That division is the point. The email goes out as ``multipart/alternative``:
a plain-text part and an HTML part, and a mail client shows whichever it
prefers. If the two parts were built by two pieces of code that each decided
what to say, they would eventually say different things about the same card
-- and the reader would have no way of knowing which half was lying. So the
HTML part is a rendering of the same model the text part renders, and a
renderer that wanted to add a fact would have nowhere to get it from.

Design constraints that are not preferences:

* **Everything interpolated is escaped.** Card titles are seller-authored
  text off eBay. A title containing ``<`` is not exotic and a title
  containing a tag is not impossible; either way, nothing from a listing
  reaches the output without going through ``html.escape``.
* **Tables, not flexbox or grid.** Email clients are not browsers. The
  layout here is the boring one that renders the same in Apple Mail, the
  Gmail app and Outlook.
* **Inline styles for everything that must be right.** Some clients strip
  ``<style>``. The ``<style>`` block carries only progressive enhancement --
  dark mode and one width rule -- so an email that loses it is plainer, not
  broken.
* **No images, no web fonts, no tracking.** Nothing to load, nothing to
  block, nothing that looks broken with remote content off (which is the
  default in Apple Mail). A system font stack renders instantly and looks
  native on the phone this is actually read on.

The colour rule is a content rule, not a palette choice. A saturated accent
means *CardPro is making a claim about value here*, and only the sections
that have a comp behind them get one. The sections that exist to show a card
without valuing it -- COOL CARDS, CHEAP FINDS, CHEAP AUCTIONS, YOUNG CORE,
and the not-a-recommendation sections below them -- get muted greys. The
whole project is built on keeping "this is cheap" apart from "this is
underpriced"; an email that paints them the same colour argues with its own
text.
"""
from __future__ import annotations

import html
import re
from typing import Optional

from src import report

# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------

#: Light theme. Dark equivalents live in the media query in _STYLE and are
#: applied by class, because no email client supports CSS variables reliably.
INK = "#15181d"
MUTED = "#5c6773"
FAINT = "#8b95a3"
PAGE_BG = "#f2f4f7"
CARD_BG = "#ffffff"
BORDER = "#e2e6ec"
CHIP_BG = "#eef1f5"

#: Per-section accent. Saturated = CardPro is standing behind a number in
#: this section. Grey = it is not, and the colour says so before the
#: subtitle has to. Getting this backwards would make the browse sections
#: look like deals, which is the single confusion this report is built to
#: prevent.
SECTION_ACCENTS = {
    report.SECTION_ACT_NOW: "#c62f1c",
    report.SECTION_TOP_OPPORTUNITIES: "#12784a",
    report.SECTION_TARGET_HITS: "#1a63c9",
    report.SECTION_OFFERS: "#5350b8",
    report.SECTION_CHEAP_AUCTIONS: "#7b8794",
    report.SECTION_AUCTIONS: "#7b8794",
    report.SECTION_COOL_CARDS: "#7b8794",
    report.SECTION_CHEAP_FINDS: "#7b8794",
    report.SECTION_INVESTMENT: "#7b8794",
    report.SECTION_WATCH: "#98a2b0",
    report.SECTION_NEEDS_REVIEW: "#98a2b0",
    report.SECTION_PRICE_DROPS: "#98a2b0",
}
DEFAULT_ACCENT = "#7b8794"

FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
    "Arial,sans-serif"
)

#: Only what cannot be inlined: the dark palette and the one width rule.
#: Everything load-bearing is inlined on the element, so a client that drops
#: this block still gets the light email rather than an unstyled one.
_STYLE = """
:root { color-scheme: light dark; supported-color-schemes: light dark; }
@media (prefers-color-scheme: dark) {
  .page      { background:#0d0f12 !important; }
  .panel     { background:#16191e !important; border-color:#262b33 !important; }
  .ink       { color:#e9ecf0 !important; }
  .muted     { color:#9aa4b2 !important; }
  .faint     { color:#78828f !important; }
  .chip      { background:#232830 !important; color:#c3cbd6 !important; }
  .hairline  { border-color:#262b33 !important; }
  .btn       { background:#232830 !important; color:#dfe4ea !important;
               border-color:#39404a !important; }
  .accentbar { opacity:0.85; }
}
@media only screen and (max-width:480px) {
  .pad { padding-left:14px !important; padding-right:14px !important; }
}
"""

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def esc(text) -> str:
    """Escape for element content. Everything that came from a listing, a
    seller or a config file goes through this."""
    return html.escape("" if text is None else str(text), quote=False)


def attr(text) -> str:
    """Escape for an attribute value -- quotes included."""
    return html.escape("" if text is None else str(text), quote=True)


def unfill(text: str) -> str:
    """Undo ``textwrap.fill``: one paragraph of prose per blank-line group.

    The model carries its long explanatory passages already wrapped for the
    plain-text email. Re-flowing them here beats keeping a second copy of
    the same paragraphs in this file -- those passages are the report's
    disclosures, and two copies of a disclosure is one copy that can go
    stale.
    """
    paragraphs = []
    for chunk in re.split(r"\n\s*\n", (text or "").strip()):
        paragraphs.append(" ".join(part.strip() for part in chunk.splitlines() if part.strip()))
    return "\n\n".join(p for p in paragraphs if p)


def linkify(escaped: str) -> str:
    """Turn URLs in already-escaped text into links. Runs after escaping, so
    it is inserting markup into text that can no longer contain any."""
    def replace(match):
        url = match.group(0)
        return '<a href="{}" style="color:inherit;">{}</a>'.format(attr(url), url)

    return _URL_RE.sub(replace, escaped)


def _p(text: str, *, size: float = 14, color: str = MUTED, klass: str = "muted",
       top: int = 0, bottom: int = 12, line: str = "1.55") -> str:
    return (
        '<p class="{klass}" style="margin:{top}px 0 {bottom}px;font-family:{font};'
        'font-size:{size}px;line-height:{line};color:{color};">{text}</p>'
    ).format(klass=klass, top=top, bottom=bottom, font=FONT, size=size, line=line,
             color=color, text=text)


def _prose(text: str, **kwargs) -> str:
    """A wrapped plain-text passage as one or more paragraphs."""
    return "".join(
        _p(linkify(esc(para)), **kwargs) for para in unfill(text).split("\n\n") if para
    )


# ---------------------------------------------------------------------------
# card blocks
# ---------------------------------------------------------------------------


def _chips(labels) -> str:
    if not labels:
        return ""
    chips = "".join(
        '<span class="chip" style="display:inline-block;background:{bg};color:{color};'
        'font-family:{font};font-size:11px;font-weight:600;letter-spacing:0.04em;'
        'padding:3px 8px;border-radius:10px;margin:0 6px 6px 0;white-space:nowrap;">'
        "{label}</span>".format(bg=CHIP_BG, color=MUTED, font=FONT, label=esc(label))
        for label in labels
    )
    return '<div style="margin:0 0 10px;">{}</div>'.format(chips)


def _field_rows(fields) -> str:
    """The labelled lines, as a two-column table.

    The label column is narrow and quiet on purpose. These labels are
    navigation -- the reader is looking for "Cost" or "Max bid" -- and the
    value is what they came to read, so the value gets the contrast.
    """
    rows = []
    for label, value in fields:
        rows.append(
            '<tr>'
            '<td class="lab faint" valign="top" style="width:96px;padding:0 12px 7px 0;'
            'font-family:{font};font-size:11px;font-weight:700;letter-spacing:0.04em;'
            'text-transform:uppercase;color:{faint};line-height:1.7;white-space:nowrap;">'
            "{label}</td>"
            '<td class="ink" valign="top" style="padding:0 0 7px;font-family:{font};'
            'font-size:14px;line-height:1.55;color:{ink};">{value}</td>'
            "</tr>".format(font=FONT, faint=FAINT, ink=INK, label=esc(label),
                           value=linkify(esc(value)))
        )
    if not rows:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="width:100%;border-collapse:collapse;">{}</table>'
    ).format("".join(rows))


def _button(url: str, source_label: Optional[str]) -> str:
    source = ' <span class="faint" style="color:{};">via {}</span>'.format(
        FAINT, esc(source_label)
    ) if source_label else ""
    return (
        '<div style="margin:12px 0 0;">'
        '<a class="btn" href="{url}" style="display:inline-block;background:{chip};'
        'color:{ink};font-family:{font};font-size:13px;font-weight:600;'
        "text-decoration:none;padding:9px 16px;border-radius:6px;"
        'border:1px solid {border};">View listing &rarr;</a>'
        '<span style="font-family:{font};font-size:12px;margin-left:10px;">{source}</span>'
        "</div>"
    ).format(url=attr(url), chip=CHIP_BG, ink=INK, font=FONT, border=BORDER, source=source)


def render_card(card, accent: str) -> str:
    """One card, as a panel with the section's accent down its left edge."""
    headline = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="width:100%;border-collapse:collapse;margin:0 0 10px;"><tr>'
        '<td valign="top" style="width:26px;padding:0;font-family:{font};font-size:13px;'
        'font-weight:700;color:{faint};line-height:1.45;" class="faint">{index}.</td>'
        '<td valign="top" style="padding:0;">'
        '<div class="ink" style="font-family:{font};font-size:16px;font-weight:700;'
        'color:{ink};line-height:1.35;">{player}</div>'
        '<div class="muted" style="font-family:{font};font-size:13px;color:{muted};'
        'line-height:1.45;margin-top:2px;">{description}</div>'
        "</td></tr></table>"
    ).format(
        font=FONT, faint=FAINT, ink=INK, muted=MUTED,
        index=card.index,
        player=esc(card.player),
        description=esc(" · ".join(part for part in (card.description, card.grade) if part)),
    )

    labels = list(card.tags)
    if card.target_label is not None:
        labels.append("TARGET: {}".format(card.target_label))

    body = headline + _chips(labels) + _field_rows(card.fields)
    if card.title is not None:
        body += _p(
            esc(card.title), size=12, color=FAINT, klass="faint", top=8, bottom=0,
        )
    if card.url is not None:
        body += _button(card.url, card.source_label)

    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="width:100%;border-collapse:separate;margin:0 0 10px;">'
        "<tr>"
        '<td class="accentbar" width="3" style="width:3px;background:{accent};'
        'border-radius:3px 0 0 3px;font-size:0;line-height:0;">&nbsp;</td>'
        '<td class="panel pad" style="background:{bg};border:1px solid {border};'
        'border-left:0;border-radius:0 6px 6px 0;padding:14px 18px;">{body}</td>'
        "</tr></table>"
    ).format(accent=accent, bg=CARD_BG, border=BORDER, body=body)


def render_section(key: str, cards) -> str:
    accent = SECTION_ACCENTS.get(key, DEFAULT_ACCENT)
    title = report.SECTION_TITLES[key]
    subtitle = report.SECTION_SUBTITLES.get(key)

    head = (
        '<div style="margin:26px 0 10px;">'
        '<div class="ink" style="font-family:{font};font-size:15px;font-weight:800;'
        'letter-spacing:0.03em;color:{ink};line-height:1.4;">{title}</div>'
        '<div style="height:3px;width:44px;background:{accent};border-radius:2px;'
        'margin:7px 0 0;font-size:0;line-height:0;">&nbsp;</div>'
        "</div>"
    ).format(font=FONT, ink=INK, title=esc(title), accent=accent)

    if subtitle:
        head += _prose(subtitle, size=12.5, bottom=14)

    return head + "".join(render_card(card, accent) for card in cards)


# ---------------------------------------------------------------------------
# footer prose
# ---------------------------------------------------------------------------


def _footer_html(text: str) -> str:
    """The diagnostic footers, kept as structure rather than redesigned.

    Their shape carries meaning -- which counts belong under which heading --
    so it is preserved rather than flattened into prose. What changes is only
    that it is set in a readable size with the headings picked out.

    The shape, as the text renderer emits it: sub-blocks separated by blank
    lines; the first unindented line of a sub-block is its heading; further
    unindented lines are that heading's wrapped prose; an indented line is a
    list item, and a more-deeply-indented line continues the item above it.

    "First unindented line" is load-bearing rather than fussy. Treating every
    unindented line as a heading turned the three wrapped lines of the SEARCH
    COVERAGE explanation into three uppercase headings shouting mid-sentence
    fragments -- "EACH SLICE BELOW", then "PULLS PART OF THE MARKET...".
    """
    if not text:
        return ""
    out: list = []
    items: list = []
    prose: list = []
    expecting_heading = True

    def flush_prose():
        if prose:
            out.append(_p(linkify(esc(" ".join(prose))), size=12.5, bottom=10))
            prose.clear()

    def flush_items():
        if not items:
            return
        rows = "".join(
            '<li style="margin:0 0 5px;">{}</li>'.format(linkify(esc(item)))
            for item in items
        )
        out.append(
            '<ul class="muted" style="margin:0 0 14px;padding-left:18px;font-family:{font};'
            'font-size:12.5px;line-height:1.6;color:{muted};">{rows}</ul>'.format(
                font=FONT, muted=MUTED, rows=rows
            )
        )
        items.clear()

    for line in text.splitlines():
        if not line.strip():
            flush_prose()
            flush_items()
            expecting_heading = True
            continue
        if not line.startswith(" "):
            if expecting_heading:
                flush_prose()
                flush_items()
                out.append(
                    '<div class="ink" style="margin:18px 0 8px;font-family:{font};'
                    'font-size:13px;font-weight:800;letter-spacing:0.02em;color:{ink};'
                    'line-height:1.4;">{line}</div>'.format(
                        font=FONT, ink=INK, line=esc(line.strip())
                    )
                )
                expecting_heading = False
            else:
                flush_items()
                prose.append(line.strip())
            continue
        # Indented: a list item, or a continuation of the one above it.
        flush_prose()
        expecting_heading = False
        if line.startswith("    ") and items:
            items[-1] += " " + line.strip()
        else:
            items.append(line.strip().lstrip("* ").strip() or line.strip())
    flush_prose()
    flush_items()
    return "".join(out)


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------


def _header(model) -> str:
    counts = ""
    if model.summary_line:
        counts = "".join(
            '<span class="chip" style="display:inline-block;background:{bg};color:{ink};'
            'font-family:{font};font-size:12px;font-weight:600;padding:4px 10px;'
            'border-radius:12px;margin:0 6px 6px 0;">{part}</span>'.format(
                bg=CHIP_BG, ink=INK, font=FONT, part=esc(part.strip())
            )
            for part in model.summary_line.split("|")
        )
        counts = '<div style="margin:10px 0 0;">{}</div>'.format(counts)

    return (
        '<div style="margin:0 0 6px;">'
        '<div class="ink" style="font-family:{font};font-size:12px;font-weight:800;'
        'letter-spacing:0.16em;color:{ink};">CARDPRO DAILY</div>'
        '<div class="muted" style="font-family:{font};font-size:13px;color:{muted};'
        'margin-top:4px;">{date}</div>{counts}</div>'
    ).format(font=FONT, ink=INK, muted=MUTED, date=esc(model.date_full), counts=counts)


def render(model) -> str:
    """A ``report.ReportModel`` as a complete HTML document."""
    body = [_header(model)]

    if model.focus_line:
        body.append(_prose(model.focus_line, size=12, color=FAINT, klass="faint",
                           top=6, bottom=0))

    body.append(
        '<div class="hairline" style="border-top:1px solid {};margin:18px 0 0;'
        'font-size:0;line-height:0;">&nbsp;</div>'.format(BORDER)
    )

    if model.not_configured is not None:
        body.append(_prose(model.not_configured, top=16))
    else:
        if model.lede:
            body.append(_prose(model.lede, size=13.5, top=16, bottom=0))
        if model.empty_state:
            body.append(_prose(model.empty_state, size=13, top=16, bottom=0))
        for key, cards in (model.sections or {}).items():
            body.append(render_section(key, cards))
        if model.thresholds:
            body.append(
                '<div class="hairline" style="border-top:1px solid {};margin:26px 0 0;'
                'font-size:0;line-height:0;">&nbsp;</div>'.format(BORDER)
            )
            body.append(_prose(model.thresholds, size=12, color=FAINT, klass="faint",
                               top=14, bottom=0))
        if model.assumptions:
            # Same heading-and-bullets shape as the footers below, so it gets
            # the same converter. Run through _prose it collapsed into one
            # paragraph with stray asterisks where the bullets had been.
            body.append(_footer_html(model.assumptions))

    if model.footer:
        body.append(
            '<div class="hairline" style="border-top:1px solid {};margin:24px 0 0;'
            'font-size:0;line-height:0;">&nbsp;</div>'.format(BORDER)
        )
        body.append(_footer_html(model.footer))

    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        "<title>{title}</title><style>{style}</style></head>"
        '<body class="page" style="margin:0;padding:0;background:{page};">'
        '<table role="presentation" class="page" cellpadding="0" cellspacing="0" '
        'border="0" width="100%" style="width:100%;background:{page};'
        'border-collapse:collapse;">'
        '<tr><td class="page" align="center" bgcolor="{page}" '
        'style="background:{page};padding:20px 12px 32px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" '
        'style="width:100%;max-width:600px;border-collapse:collapse;">'
        '<tr><td class="pad" style="padding:0 4px;">{body}</td></tr>'
        "</table></td></tr></table></body></html>"
    ).format(
        title=esc(model.subject), style=_STYLE, page=PAGE_BG, body="".join(body)
    )
