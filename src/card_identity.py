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
    "O-Pee-Chee", "Pinnacle", "Playoff", "SkyBox",
]

SET_KEYWORDS = [
    "Prizm", "Chrome", "Optic", "Select", "Mosaic", "Contenders", "Immaculate",
    "National Treasures", "Flawless", "Certified", "Absolute", "Spectra", "Phoenix",
    "Chronicles", "Donruss Optic", "Bowman Chrome", "Topps Chrome", "Finest",
    "Stadium Club", "Heritage", "Archives", "Update", "Gallery", "Obsidian",
    "Recon", "Revolution", "Origins", "Pinnacle", "Playbook", "Illusions",
    # Added in the 2.0 hardening pass -- all measured as common in the live
    # corpus and previously extracting as set_name=None.
    # "Rated Rookie" was here and is NOT a set: it is a Donruss rookie
    # designation printed across Donruss, Optic, Score and Elite, so it
    # pooled four products into one bucket -- the failure the comment below
    # about brand words forbids, arriving by a different door.
    "Young Guns", "Bowman Chrome Draft", "Topps Chrome Update",
    "Prizm Draft Picks", "Select Concourse", "Downtown", "Kaboom", "Cosmic",
    "Instant", "Panini Instant", "Topps Now", "Allen & Ginter", "Ginter",
    "Museum Collection", "Tribute", "Diamond Kings", "Score", "Prestige",
    "Elite", "Hoops", "Sapphire", "Merlin", "Stadium Club Chrome",
    "Gypsy Queen", "Big League", "Opening Day",
    # "Collector's Choice" is an Upper Deck set. It was being read as the
    # parallel "Choice" at high confidence, because only the phrase "Your
    # Choice" was masked -- so it is listed here AND masked below.
    "Collector's Choice", "Collectors Choice",
    # 2023-2026 releases that were extracting as set_name=None. Vocabulary
    # only -- adding a product here can only ever turn an unknown set into a
    # known one, never change a set that already matched, because
    # _find_keyword takes the longest phrase present.
    #
    # The brand words (Panini, Topps, Bowman, Donruss, Upper Deck) are
    # deliberately NOT here and must never be added. _find_keyword is
    # longest-first, so "Panini" (6) would beat "Prizm" (5) and every Panini
    # product a player has would pool into one bucket -- the "$1.25 base card
    # is 95% under market" failure, rebuilt. A flagship base set needs its
    # own guarded path, not a vocabulary entry.
    "Zenith", "Luminance", "Court Kings", "Impeccable", "Noir", "Crown Royale",
    "One and One", "Eminence", "Elite Extra Edition", "Leaf Metal",
    "Leaf Metal Draft", "Bowman's Best", "Bowman Draft", "Bowman Sterling",
    "Cosmic Chrome", "Topps Chrome Sapphire", "SP Authentic", "SPx",
    "Upper Deck MVP", "O-Pee-Chee", "Metal Universe", "Topps Fire",
    "Topps Inception", "Topps Definitive", "Topps Dynasty", "Topps Tier One",
    "Topps Series 1", "Topps Series 2", "Topps Series One", "Topps Series Two",
    "Topps Update", "Prizm Monopoly", "Select Draft Picks", "Contenders Optic",
    "National Treasures Collegiate", "Panini Chronicles Draft",
    "Bowman Chrome Prospects", "Topps Heritage Minor League", "Topps Pristine",
    "Topps Sterling", "Topps Triple Threads", "Topps Transcendent",
    "Panini Encased", "Panini Origins", "Panini Rookies and Stars",
    "Panini Playoff", "Panini Absolute Memorabilia", "Panini Vertex",
    "Panini Spectra", "Panini Chronicles Draft Picks", "Upper Deck Trilogy",
    "Upper Deck Series 1", "Upper Deck Series 2", "Upper Deck Artifacts",
    "Upper Deck Ice", "Upper Deck Black Diamond", "Upper Deck Allure",
    "Synergy", "Credentials", "Ultimate Collection", "The Cup",
    # Topps Gold Label is a SET, and it was resolving to flagship "Topps"
    # with parallel "Gold" -- putting a Gold Label base card and a flagship
    # Topps Gold parallel of the same player, year and card number into ONE
    # exact bucket, which is the level allowed to declare a deal.
    "Topps Gold Label", "Gold Label",
]

