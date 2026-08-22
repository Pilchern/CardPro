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
matching logic -- add a brand, set, parallel name, mask phrase or negative
signal pattern to the relevant list and it's picked up with no code
changes, same spirit as config/watchlist.json.

WHY THE EXTRA MACHINERY (see docs/CARDPRO_2_AUDIT.md sections 3 and 9):
`parallel` is used as a comparable-sales bucket key, so a *wrong* parallel
is worse than no parallel at all. The audit measured the extractor turning
team and award names into parallels ("Chicago White Sox" -> "White",
"Gold Glove" -> "Gold"), which both violates the never-guess rule and
pools a base card in with a genuine coloured parallel. Three defences,
in order:

  1. MASKING. Known phrases that merely *contain* a colour word (team
     names, awards, place names) are blanked out before any keyword
     matching runs. Masking replaces characters with spaces so word
     boundaries and offsets are preserved.
  2. CONFIDENCE TIERING. After masking, a bare colour word is still only
     weak evidence ("Blue" could be a jersey, a border, a sticker), so it
     extracts at confidence "medium". Compound and unambiguous hobby terms
     ("Orange Ice", "Superfractor", "Green Refractor") extract "high".
  3. VOCABULARY. Compound names are matched longest-first, so the specific
     term always beats the generic colour it contains.

The masking tradeoff is deliberate and costs us some real parallels: a
genuine Topps "Green Wave" refractor of a Tulane player is unrecoverable
from the title alone, so we prefer to report *unknown* than to report a
parallel we can't stand behind. Same tradeoff everywhere in this module.
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
    # Added in the 2.0 hardening pass -- all measured as common in the live
    # corpus and previously extracting as set_name=None.
    "Young Guns", "Rated Rookie", "Bowman Chrome Draft", "Topps Chrome Update",
    "Prizm Draft Picks", "Select Concourse", "Downtown", "Kaboom", "Cosmic",
    "Instant", "Panini Instant", "Topps Now", "Allen & Ginter", "Ginter",
    "Museum Collection", "Tribute", "Diamond Kings", "Score", "Prestige",
    "Elite", "Hoops", "Sapphire", "Merlin", "Stadium Club Chrome",
    "Gypsy Queen", "Big League", "Opening Day",
]

# --- Parallel vocabulary, split by how much a match is worth ------------
#
# COMPOUND_PARALLELS and UNAMBIGUOUS_PARALLELS are hobby terms that only
# ever mean "this is a parallel"; a hit is confidence "high".
# COLOR_PARALLEL_WORDS are bare colours: even after masking they are weak
# evidence, so a hit is confidence "medium" (see module docstring).
COMPOUND_PARALLELS = [
    "Red White Blue", "Atomic Refractor", "Orange Ice", "Blue Ice", "Purple Ice",
    "Green Ice", "Cracked Ice", "Color Blast", "Silver Prizm", "Gold Vinyl",
    "Tie-Dye", "Stained Glass", "Fast Break", "Neon Green", "Hyper Pink",
]

UNAMBIGUOUS_PARALLELS = [
    "Superfractor", "X-Fractor", "Refractor", "Prizmatic", "Snakeskin", "Shimmer",
    "Pulsar", "Genesis", "Choice", "Disco", "Scope", "Hyper", "Mojo", "Wave",
    "Lava", "Kaboom", "Downtown", "Camo", "Tiger", "Zebra", "Sepia", "Ice",
]

COLOR_PARALLEL_WORDS = [
    "Silver", "Gold", "Green", "Blue", "Red", "Orange", "Purple", "Pink", "Black",
    "White", "Bronze", "Teal", "Aqua", "Yellow",
]

# Words that turn a bare colour into a real parallel name when they sit
# right next to it ("Green Refractor", "Gold Wave"). Captured as the
# compound, at confidence "high".
PARALLEL_QUALIFIER_WORDS = [
    "Cracked Ice", "Refractor", "Sparkle", "Shimmer", "Pulsar", "Prizm",
    "Holo", "Foil", "Wave", "Mojo", "Ice",
]

# Kept as the single flat vocabulary for anything that just wants "all the
# parallel words we know"; extending any of the three lists above extends
# this automatically.
PARALLEL_KEYWORDS = COMPOUND_PARALLELS + UNAMBIGUOUS_PARALLELS + COLOR_PARALLEL_WORDS

