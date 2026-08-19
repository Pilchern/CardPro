"""Structured card identity extraction from a listing title.

Same philosophy as matcher.py: keyword/regex matching, no ML, no fuzzy
guessing. Every field is optional and carries a confidence + source so
downstream code (comps, reporting) can tell "we know this is a Silver
Prizm" apart from "we have no idea what parallel this is" -- a missing
value means unknown, never a guess.

This is deliberately additive: nothing here changes matcher.py's existing
player/grade/rookie detection, and nothing downstream is required to use
it yet. It's the first building block toward the hierarchical comp
matching described in docs/AUDIT_AND_ROADMAP.md (year/set/parallel-aware
comps instead of just player + raw-vs-graded + price tier).

The keyword lists below are intentionally plain data, not baked into the
matching logic -- add a brand, set, or parallel name to the relevant list
and it's picked up with no code changes, same spirit as
config/watchlist.json.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import Optional

# Extend freely -- these are just data, matched case-insensitively as
# whole-word/phrase keywords against the title.
MANUFACTURERS = [
    "Panini", "Topps", "Bowman", "Upper Deck", "Leaf", "Donruss", "Fleer", "Score",
]

SET_KEYWORDS = [
    "Prizm", "Chrome", "Optic", "Select", "Mosaic", "Contenders", "Immaculate",
    "National Treasures", "Flawless", "Certified", "Absolute", "Spectra", "Phoenix",
    "Chronicles", "Donruss Optic", "Bowman Chrome", "Topps Chrome", "Finest",
    "Stadium Club", "Heritage", "Archives", "Update", "Gallery", "Obsidian",
    "Recon", "Revolution", "Origins", "Pinnacle", "Playbook", "Illusions",
]

PARALLEL_KEYWORDS = [
    "Silver", "Gold", "Green", "Blue", "Red", "Orange", "Purple", "Pink", "Black",
    "White", "Refractor", "Wave", "Shimmer", "Mojo", "Kaboom", "Downtown",
    "Color Blast", "Cracked Ice", "Camo", "Tiger", "Zebra", "Disco", "Scope",
    "Hyper", "Ice", "Superfractor", "X-Fractor", "Sepia",
]

AUTOGRAPH_KEYWORDS = ["autograph", "autographed", "auto", "signed", "signature"]
MEMORABILIA_KEYWORDS = ["patch", "relic", "jersey", "memorabilia", "swatch", "game-used", "game used"]

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})(?:-\d{2})?\b")
CARD_NUMBER_RE = re.compile(r"#([A-Za-z0-9-]{1,10})\b")
SERIAL_NUMBER_RE = re.compile(r"\b(\d{1,4})\s*/\s*(\d{1,5})\b")
LOT_OF_N_RE = re.compile(r"\blot\s+of\s+\d+\b", re.IGNORECASE)
CARD_LOT_RE = re.compile(r"\b\d+\s*-?\s*card\s+lot\b", re.IGNORECASE)
BARE_LOT_RE = re.compile(r"\blot\b", re.IGNORECASE)


@dataclass
class Field:
    """One extracted attribute. value=None means "unknown", never a guess."""

    value: object = None
    confidence: str = "none"  # "high" | "medium" | "low" | "none"
    source: str = "title"


@dataclass
class CardIdentity:
    year: Field = dataclass_field(default_factory=Field)
    manufacturer: Field = dataclass_field(default_factory=Field)
    set_name: Field = dataclass_field(default_factory=Field)
    parallel: Field = dataclass_field(default_factory=Field)
    card_number: Field = dataclass_field(default_factory=Field)
    serial_number: Field = dataclass_field(default_factory=Field)  # value e.g. "23/99"
    is_autograph: Field = dataclass_field(default_factory=lambda: Field(False, "high"))
    is_memorabilia: Field = dataclass_field(default_factory=lambda: Field(False, "high"))
    is_lot: Field = dataclass_field(default_factory=lambda: Field(False, "high"))


def _find_keyword(title: str, keywords: list[str]) -> Optional[str]:
    """Returns the canonical keyword (as spelled in the list) whose whole
    phrase appears in the title, case-insensitively. Longer keywords are
    checked first so e.g. "Donruss Optic" wins over a bare "Optic" match.
    """
    lowered = title.lower()
    for keyword in sorted(keywords, key=len, reverse=True):
        if re.search(rf"\b{re.escape(keyword.lower())}\b", lowered):
            return keyword
    return None


def _keyword_field(title: str, keywords: list[str]) -> Field:
    match = _find_keyword(title, keywords)
    if match:
        return Field(value=match, confidence="high", source="title")
    return Field(value=None, confidence="none", source="title")


def extract_card_identity(title: str) -> CardIdentity:
    """Best-effort structured extraction from a listing title. Every field
    degrades to unknown (year/manufacturer/set_name/parallel/card_number/
    serial_number) or a confident False (is_autograph/is_memorabilia/
    is_lot -- sellers reliably mention these when true) rather than
    guessing.
    """
    year_match = YEAR_RE.search(title)
    year_field = (
        Field(value=int(year_match.group(1)), confidence="high", source="title")
        if year_match
        else Field(value=None, confidence="none", source="title")
    )

    card_number_match = CARD_NUMBER_RE.search(title)
    card_number_field = (
        Field(value=card_number_match.group(1), confidence="high", source="title")
        if card_number_match
        else Field(value=None, confidence="none", source="title")
    )

    serial_match = SERIAL_NUMBER_RE.search(title)
    serial_field = (
        Field(value=f"{serial_match.group(1)}/{serial_match.group(2)}", confidence="high", source="title")
        if serial_match
        else Field(value=None, confidence="none", source="title")
    )

    is_lot = bool(LOT_OF_N_RE.search(title) or CARD_LOT_RE.search(title))
    lot_confidence = "high"
    if not is_lot and BARE_LOT_RE.search(title):
        is_lot, lot_confidence = True, "medium"  # bare "lot" with no count -- still very likely a multi-card lot

    return CardIdentity(
        year=year_field,
        manufacturer=_keyword_field(title, MANUFACTURERS),
        set_name=_keyword_field(title, SET_KEYWORDS),
        parallel=_keyword_field(title, PARALLEL_KEYWORDS),
        card_number=card_number_field,
        serial_number=serial_field,
        is_autograph=Field(bool(_find_keyword(title, AUTOGRAPH_KEYWORDS)), "high", "title"),
        is_memorabilia=Field(bool(_find_keyword(title, MEMORABILIA_KEYWORDS)), "high", "title"),
        is_lot=Field(is_lot, lot_confidence, "title"),
    )