#: Spellings that mean the same product. Two names for one set means two comp
#: buckets, each half as deep -- and depth is the thing this project has
#: least of. Applied after matching, so the vocabulary above can list every
#: spelling a seller might type while the corpus only ever stores one.
SET_ALIASES = {
    "Collectors Choice": "Collector's Choice",
    "Ginter": "Allen & Ginter",
    "Topps Series One": "Topps Series 1",
    "Topps Series Two": "Topps Series 2",
}

#: A bare "Chrome" is Topps Chrome or Bowman Chrome depending on the brand
#: word in the same title, and they are different products at different
#: prices. The live corpus had this splitting one Kyle Teel card across a
#: 'Chrome' bucket and a 'Topps Chrome' bucket on nothing but which words the
#: seller typed. Only these two manufacturers make a "Chrome" line, so a
#: title naming any other brand leaves the bare name alone rather than
#: inventing a product.
BARE_SET_BY_MANUFACTURER = {
    "Chrome": {"Topps": "Topps Chrome", "Bowman": "Bowman Chrome"},
}

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
    # Multi-word parallels belong HERE, not in UNAMBIGUOUS_PARALLELS. That
    # list is consulted one word at a time by the adjacency run, so a
    # two-word entry in it can never match anything -- "Aqua Vapor
    # Refractor" came out as plain "Refractor" at high confidence, which is
    # the exact pooling the entry was added to prevent.
    "Aqua Vapor", "Rose Gold", "Press Proof", "Dragon Scale", "Logofractor",
    "Rainbow Foil", "Speckle Refractor", "Snakeskin Refractor",
]

UNAMBIGUOUS_PARALLELS = [
    "Superfractor", "X-Fractor", "Refractor", "Prizmatic", "Snakeskin", "Shimmer",
    "Pulsar", "Genesis", "Choice", "Disco", "Scope", "Hyper", "Mojo", "Wave",
    # "Tiger" was here and gave "Tiger Woods" the parallel "Tiger" at high
    # confidence. A Tiger parallel exists, but not often enough to be worth
    # a wrong high-confidence value on every Tiger Woods listing.
    "Lava", "Kaboom", "Downtown", "Camo", "Zebra", "Sepia", "Ice",
    # Modifiers that name a specific refractor/prizm. They were missing, so
    # "Raywave Refractor" came out as plain "Refractor" at high confidence
    # and a scarce parallel got valued against base copies.
    "Raywave", "Speckle", "Sparkle", "Marble", "Mini-Diamond", "Cracked",
    "Padparadscha", "Fuchsia", "Peridot", "Die-Cut", "Holo", "Prismatic",
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
    # Set names containing a parallel word
    "Collector's Choice", "Collectors Choice",
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
# "Card No. 150" / "card number 301" -- the same fact, spelled out.
# Deliberately no "#" alternative here: the hashed form is CARD_NUMBER_RE's
# job, and letting this rule match it too would re-admit the idioms that one
# rejects -- "Michael Jordan card #2 of 10" came back as card number 2.
SPELLED_CARD_NUMBER_RE = re.compile(
    r"\bcard\s*(?:no\.?|number)\s*([A-Za-z0-9-]{1,10})\b", re.IGNORECASE
)
# A hobby-shaped number printed WITHOUT a hash: "BDC-100", "US150", "BCP-83".
# These are printed identifiers, not inferences -- the prefix is part of the
# number on the card. The list is closed on purpose: a bare trailing integer
# ("Caleb Williams 2024 Prizm RC 301") is NOT here, because in an eBay title
# a bare integer is a jersey number, a lot count, a grade or a year fragment
# as often as it is a card number, and guessing is what this module refuses
# to do.
PREFIXED_CARD_NUMBER_RE = re.compile(
    r"\b((?:US|USC|BDC|BCP|BDP|BD|CPA|CDA|RA|TC|SG)-?\d{1,4})\b"
)
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

#: The masks that apply when looking for a SET name -- everything except the
#: phrases that ARE set names.
#:
#: A phrase can need masking for one field and be the answer for another.
#: "Collector's Choice" has to be blanked before parallel extraction, or the
#: "Choice" in it is read as a parallel at high confidence; but blanking it
#: before set extraction is why the set then came out unknown. The mask list
#: exists to stop colour-and-parallel words in team, award and product names
#: being mistaken for parallels, which is a problem set names do not have.
_SET_MASK_PATTERNS = [
    re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
    for phrase in sorted(
        (p for p in TEAM_AND_PHRASE_MASKS if p not in set(SET_KEYWORDS)),
        key=len,
        reverse=True,
    )
]

def _flexible_phrase(name: str) -> re.Pattern:
    """A phrase pattern that tolerates how sellers actually punctuate.

    "Red White Blue" has to match "Red, White & Blue" and "Red White and
    Blue"; "Tie-Dye" has to match "Tie Dye". Matching the literal spelling
    was why a Red White & Blue Prizm came out as parallel "Blue Prizm" at
    high confidence -- the compound missed, and the colour-plus-qualifier
    rule downstream then grabbed the tail of the phrase.
    """
    words = re.findall(r"[A-Za-z0-9]+", name)
    separator = r"[\s,&/-]+(?:and[\s,&/-]+)?"
    return re.compile(r"\b" + separator.join(re.escape(w) for w in words) + r"\b", re.IGNORECASE)


_COMPOUND_PARALLEL_PATTERNS = [
    (name, _flexible_phrase(name))
    for name in sorted(COMPOUND_PARALLELS, key=len, reverse=True)
]

#: The words a multi-word parallel run may be built from: bare colours and
#: terms that only ever mean "parallel". Deliberately NOT
#: PARALLEL_QUALIFIER_WORDS, because several of those double as set names --
#: "Prizm" in "2024 Panini Prizm Silver" is the set, and a run rule that
#: swallowed it would report the parallel as "Prizm Silver".
_RUN_WORD_LOOKUP = {
    word.lower(): word for word in UNAMBIGUOUS_PARALLELS + COLOR_PARALLEL_WORDS
}
_UNAMBIGUOUS_LOWER = {word.lower() for word in UNAMBIGUOUS_PARALLELS}
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]*")


