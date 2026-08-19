"""Simple, inspectable title matching: which watchlist player is this
listing about, and is it graded or raw?

No fuzzy matching, no ML -- just keyword checks, on purpose. If a match
looks wrong, it's because a specific word wasn't in the title, and that's
easy to reason about and fix.
"""
from __future__ import annotations

import re
from typing import Optional

GRADE_RE = re.compile(r"\b(PSA|BGS|SGC|CSG)\s*(\d{1,2}(?:\.5)?)\b", re.IGNORECASE)
ROOKIE_RE = re.compile(r"\bRC\b|\bROOKIE\b", re.IGNORECASE)


def match_player(title: str, players: list[str]) -> Optional[str]:
    """Return the watchlist player name if every word of their name shows
    up in the title (case-insensitive), else None.

    Checking all name parts (not just a substring of the full name) avoids
    misses caused by extra punctuation/spacing in listing titles.
    """
    lowered = title.lower()
    for player in players:
        parts = player.lower().split()
        if all(re.search(rf"\b{re.escape(part)}\b", lowered) for part in parts):
            return player
    return None


def detect_grading(title: str) -> tuple[str, Optional[str], Optional[str]]:
    """Return (card_type, grader, grade). card_type is 'graded' if a
    PSA/BGS/SGC/CSG + number pattern is found, else 'raw'.
    """
    match = GRADE_RE.search(title)
    if match:
        grader, grade = match.group(1).upper(), match.group(2)
        return "graded", grader, grade
    return "raw", None, None


def detect_rookie_card(title: str) -> bool:
    """Keyword match on "RC" or "Rookie" -- same deliberately-simple
    approach as detect_grading. Not perfect (a listing could be a rookie
    card without saying so, or say "rookie" loosely), but it's the same
    "you can see exactly why" tradeoff as the rest of this project's
    matching logic.
    """
    return bool(ROOKIE_RE.search(title))