# Phrases that contain a colour/parallel word but are never a parallel:
# team names, awards, place names, and stock listing phrases. Blanked out
# before keyword matching. Plain data -- add a phrase, no code changes.
TEAM_AND_PHRASE_MASKS = [
    # Teams (colour-bearing)
    "White Sox", "Red Sox", "Red Wings", "Red Raiders", "Blue Jays", "Blue Jackets",
    "Blue Devils", "Green Bay", "Green Wave", "Golden State", "Golden Knights",
    "Golden Bears", "Black Knights", "Blackhawks", "Big Red", "Crimson Tide",
    "Scarlet Knights", "Syracuse Orange", "Reds", "Orioles", "Browns", "Cardinals",
    "Royals", "Blues", "Rangers",
    # Awards / honours
    "Silver Slugger", "Gold Glove", "Golden Spikes", "Green Jacket",
    # Events / places / idioms
    "Orange Bowl", "Rose Bowl", "White House", "Red Zone", "Blue Line",
    # Listing boilerplate that would otherwise read as a parallel
    "Your Choice",
]

AUTOGRAPH_KEYWORDS = ["autograph", "autographed", "auto", "signed", "signature"]
MEMORABILIA_KEYWORDS = ["patch", "relic", "jersey", "memorabilia", "swatch", "game-used", "game used"]
# Patch is a strict subset of memorabilia and is worth materially more than
# a plain jersey swatch, so it gets its own field rather than being pooled.
PATCH_KEYWORDS = ["patch", "patches", "rpa", "logoman", "laundry tag", "letter patch"]

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})(?:-\d{2})?\b")
SEASON_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})\b")
CARD_NUMBER_RE = re.compile(r"#([A-Za-z0-9-]{1,10})\b")
# "23/99" -- a numerator makes it a serial number AND tells us the print run.
# The lookbehind keeps us off "9.5/10" (grade) and the lookahead off date
# chains like "12/25/2024".
SERIAL_NUMBER_RE = re.compile(r"(?<![\d./])(\d{1,4})\s*/\s*(\d{1,5})(?!\s*[/.]?\d)\b")
# Bare "/99" or "/ 99" -- print run only, no serial. Must start at a space
# or bracket so we don't fire on "9.5/10" or "w/99".
BARE_PRINT_RUN_RE = re.compile(r"(?:(?<=\s)|(?<=\()|^)/\s*(\d{1,5})(?!\s*[/.]?\d)\b")
LOT_OF_N_RE = re.compile(r"\blot\s+of\s+\d+\b", re.IGNORECASE)
CARD_LOT_RE = re.compile(r"\b\d+\s*-?\s*card\s+lot\b", re.IGNORECASE)
BARE_LOT_RE = re.compile(r"\blot\b", re.IGNORECASE)

# A print run outside this range is a parse artifact (a date, a price, a
# lot count), not a serial numbering.
MIN_PRINT_RUN = 1
MAX_PRINT_RUN = 100000

# "#N" followed by one of these words is an idiom, not a card number
# ("#1 Draft Pick", "#1 Overall", "#2 of 10").
CARD_NUMBER_STOP_WORDS = ["draft", "overall", "pick", "prospect", "jersey", "of", "fan", "ranked"]