def _parallel_run(masked_title: str):
    """The longest run of adjacent parallel words, as (name, is_specific).

    "Aqua Lava Refractor" is one parallel, not the parallel "Refractor" with
    some words in front of it -- and the difference is a /199 card being
    valued against base refractors. The old rule picked the single longest
    vocabulary word anywhere in the title, so "Refractor" (nine letters) beat
    "Lava" (four) and the modifier was discarded at high confidence. Every
    named refractor collapsed into one bucket, which is exactly the
    same-card-different-parallel pooling the engine exists to prevent.

    Adjacency means separated by whitespace or a hyphen only: an intervening
    comma or any other word ends the run, so a colour at one end of a title
    never joins a parallel word at the other.
    """
    best_words: list = []
    best_specific = False
    current: list = []
    current_specific = False
    previous_end = None

    for match in _WORD_RE.finditer(masked_title):
        word = match.group(0).lower()
        canonical = _RUN_WORD_LOOKUP.get(word)
        gap = masked_title[previous_end:match.start()] if previous_end is not None else None
        adjacent = gap is not None and gap != "" and re.fullmatch(r"[\s-]+", gap) is not None
        if canonical is None:
            current, current_specific = [], False
        else:
            if not adjacent:
                current, current_specific = [], False
            current.append(canonical)
            current_specific = current_specific or word in _UNAMBIGUOUS_LOWER
            # Longer run wins; on a tie, the specific one does. Without the
            # tie-break a bare colour earlier in the title ("Gold Label ...
            # Refractor") outranked a term that only ever means parallel,
            # which is a worse answer at lower confidence.
            if (len(current), current_specific) > (len(best_words), best_specific):
                best_words, best_specific = list(current), current_specific
        previous_end = match.end()

    return (" ".join(best_words), best_specific) if best_words else (None, False)

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
    # True when the title says, as clearly as a title can, that this is the
    # BASE card of its set -- as distinct from parallel=None, which means
    # "we could not read a parallel". See _extract_is_base for why the two
    # have to be separated and why this does not yet key a comp bucket.
    is_base: Field = dataclass_field(default_factory=lambda: Field(None, "none"))


def mask_for_set_lookup(title: str) -> str:
    """Masked title for set extraction -- see _SET_MASK_PATTERNS."""
    masked = title
    for pattern in _SET_MASK_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


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


def _find_keyword_leftmost(title: str, keywords: list[str]) -> Optional[str]:
    """The keyword appearing EARLIEST in the title, longest wins on a tie.

    For manufacturers, longest-first is wrong: "2024 Panini Donruss Optic"
    returned "Donruss" (7 letters) over "Panini" (6), so the brand on a
    Panini product came out as the sub-brand. eBay titles put the
    manufacturer first, so position is the better signal than length here.
    """
    lowered = title.lower()
    best = None
    for keyword in keywords:
        match = re.search(rf"\b{re.escape(keyword.lower())}\b", lowered)
        if match is None:
            continue
        candidate = (match.start(), -len(keyword), keyword)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else None


