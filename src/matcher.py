"""Simple, inspectable title matching: which watchlist player(s) is this
listing about, and is it graded or raw?

No fuzzy matching, no ML -- just keyword checks, on purpose. If a match
looks wrong, it's because a specific word wasn't in the title, and that's
easy to reason about and fix.

Two things the 2.0 audit forced open here (docs/CARDPRO_2_AUDIT.md §3):

  * MULTI-PLAYER. `match_player` returns the first watchlist hit, which
    quietly turns a Jordan/Payton dual auto into "a Michael Jordan card".
    A dual or triple auto is its own market and must never be comped
    against single-player cards, so `match_players` returns all hits and
    `match_player` is now just "the first of those".
  * GRADE DETAIL. `(card_type, grader, grade)` throws away the two things
    that most change what a slab is worth: a qualifier ("PSA 8 OC" is not a
    PSA 8) and an authenticity-only slab ("PSA Authentic" is not a grade at
    all). `detect_grade_details` returns those; `detect_grading` is kept
    byte-for-byte compatible as a thin wrapper so no existing caller moves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Plain data, extendable without code changes -- same spirit as the keyword
# lists in card_identity.py.
GRADERS = ["PSA", "BGS", "SGC", "CSG", "CGC", "HGA", "TAG"]
# Trailing qualifiers a grader prints on the label. They are always
# upper-case abbreviations, which is what lets us tell "PSA 9 OC"
# (off-centre) from the English word in "PSA 9 of 12" -- see _qualifier_of.
GRADE_QUALIFIERS = ["OC", "MK", "ST", "MC", "PD", "OF"]

_GRADER_ALT = "|".join(GRADERS)
# Optional "GEM MT"/"GEM MINT"/"MINT" filler is only ever accepted *after* a
# grader token, so a bare "GEM MT 10" in a seller's hype text can't invent a
# grade out of nothing.
GRADE_RE = re.compile(
    rf"\b({_GRADER_ALT})\s*(?:GEM\s*-?\s*(?:MT|MINT)\s*|MINT\s*|MT\s*)?(\d{{1,2}}(?:\.5)?)\b",
    re.IGNORECASE,
)
# "PSA Authentic", "SGC Authentic Altered", or a bare "Authentic Altered"
# (which only ever describes a slab).
AUTHENTIC_RE = re.compile(
    rf"\b(?:({_GRADER_ALT})\s+)?(AUTH|AUTHENTIC)(\s+ALTERED)?\b",
    re.IGNORECASE,
)
ROOKIE_RE = re.compile(r"\bRC\b|\bROOKIE\b", re.IGNORECASE)

# "TAG" is a real grading company and also the second half of "laundry tag"
# / "name tag" (patch-card language). Two-letter-company ambiguity is cheap
# to fix and expensive to ignore -- a laundry tag 1/1 read as "TAG 1" would
# comp a $400 patch against the worst slabs in the market.
_TAG_FALSE_FRIENDS = ["laundry", "name", "jersey", "price", "tape"]
#: How far back to look for one of those words. The old check read exactly
#: one space-delimited token, so "laundry-tag TAG 1/1" walked straight past
#: it -- the hyphen made "laundry-tag" a single token that matches nothing
#: in the list, on the very phrase the list exists to catch.
_TAG_FALSE_FRIEND_WINDOW = 30

#: A grade cannot be immediately followed by "/N": that is a print run.
#: "TAG 1/1" is a one-of-one, not a TAG 1, and reading it as a grade comps
#: a patch card against the worst slabs on the market. No whitespace is
#: allowed before the slash, because "PSA 9 /99" really is a PSA 9 of a card
#: numbered to 99.
_SERIAL_NOT_GRADE_RE = re.compile(r"/\s*\d")


@dataclass
class GradeInfo:
    """Everything the title says about the slab.

    card_type is "graded"/"raw" exactly as before. grade is the numeric
    grade as a string, or "AUTH" for an authenticity-only slab. qualifier is
    the label qualifier ("OC", "MK", ...) when present -- callers should key
    comps on (grader, grade, qualifier), because a qualified card trades in
    a different market from a clean one at the same number.
    """

    card_type: str = "raw"
    grader: Optional[str] = None
    grade: Optional[str] = None
    qualifier: Optional[str] = None
    authentic_only: bool = False


#: What may sit between the parts of a name: spacing, punctuation eBay
#: sellers sprinkle in, and the hyphen of "Michael-Jordan". Deliberately not
#: "/" or any word character -- those separate two PEOPLE.
_NAME_SEPARATOR = r"[\s.,'\u2019\-]+"

_NAME_PATTERNS: dict = {}


def _name_pattern(player: str):
    """A compiled pattern that matches this player's name and not two other
    people's names sitting in the same title.

    The parts have to be ADJACENT. Testing each part independently anywhere
    in the title reads a third person out of two: "Jordan Love RC Michael
    Penix Jr" matched Michael Jordan, and on the real watchlist any title
    containing both a Caleb Wilson and a Jameson Williams matched Caleb
    Williams and Caleb Wilson at once. The first is worse than a miss -- a
    $12 Jordan Love card entered the pipeline as a Michael Jordan card,
    which is exactly the cheap-against-a-legend shape that becomes a DEALS
    headline and an ask in the Michael Jordan comp bucket. The second
    silently discarded a genuine Caleb Wilson card as a multi-player lot.

    "Last, First" is supported separately and REQUIRES the comma. Allowing
    bare reversed adjacency would put the same bug back in a new place:
    "Isiah Thomas Frank Robinson" would match Frank Thomas.
    """
    pattern = _NAME_PATTERNS.get(player)
    if pattern is None:
        parts = [re.escape(part) for part in player.lower().split()]
        forward = _NAME_SEPARATOR.join(parts)
        if len(parts) > 1:
            reversed_form = r"{}\s*,\s*{}".format(parts[-1], _NAME_SEPARATOR.join(parts[:-1]))
            body = "(?:{}|{})".format(forward, reversed_form)
        else:
            body = forward
        pattern = re.compile(r"\b{}\b".format(body))
        _NAME_PATTERNS[player] = pattern
    return pattern


def match_players(title: str, players: List[str]) -> List[str]:
    """Return every watchlist player whose full name appears in the title,
    in watchlist order.

    The name is matched as a whole, tolerating the spacing and punctuation
    sellers put through it -- see _name_pattern for why the parts must be
    adjacent rather than merely both present.

    Multi-player hits are the point: a dual/triple auto is a different
    market from either player's single-player cards, and silently calling it
    a card of whichever player happens to sit first in the watchlist is how
    you comp a $900 dual auto against $30 base cards.
    """
    lowered = title.lower()
    return [
        player for player in players
        if player.split() and _name_pattern(player).search(lowered)
    ]


def match_player(title: str, players: List[str]) -> Optional[str]:
    """First watchlist player matched, or None. Thin wrapper over
    match_players so existing single-player callers are unaffected.
    """
    matched = match_players(title, players)
    return matched[0] if matched else None


def _qualifier_of(title: str, after_index: int) -> Optional[str]:
    """Label qualifier sitting immediately after the numeric grade.

    Deliberately case-SENSITIVE: qualifiers are printed upper-case on the
    label, and requiring that is the cheapest way to keep "PSA 9 of 12"
    (a lot count) from being read as qualifier "OF". A qualifier followed by
    a number is likewise treated as prose, not a qualifier.
    """
    following = title[after_index:]
    match = re.match(r"\s*([A-Z]{2})\b(?!\s*\d)", following)
    if match and match.group(1) in GRADE_QUALIFIERS:
        return match.group(1)
    return None


def detect_grade_details(title: str) -> GradeInfo:
    """Full grading read of a title: grader, numeric grade, label qualifier,
    and authenticity-only slabs.

    Numeric grades are checked first because "PSA 10 Authentic Auto" is a
    graded 10, not an authenticity slab. Anything we can't read stays raw
    with None fields -- unknown is never a guess (card_identity.py's rule
    applies here too).
    """
    for match in GRADE_RE.finditer(title):
        grader, grade = match.group(1).upper(), match.group(2)
        if _SERIAL_NOT_GRADE_RE.match(title, match.end()):
            continue
        if grader == "TAG":
            window = title[max(0, match.start() - _TAG_FALSE_FRIEND_WINDOW):match.start()]
            words = [word for word in re.split(r"\W+", window.lower()) if word]
            if any(word in _TAG_FALSE_FRIENDS for word in words):
                continue
        return GradeInfo(
            card_type="graded",
            grader=grader,
            grade=grade,
            qualifier=_qualifier_of(title, match.end()),
            authentic_only=False,
        )

    authentic = AUTHENTIC_RE.search(title)
    if authentic and (authentic.group(1) or authentic.group(3)):
        # Either a grader said it ("PSA Authentic") or the title says
        # "Authentic Altered", which is a slab label and nothing else. A
        # bare "authentic" on its own is seller boilerplate on almost every
        # raw listing, so it is ignored on purpose.
        grader = authentic.group(1).upper() if authentic.group(1) else None
        return GradeInfo(
            card_type="graded",
            grader=grader,
            grade="AUTH",
            qualifier=None,
            authentic_only=True,
        )

    return GradeInfo(card_type="raw", grader=None, grade=None, qualifier=None, authentic_only=False)


def detect_grading(title: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (card_type, grader, grade). card_type is 'graded' if a
    grader + number pattern is found, else 'raw'.

    Kept as the stable three-tuple API for existing callers; it is now a
    wrapper over detect_grade_details, so callers that need the qualifier or
    the authentic-only flag can move over one at a time.
    """
    details = detect_grade_details(title)
    return details.card_type, details.grader, details.grade


def detect_rookie_card(title: str) -> bool:
    """Keyword match on "RC" or "Rookie" -- same deliberately-simple
    approach as detect_grading. Not perfect (a listing could be a rookie
    card without saying so, or say "rookie" loosely), but it's the same
    "you can see exactly why" tradeoff as the rest of this project's
    matching logic.
    """
    return bool(ROOKIE_RE.search(title))