# --- Negative signals ---------------------------------------------------
#
# A negative signal means "this listing may not be the card the rest of the
# identity says it is". Audit failure mode #6: a $20 REPRINT sitting next to
# $6,000 comps is the most expensive-looking false positive this system can
# produce.
#
# WHICH SIDE WE ERRED ON, per signal family:
#   * Unambiguous words (reprint, replica, NFT, facsimile, blaster) are
#     matched bare -- a false positive costs us one skipped listing, a false
#     negative costs real money.
#   * Generic words (pack, case, break, spot, choose) are matched ONLY in a
#     phrase that makes them a product or a service. "Pack fresh" describes a
#     single raw card and is deliberately NOT a sealed-product signal; "wax
#     pack" / "sealed pack" / "pack of 5" are. "Case" alone is usually a
#     one-touch holder around a single card, so only "sealed case" / "case of
#     N" / "case break" count. "Break" alone is a Fast Break parallel as often
#     as it is a group break, so it needs "team/case/box/group/live/personal"
#     in front or "spot/slot" behind.
#   * "damaged" is detected but is NOT a hard block (see
#     is_excluded_from_deals) -- a creased card is still a real card, just
#     worth less, so it belongs in the report as a risk rather than being
#     silently dropped.
NEGATIVE_SIGNAL_PATTERNS = [
    ("reprint", r"\bre-?prints?\b"),
    ("replica", r"\breplicas?\b|\breproductions?\b|\brepro\b"),
    ("custom", r"\bcustoms?\b|\bart\s+card\b|\baceo\b|\bfantasy\s+card\b|\bnovelty\b"),
    ("digital", r"\bdigital\b|\bnfts?\b"),
    (
        "facsimile_auto",
        r"\bfacsimile\b|\bstamped\s+(?:signature|sig|auto\w*)\b"
        r"|\bprinted\s+(?:autograph|auto|signature)\b|\bpre-?print(?:ed)?\s+auto\w*\b",
    ),
    (
        "sealed_product",
        r"\bsealed\b|\bhobby\s+box\b|\bblaster\b|\bmega\s+box\b|\bjumbo\s+box\b"
        r"|\bfactory\s+set\b|\bhanger\b|\bwax\s+pack\b|\bunopened\s+pack\b"
        r"|\bcello\s+pack\b|\brack\s+pack\b|\bpack\s+of\s+\d+\b|\bcase\s+of\s+\d+\b",
    ),
    (
        "break_slot",
        r"\b(?:team|case|box|group|live|personal|pyt)\s+break\b"
        r"|\bbreak\s+(?:spot|slot)\b|\bspot\s+in\b|\bpyt\b|\bpick\s+your\s+team\b"
        r"|\brandom\s+team\b|\brandom\s+player\b",
    ),
    (
        "pick_your_card",
        r"\byou\s+pick\b|\bu\s*-?\s*pick\b|\byour\s+choice\b"
        r"|\bchoose\s+(?:your|any|one|from)\b|\bpick\s+your\s+card\b",
    ),
    # "lot" reuses the existing lot detection rather than a second regex --
    # see extract_card_identity.
    ("lot", None),
    (
        "damaged",
        r"\bdamaged?\b|\bcreased?\b|\bpoor\s+condition\b|\bas[-\s]is\b"
        r"|\baltered\b|\btrimmed\b|\bwater\s+damage\b",
    ),
]

# Canonical vocabulary and emission order -- negative_signals.value is
# always a tuple drawn from this, in this order, so it's stable to compare
# and to key on.
NEGATIVE_SIGNAL_VOCABULARY = tuple(name for name, _ in NEGATIVE_SIGNAL_PATTERNS)

# Short human phrases for the email report. One line per signal, written to
# read as "why this listing was set aside".
NEGATIVE_SIGNAL_LABELS: dict[str, str] = {
    "reprint": "title says REPRINT",
    "replica": "title says replica/reproduction",
    "custom": "custom or art card, not a licensed issue",
    "digital": "digital card / NFT, not a physical card",
    "facsimile_auto": "signature is facsimile/printed, not autographed",
    "sealed_product": "sealed product (box/pack/case), not a single card",
    "break_slot": "group-break slot, not a card you receive",
    "pick_your_card": "pick-your-card listing, price is not for a specific card",
    "lot": "multi-card lot, not a single card",
    "damaged": "condition problem stated in the title",
}

# Hard blocks: the listing is not the single, genuine card it appears to
# be, so no valuation of it can be trusted. "damaged" is deliberately absent
# -- it's a real card at a lower price, i.e. a risk to report, not a reason
# to hide the listing.
HARD_BLOCK_SIGNALS = frozenset(
    {
        "reprint", "replica", "custom", "digital", "facsimile_auto",
        "sealed_product", "break_slot", "pick_your_card", "lot",
    }
)

_MASK_PATTERNS = [
    re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
    for phrase in sorted(TEAM_AND_PHRASE_MASKS, key=len, reverse=True)
]

_COMPOUND_PARALLEL_PATTERNS = [
    (name, re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE))
    for name in sorted(COMPOUND_PARALLELS, key=len, reverse=True)
]