def _leftmost_keyword_field(title: str, keywords: list[str]) -> Field:
    match = _find_keyword_leftmost(title, keywords)
    if match:
        return Field(value=match, confidence="high", source="title")
    return Field(value=None, confidence="none", source="title")


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

    # 2. Colour immediately next to a parallel qualifier ("Green Prizm",
    #    "Red Wave"). This runs BEFORE the adjacency run, and the order is
    #    the fix for a real regression: a great many players have a colour
    #    for a surname. With the run first, "Coby White Gold Prizm" came out
    #    as "White Gold", "Draymond Green Green Prizm" as "Green Green", and
    #    "Jalen Green Gold Prizm" as "Green Gold" -- phantom parallels that
    #    then keyed comp buckets, because nothing downstream gates a parallel
    #    on its confidence. Anchoring on the qualifier instead picks the
    #    colour that is actually attached to a parallel word, and the
    #    surname in front of it falls away.
    adjacent = _COLOR_QUALIFIER_RE.search(masked_title)
    if adjacent:
        color = _COLOR_LOOKUP[adjacent.group(1).lower()]
        qualifier = _QUALIFIER_LOOKUP[adjacent.group(2).lower()]
        return Field(value=f"{color} {qualifier}", confidence="high", source="title")

    # 3. A run of adjacent parallel words ("Aqua Lava Refractor", "Sepia
    #    Refractor"). Taken whole: these are different markets from each
    #    other and from the bare term, and reporting the bare term pools them.
    run, specific = _parallel_run(masked_title)
    if run and " " in run:
        return Field(value=run, confidence="high" if specific else "medium", source="title")

    # 4. A single hobby term that only ever means "parallel".
    if run and specific:
        return Field(value=run, confidence="high", source="title")

    # 5. A bare colour: usable as a hint, never as a confident bucket key.
    if run:
        return Field(value=run, confidence="medium", source="title")

    return Field(value=None, confidence="none", source="title")


def _canonical_set(set_field: Field, manufacturer: Optional[str]) -> Field:
    """One name per product, so one product means one comp bucket.

    Does two things and nothing else: folds known alternate spellings onto a
    single name, and resolves a bare product line ("Chrome") against the
    brand word in the same title. Both only ever REPLACE a name that was
    already found in the vocabulary -- neither can invent a set for a title
    that has none, which is the guess this module refuses to make.
    """
    name = set_field.value
    if name is None:
        return set_field
    name = SET_ALIASES.get(name, name)
    by_manufacturer = BARE_SET_BY_MANUFACTURER.get(name)
    if by_manufacturer and manufacturer in by_manufacturer:
        name = by_manufacturer[manufacturer]
    if name == set_field.value:
        return set_field
    return Field(value=name, confidence=set_field.confidence, source=set_field.source)


#: Tokens that mean "there is something special about this card that the
#: parallel vocabulary may not have caught". Any of them, and base is not
#: asserted. "1st" is here because Bowman "1st Chrome" and "1st Bowman" are
#: distinct printings; "variation" and "SP" because they are the hobby's own
#: word for "this is not the base card".
NOT_BASE_TOKENS = [
    "sp", "ssp", "variation", "variant", "short print", "shortprint",
    "insert", "die cut", "die-cut", "case hit", "1st", "1of1", "one of one",
    "parallel", "numbered", "exclusive", "exclusives", "premium",
]
_NOT_BASE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in NOT_BASE_TOKENS) + r")\b", re.IGNORECASE
)


# --- The flagship base set ----------------------------------------------
#
# For six brands the set IS the brand: "2024 Topps", "1986 Fleer", "2024
# Panini Donruss". Those titles name a real, single product, and they were
# the largest class of set_name=None in the corpus (66% of listings are
# blocked first by set_name -- see docs/PROJECT_STATUS.md section 0).
#
# WHY THIS IS A GUARDED PATH AND NOT A VOCABULARY ENTRY. _find_keyword is
# longest-first, so putting "Panini" (6 letters) in SET_KEYWORDS beats
# "Prizm" (5) and "2024 Panini Prizm Caleb Williams #301" resolves to set
# "Panini" -- every Panini product a player has pooled into one bucket,
# which is the audit's "$1.25 base card is 95% under market" failure
# rebuilt. Measured, not assumed: appending the brand words to SET_KEYWORDS
# and re-running _find_keyword returns "Panini" for that title today. So the
# flagship is asserted ONLY as a fallback, after the ordinary lookup has
# already come back empty, which makes it structurally impossible for it to
# displace a named product.

#: Brands whose own name is also the name of a set they issue every year.
#:
#: Each of these is a line collectors and sellers refer to by the brand word
#: alone: "2024 Topps" (flagship since 1951), "2024 Bowman", "2024 Panini
#: Donruss", "2024-25 Upper Deck", "1986 Fleer", "1989 Score".
#:
#: Deliberately absent: "Panini", because Panini's name is on every product
#: it makes and names none of them -- "2024 Panini Caleb Williams" states a
#: company, not a card. "Leaf", "Playoff" and "SkyBox" likewise: modern Leaf
#: products are all named (Leaf Metal, Leaf Metal Draft), so a bare "Leaf" is
#: more likely a product name we failed to read than a base set.
#: "Pinnacle" and "O-Pee-Chee" are absent because they are already in
#: SET_KEYWORDS, so the ordinary lookup resolves them and this path never
#: sees them. "Score" is in SET_KEYWORDS too and so is inert here today; it
#: is listed anyway so the flagship path stays correct if that entry ever
#: moves.
FLAGSHIP_MANUFACTURERS = [
    "Topps", "Bowman", "Donruss", "Upper Deck", "Fleer", "Score",
]

#: Card-number prefixes that name a specific line inside a brand's flagship
#: family. These are PRINTED ON THE CARD -- reading "US150" as Topps Update
#: is not an inference about the card, it is transcription, the same reason
#: PREFIXED_CARD_NUMBER_RE trusts them as card numbers.
#:
#: There is no numeric-range rule here and there must never be one. "#1-350
#: is Series 1, #351-700 is Series 2" is true of some years and wrong in
#: others -- the boundary moves with the set size -- so it is a guess wearing
#: a rule's clothes. When the series is not stated and no prefix is printed,
#: the undifferentiated flagship is the honest answer.
#:
#: Uncertain entry, flagged rather than hidden: "USC" may denote Topps Chrome
#: Update rather than paper Update. If it does, this mapping pools a chrome
#: card with paper ones at the same_set level (never at exact -- the card
#: number is part of that key and USC150 != US150). Changing the value below
#: is the whole fix.
FLAGSHIP_NUMBER_PREFIX_SETS = {
    ("Topps", "US"): "Topps Update",
    ("Topps", "USC"): "Topps Update",
    ("Bowman", "BDC"): "Bowman Chrome Draft",
    ("Bowman", "BCP"): "Bowman Chrome Prospects",
    ("Bowman", "BDP"): "Bowman Draft",
}

#: Whether the flagship may be asserted without a card number. See
#: _extract_flagship_set for the argument; short version: the bare brand name
#: is the one set name in this module that does not identify a single
#: product, and the card number is what makes it one.
FLAGSHIP_REQUIRES_CARD_NUMBER = True

#: Ordinary title noise: words that appear in listing titles and never name a
#: product. Used ONLY by the flagship guard's closed-world check, where an
#: unrecognised word is what stops the assertion -- so every word added here
#: makes the guard MORE willing to assert, and the bar for adding one is
#: "this could not possibly be a set name". That is why "Draft", "Pick",
#: "Prospect", "Star" and "Total" are not here despite being common noise:
#: each of them is, or is part of, a real product name.
TITLE_NOISE_WORDS = [
    "rc", "rookie", "rookies", "rated", "card", "cards", "base", "the", "of", "and",
    "psa", "bgs", "sgc", "cgc", "csg", "hga", "beckett", "graded", "ungraded",
    "raw", "slab", "slabbed", "gem", "mint", "nm", "near", "ex", "vg",
    "condition", "cond", "centered", "sharp", "clean", "pack", "fresh",
    "nfl", "nba", "mlb", "nhl", "football", "baseball", "basketball", "hockey",
    "free", "shipping", "ships", "new", "hot", "invest", "hof", "mvp", "roy",
    "jr", "sr", "ii", "iii", "iv",
]


def _vocabulary_words(*phrase_lists) -> set:
    """Every individual word appearing in the given vocabularies, lowercased."""
    words = set()
    for phrases in phrase_lists:
        for phrase in phrases:
            for word in re.findall(r"[A-Za-z0-9'&-]+", phrase):
                words.add(word.lower())
    return words