_COLOR_LOOKUP = {word.lower(): word for word in COLOR_PARALLEL_WORDS}
_QUALIFIER_LOOKUP = {word.lower(): word for word in PARALLEL_QUALIFIER_WORDS}
_COLOR_QUALIFIER_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(COLOR_PARALLEL_WORDS, key=len, reverse=True)) + r")"
    r"[\s-]+"
    r"(" + "|".join(re.escape(q) for q in sorted(PARALLEL_QUALIFIER_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_NEGATIVE_SIGNAL_RES = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in NEGATIVE_SIGNAL_PATTERNS
    if pattern is not None
]


@dataclass
class Field:
    """One extracted attribute. value=None means "unknown", never a guess."""

    value: object = None
    confidence: str = "none"  # "high" | "medium" | "low" | "none"
    source: str = "title"


@dataclass
class CardIdentity:
    year: Field = dataclass_field(default_factory=Field)
    season: Field = dataclass_field(default_factory=Field)  # value e.g. "2023-24"
    manufacturer: Field = dataclass_field(default_factory=Field)
    set_name: Field = dataclass_field(default_factory=Field)
    parallel: Field = dataclass_field(default_factory=Field)
    card_number: Field = dataclass_field(default_factory=Field)
    serial_number: Field = dataclass_field(default_factory=Field)  # value e.g. "23/99"
    print_run: Field = dataclass_field(default_factory=Field)  # value e.g. 99 (int)
    is_autograph: Field = dataclass_field(default_factory=lambda: Field(False, "high"))
    is_memorabilia: Field = dataclass_field(default_factory=lambda: Field(False, "high"))
    is_patch: Field = dataclass_field(default_factory=lambda: Field(False, "high"))
    is_lot: Field = dataclass_field(default_factory=lambda: Field(False, "high"))
    # value is a tuple of canonical signal names from NEGATIVE_SIGNAL_VOCABULARY
    negative_signals: Field = dataclass_field(default_factory=lambda: Field((), "none"))


def mask_known_phrases(title: str) -> str:
    """Blank out team/award/place phrases that merely contain a colour or
    parallel word, replacing each with the same number of spaces so word
    boundaries (and character offsets) survive. Longest phrase first, so
    "Red Sox" is consumed before a bare "Reds" mask could nibble at it.
    """
    masked = title
    for pattern in _MASK_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


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


def _extract_parallel(masked_title: str) -> Field:
    """Parallel extraction over an already-masked title, most specific
    evidence first. A bare colour is the last resort and never gets "high"
    confidence, because after masking it is still just as likely to be a
    jersey colour, a border, or a word from the player's team.
    """
    # 1. Named compound parallels ("Orange Ice", "Cracked Ice"), longest first.
    for name, pattern in _COMPOUND_PARALLEL_PATTERNS:
        if pattern.search(masked_title):
            return Field(value=name, confidence="high", source="title")

    # 2. Colour immediately next to a parallel qualifier ("Green Refractor").
    #    Captured as the compound: "Green Refractor" and "Gold Refractor" are
    #    different markets, and neither is the base "Refractor" market.
    adjacent = _COLOR_QUALIFIER_RE.search(masked_title)
    if adjacent:
        color = _COLOR_LOOKUP[adjacent.group(1).lower()]
        qualifier = _QUALIFIER_LOOKUP[adjacent.group(2).lower()]
        return Field(value=f"{color} {qualifier}", confidence="high", source="title")

    # 3. Single hobby terms that only ever mean "parallel".
    unambiguous = _find_keyword(masked_title, UNAMBIGUOUS_PARALLELS)
    if unambiguous:
        return Field(value=unambiguous, confidence="high", source="title")

    # 4. A bare colour: usable as a hint, never as a confident bucket key.
    color = _find_keyword(masked_title, COLOR_PARALLEL_WORDS)
    if color:
        return Field(value=color, confidence="medium", source="title")

    return Field(value=None, confidence="none", source="title")


def _extract_card_number(title: str) -> Field:
    """First "#N" that isn't a well-known non-card-number idiom.

    "#1 Draft Pick", "#1 Overall", "#2 of 10" are all descriptions of the
    player or the listing, not the card's number, and the audit measured
    them landing in card_number and poisoning the comp key. Alphanumeric
    numbers with hyphens ("BDC-25", "US150", "RC-12") stay supported.
    """
    for match in CARD_NUMBER_RE.finditer(title):
        following = title[match.end():].lstrip()
        next_word = re.match(r"[A-Za-z]+", following)
        if next_word and next_word.group(0).lower() in CARD_NUMBER_STOP_WORDS:
            continue
        return Field(value=match.group(1), confidence="high", source="title")
    return Field(value=None, confidence="none", source="title")


def _valid_print_run(value: int) -> bool:
    return MIN_PRINT_RUN <= value <= MAX_PRINT_RUN


def _extract_serial_and_print_run(title: str):
    """Return (serial_number Field, print_run Field).

    Two shapes matter and only one of them was handled before:
      "23/99"  -> serial "23/99", print run 99
      "/99"    -> serial unknown, print run 99   (audit honourable mention)
    A serial can't exceed its print run, which is a cheap way to throw out
    "9.5/10"-style noise that survives the regex guards. Denominators that
    look like a calendar year with a day-sized numerator are dropped as
    dates -- we'd rather miss a /2024 commemorative than invent a print run.
    """
    unknown = Field(value=None, confidence="none", source="title")

    for match in SERIAL_NUMBER_RE.finditer(title):
        numerator, denominator = int(match.group(1)), int(match.group(2))
        if not _valid_print_run(denominator) or numerator > denominator:
            continue
        if 1900 <= denominator <= 2099 and numerator <= 31:
            continue  # "12/2024" is a date, not a serial
        serial = Field(value=f"{numerator}/{denominator}", confidence="high", source="title")
        return serial, Field(value=denominator, confidence="high", source="title")

    for match in BARE_PRINT_RUN_RE.finditer(title):
        denominator = int(match.group(1))
        if not _valid_print_run(denominator):
            continue
        return unknown, Field(value=denominator, confidence="high", source="title")

    return unknown, unknown


def _extract_negative_signals(title: str, is_lot: bool) -> Field:
    """Canonical negative-signal tuple, in NEGATIVE_SIGNAL_VOCABULARY order.

    Empty tuple + confidence "none" means "we found nothing", which is not
    the same claim as "this is definitely a clean listing" -- absence of a
    keyword is not evidence, same rule as everywhere else in this module.
    """
    found = set()
    for name, pattern in _NEGATIVE_SIGNAL_RES:
        if pattern.search(title):
            found.add(name)
    if is_lot:
        found.add("lot")
    if not found:
        return Field(value=(), confidence="none", source="title")
    ordered = tuple(name for name in NEGATIVE_SIGNAL_VOCABULARY if name in found)
    return Field(value=ordered, confidence="high", source="title")


def is_excluded_from_deals(identity: CardIdentity) -> bool:
    """True when a hard-block negative signal is present, i.e. the listing
    is not a single genuine card and no valuation of it can be trusted.

    "damaged" is intentionally not a hard block: a creased card is a real
    card at a lower price. That's a risk for the report to state, not a
    reason to make the listing disappear (audit failure mode #9 -- silent
    drops are how you stop seeing what you're missing).
    """
    signals = identity.negative_signals.value or ()
    return any(signal in HARD_BLOCK_SIGNALS for signal in signals)


def extract_card_identity(title: str) -> CardIdentity:
    """Best-effort structured extraction from a listing title. Every field
    degrades to unknown (year/season/manufacturer/set_name/parallel/
    card_number/serial_number/print_run) or a confident False
    (is_autograph/is_memorabilia/is_patch/is_lot -- sellers reliably mention
    these when true) rather than guessing.
    """
    # Team/award/place masking runs first: everything keyword-matched below
    # is matched against the masked text, so "Chicago White Sox" can't
    # become a White parallel and "Gold Glove" can't become a Gold one.
    masked = mask_known_phrases(title)

    year_match = YEAR_RE.search(title)
    year_field = (
        Field(value=int(year_match.group(1)), confidence="high", source="title")
        if year_match
        else Field(value=None, confidence="none", source="title")
    )

    # "2023-24 Upper Deck" is one hockey/basketball season, and flattening it
    # to 2023 loses the distinction between the 2023-24 and 2022-23 issues.
    # year keeps the leading year (unchanged for existing callers); season
    # carries the full span when the title states one.
    season_match = SEASON_RE.search(title)
    season_field = (
        Field(value=f"{season_match.group(1)}-{season_match.group(2)}", confidence="high", source="title")
        if season_match
        else Field(value=None, confidence="none", source="title")
    )

    serial_field, print_run_field = _extract_serial_and_print_run(title)

    is_lot = bool(LOT_OF_N_RE.search(title) or CARD_LOT_RE.search(title))
    lot_confidence = "high"
    if not is_lot and BARE_LOT_RE.search(title):
        is_lot, lot_confidence = True, "medium"  # bare "lot" with no count -- still very likely a multi-card lot

    is_patch = bool(_find_keyword(masked, PATCH_KEYWORDS))
    # Patch implies memorabilia even when the title only says "RPA".
    is_memorabilia = is_patch or bool(_find_keyword(masked, MEMORABILIA_KEYWORDS))

    return CardIdentity(
        year=year_field,
        season=season_field,
        manufacturer=_keyword_field(masked, MANUFACTURERS),
        set_name=_keyword_field(masked, SET_KEYWORDS),
        parallel=_extract_parallel(masked),
        card_number=_extract_card_number(title),
        serial_number=serial_field,
        print_run=print_run_field,
        is_autograph=Field(bool(_find_keyword(masked, AUTOGRAPH_KEYWORDS)), "high", "title"),
        is_memorabilia=Field(is_memorabilia, "high", "title"),
        is_patch=Field(is_patch, "high", "title"),
        is_lot=Field(is_lot, lot_confidence, "title"),
        negative_signals=_extract_negative_signals(title, is_lot),
    )