#: The closed world the flagship guard checks a title against. Words we can
#: account for: brands, parallels, the tokens that mean "not the base card",
#: autograph/memorabilia/patch vocabulary, the team and award phrases we
#: already mask, and ordinary listing noise.
#:
#: SET_KEYWORDS words are deliberately NOT in here. A leftover word from a
#: product name -- "Museum" where the title said "Topps Museum" and the
#: vocabulary wanted "Museum Collection" -- has to STOP the assertion, not be
#: waved through as familiar.
_KNOWN_TITLE_WORDS = _vocabulary_words(
    MANUFACTURERS,
    PARALLEL_KEYWORDS,
    PARALLEL_QUALIFIER_WORDS,
    NOT_BASE_TOKENS,
    AUTOGRAPH_KEYWORDS,
    MEMORABILIA_KEYWORDS,
    PATCH_KEYWORDS,
    TEAM_AND_PHRASE_MASKS,
    TITLE_NOISE_WORDS,
)

_FLAGSHIP_BRAND_RES = [
    (brand, re.compile(rf"\b{re.escape(brand)}\b", re.IGNORECASE))
    for brand in FLAGSHIP_MANUFACTURERS
]
_FLAGSHIP_PREFIX_RE = re.compile(r"^(USC|US|BDC|BCP|BDP)-?\d", re.IGNORECASE)
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}(?:-\d{2})?$")
_BARE_INTEGER_RE = re.compile(r"\d{1,5}$")


def _is_truncated(title: str) -> bool:
    """eBay cut the title off, so the words we would need are unreadable."""
    return title.rstrip().endswith(("\u2026", "..."))


def _is_number_anchor(token: str, first: bool) -> bool:
    """Whether this token is a number rather than a word, i.e. the point at
    which the product name is certainly over.

    A bare integer is an anchor everywhere EXCEPT immediately after the brand
    word, because there a number is as likely to be part of the product's
    name -- "2020 Topps 206" is a real set -- as it is to be a count or a
    grade. Refusing there costs a few titles and protects a whole product.
    """
    if token.startswith("#") or "/" in token:
        return True
    if _YEAR_TOKEN_RE.fullmatch(token) or PREFIXED_CARD_NUMBER_RE.fullmatch(token):
        return True
    if _BARE_INTEGER_RE.fullmatch(token):
        return not first
    return False


def _flagship_window_is_clean(remainder: str) -> bool:
    """Whether the words between the brand word and the first number are
    accounted for -- the whole safety argument for this path.

    "2024 Topps Fire Caleb Williams" is a different product at a different
    price from "2024 Topps Caleb Williams", and calling the first one
    flagship Topps pools two unrelated cards. So every word in the window has
    to be either a word we recognise or part of what looks like the player's
    name, and a player's name is taken to be exactly two unrecognised words
    in a row ("Caleb Williams", "Pete Crow-Armstrong"). One unrecognised word
    is a product name sitting where a name would be; three is a product name
    in front of one.

    Case is ignored on purpose: eBay titles are all-caps often enough that
    capitalisation carries no information.

    WHAT THIS CANNOT CATCH, measured rather than hoped:
      * an unknown TWO-word product standing exactly where a first and last
        name would. "2024 Topps Iron Works #150 Caleb Williams" is asserted
        as flagship Topps, because "Iron Works" and "Caleb Williams" are the
        same shape and nothing in a title tells them apart.
      * a product named AFTER the first number. The window stops at the
        number, so "2024 Topps #150 Caleb Williams Cornerstone Edition" is
        asserted. Extending the window to the end of the title is not the
        fix -- past the number a title is mostly team names and seller
        chatter, and every one of those words would refuse a good listing.
      * a product whose name is an ordinary word we already recognise --
        "2024 Topps Mint" would pass, because "mint" is condition noise.
    Each of these needs the player's name to resolve, and this module is
    given a title and nothing else. What bounds the damage is the shape of
    the comp keys rather than anything here: a wrong flagship on a card with
    no readable parallel can only reach same_set, which is context-only and
    may never flag a deal. A wrong flagship on a card that DOES carry a
    parallel and a grade can reach exact, and there it would pool a card from
    another product with the flagship copy that shares its number. That is
    the one case worth watching if this path is ever widened.
    """
    runs: list[int] = []
    current = 0
    position = 0
    for raw in remainder.split():
        token = raw.strip(".,;:()[]{}\"'!?")
        if not token:
            continue
        if _is_number_anchor(token, first=position == 0):
            break
        position += 1
        if not re.search(r"[A-Za-z0-9]", token):
            continue  # bare punctuation ("&", "-") is not a word
        if token.lower() in _KNOWN_TITLE_WORDS:
            if current:
                runs.append(current)
                current = 0
            continue
        current += 1
    if current:
        runs.append(current)
    if not runs:
        return True
    return len(runs) == 1 and runs[0] == 2


def _extract_flagship_set(
    title: str,
    set_masked_title: str,
    manufacturer: Optional[str],
    card_number: Optional[str],
) -> Field:
    """The flagship base set, or unknown. Only called when SET_KEYWORDS found
    nothing, so this can never displace a named product.

    Every one of these has to hold before a set name is asserted:

      * a manufacturer resolved, and a brand with a flagship line is named in
        the title. Not necessarily the same brand: "2024 Panini Donruss" is
        the most common way that set is titled, and manufacturer resolves to
        Panini there because the leftmost brand word wins.
      * the title is not truncated -- the part eBay cut off is exactly where
        the product name would be.
      * a card number is known (FLAGSHIP_REQUIRES_CARD_NUMBER). The bare
        brand name is the one set name in this module that does not identify
        a single product: within one year "Topps" is Series 1, Series 2 and
        Update, three different cards at three different prices. The card
        number is what separates them, it is printed rather than inferred,
        and requiring it means the flagship never keys a comp bucket without
        the field that tells those three apart. It also costs little that
        matters -- without a card number a listing can reach at most the
        same_set level, which is context-only and cannot flag a deal anyway.
      * the words between the brand and the first number are all accounted
        for (_flagship_window_is_clean).

    Confidence is "medium", never "high". The title states a brand; that the
    brand word is also this card's set is our inference, and principle 7 says
    to prefer being uncertain over being confidently wrong.
    """
    if manufacturer is None:
        return Field(value=None, confidence="none", source="title")
    if _is_truncated(title):
        return Field(value=None, confidence="none", source="title")
    if FLAGSHIP_REQUIRES_CARD_NUMBER and card_number is None:
        return Field(value=None, confidence="none", source="title")

    found = None
    for brand, pattern in _FLAGSHIP_BRAND_RES:
        match = pattern.search(set_masked_title)
        if match and (found is None or match.start() < found[1]):
            found = (brand, match.start(), match.end())
    if found is None:
        return Field(value=None, confidence="none", source="title")

    brand, _, brand_end = found
    if not _flagship_window_is_clean(set_masked_title[brand_end:]):
        return Field(value=None, confidence="none", source="title")

    name = brand
    prefix = _FLAGSHIP_PREFIX_RE.match(card_number or "")
    if prefix:
        name = FLAGSHIP_NUMBER_PREFIX_SETS.get((brand, prefix.group(1).upper()), brand)
    # source records that this name was inferred rather than read, so a
    # future reader can tell the two apart without re-deriving it.
    return Field(value=name, confidence="medium", source="title:flagship")


def _extract_is_base(
    title: str,
    parallel: Field,
    set_name: Field,
    serial_number: Field,
    print_run: Field,
) -> Field:
    """Whether this is the base card of its set -- True, or unknown.

    THE PROBLEM THIS EXISTS TO NAME. ``parallel=None`` currently means two
    different things: "this is a base card, there is no parallel" and "there
    may be a parallel and we could not read it". ``comps._key_exact`` and
    ``_key_same_card`` both refuse a None parallel, and they are right to --
    they cannot tell those apart, and pooling an unread parallel with base
    copies is the same-card-different-parallel error the engine exists to
    prevent. But the consequence is that base cards, which are the bulk of
    any alert feed, are structurally incapable of reaching a level that can
    declare a deal. Measured on the live corpus, resolving this is the single
    largest available gain in comp coverage.

    THIS FIELD DOES NOT YET KEY A COMP BUCKET, deliberately. It is recorded
    so the size of that gain can be measured on real data (see
    scripts/replay_corpus.py) before anything is valued off it. What would
    justify promoting it: replaying a corpus several weeks wide and confirming
    that the buckets it creates contain what they claim to.

    The guard is closed-world and errs toward unknown. Base is asserted only
    when the title is complete, names a set we recognise, carries no serial
    or print run, no parallel word was found, and no token suggests a special
    printing. The residual risk is a parallel named with a word absent from
    the vocabulary entirely -- and the reason that risk is tolerable rather
    than fatal is structural: the parallels that would badly distort a base
    median are almost all serial-numbered, and a serial number is exactly
    what this refuses to look past.

    ONE COMPOUND EFFECT TO KNOW ABOUT. This requires a known set, so every
    listing the flagship path newly resolves becomes eligible to be called
    base. That is intended -- "no parallel, no serial, nothing special" is a
    claim about the card, not about how we learned its set -- but it does
    mean an is_base=True can now rest on an inferred set name. It inherits
    that uncertainty and does not add to it: if the flagship inference is
    wrong, what is wrong is the set, and the card is still the base card of
    whatever set it belongs to.
    """
    if parallel.value is not None:
        return Field(value=False, confidence="high", source="title")
    if not set_name.value:
        return Field(value=None, confidence="none", source="title")
    if _is_truncated(title):
        # eBay truncated it. The parallel may be in the part we cannot see.
        return Field(value=None, confidence="none", source="title")
    if serial_number.value is not None or print_run.value is not None:
        return Field(value=None, confidence="none", source="title")
    if _NOT_BASE_RE.search(title):
        return Field(value=None, confidence="none", source="title")
    return Field(value=True, confidence="medium", source="title")


def _extract_card_number(title: str) -> Field:
    """First "#N" that isn't a well-known non-card-number idiom.

    "#1 Draft Pick", "#1 Overall", "#2 of 10" are all descriptions of the
    player or the listing, not the card's number, and the audit measured
    them landing in card_number and poisoning the comp key. Alphanumeric
    numbers with hyphens ("BDC-25", "US150", "RC-12") stay supported.
    """
    for match in CARD_NUMBER_RE.finditer(title):
        rest = title[match.end():]
        # "#25/99" is a serial number written with a hash, not card #25. It
        # was producing card_number "25", which keys an exact bucket for a
        # card that does not exist -- and two Golds with different serials
        # got two different phantom buckets, neither of which ever met the
        # real one.
        if re.match(r"\s*/\s*\d", rest):
            continue
        following = rest.lstrip()
        next_word = re.match(r"[A-Za-z]+", following)
        if next_word and _is_stop_word(next_word.group(0), following[next_word.end():]):
            continue
        return Field(value=match.group(1), confidence="high", source="title")

    spelled = SPELLED_CARD_NUMBER_RE.search(title)
    if spelled:
        following = title[spelled.end():].lstrip()
        next_word = re.match(r"[A-Za-z]+", following)
        if not (next_word and _is_stop_word(next_word.group(0), following[next_word.end():])):
            return Field(value=spelled.group(1), confidence="high", source="title")

    prefixed = PREFIXED_CARD_NUMBER_RE.search(title)
    if prefixed:
        return Field(value=prefixed.group(1), confidence="high", source="title")

    return Field(value=None, confidence="none", source="title")


def _is_stop_word(word: str, rest: str) -> bool:
    """Whether the word after "#N" makes it an idiom rather than a card number.

    "jersey" is conditional, unlike the rest. "#23 jersey number" is a
    player's shirt number; "#23 Jersey Relic /99" is a real card number
    followed by a real description of the card, and blanket-suppressing it
    threw away the number on exactly the kind of listing worth valuing.
    """
    lowered = word.lower()
    if lowered not in CARD_NUMBER_STOP_WORDS:
        return False
    if lowered == "jersey":
        return re.match(r"\s*(number\b|#)", rest) is not None
    return True


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

    manufacturer_field = _leftmost_keyword_field(masked, MANUFACTURERS)
    card_number_field = _extract_card_number(title)
    set_masked = mask_for_set_lookup(title)
    set_field = _canonical_set(
        _keyword_field(set_masked, SET_KEYWORDS),
        manufacturer_field.value,
    )
    if set_field.value is None:
        # ONLY here, with the ordinary vocabulary lookup already empty, so
        # the flagship can never outrank a named product. See
        # _extract_flagship_set.
        set_field = _extract_flagship_set(
            title, set_masked, manufacturer_field.value, card_number_field.value
        )
    parallel_field = _extract_parallel(masked)
    return CardIdentity(
        year=year_field,
        season=season_field,
        manufacturer=manufacturer_field,
        set_name=set_field,
        parallel=parallel_field,
        card_number=card_number_field,
        serial_number=serial_field,
        print_run=print_run_field,
        is_autograph=Field(bool(_find_keyword(masked, AUTOGRAPH_KEYWORDS)), "high", "title"),
        is_memorabilia=Field(is_memorabilia, "high", "title"),
        is_patch=Field(is_patch, "high", "title"),
        is_lot=Field(is_lot, lot_confidence, "title"),
        negative_signals=_extract_negative_signals(title, is_lot),
        is_base=_extract_is_base(title, parallel_field, set_field, serial_field, print_run_field),
    )
