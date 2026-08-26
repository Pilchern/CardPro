"""Saved-search query generation -- CardPro's answer to a measured coverage
problem, not a speculative feature.

The measurement (docs/CARDPRO_2_AUDIT.md §8, docs/PROJECT_STATUS.md §0), taken
against the 907-listing corpus of 2026-08-21..26:

    graded cards        11 / 907   1.2%   (KPI target: >25%)
    set_name resolved  154 / 907  17.0%

Both numbers are properties of the *searches*, not of the extractor. eBay's
alert digest returns whatever is newest for a query, and what is newest for a
bare player name is overwhelmingly cheap raw filler: one query per player
cannot see a graded market it never asks for. The corpus shows this per
player -- 115 observations for Kyle Teel, none of them graded; 37 for Michael
Jordan, none of them graded, in the deepest graded market in the hobby.

So the fix is several narrow queries per player, each pulling a slice the
broad query structurally misses, in two families that are kept separate on
purpose:

  * grader/grade queries ("... PSA", "... PSA 10", "... SGC") for the graded
    gap. A slab label states the grader and the grade, which is the one place
    identity resolution already works.
  * product queries ("... Prizm", "... Young Guns") for the set_name gap. A
    query that names a product returns titles that name that product.

They are separate because the corpus cannot say one fixes the other: of the 11
graded observations, 0 resolved a set_name and 1 resolved a year. With n=11
that is not evidence that slab titles parse badly -- it is not evidence of
anything, which is precisely why the plan does not assume graded queries will
also close the set_name gap.

This module only GENERATES query strings. It does not create saved searches
(a manual step on eBay's own site, by design -- CardPro does not automate
against a marketplace's UI), and it does not fetch anything.

FINITE ON PURPOSE. Every suggestion is something a human types into eBay and
saves by hand, so the catalogue is not the deliverable -- the *plan* is.
`plan()` ranks every candidate and emits at most ``DEFAULT_SEARCH_BUDGET``
of them, at most ``DEFAULT_MAX_PER_PLAYER`` per player. For the current
20-player watchlist that is 40 searches out of a 287-candidate catalogue,
ordered so the first ten are the ten worth doing first. A generator that
emits everything it can think of is a generator nobody uses.

HOW THE RANKING IS BUILT. Each slice carries a weight (below); each player
carries a hand-set ``market_depth`` hint, 1-3. Score is weight + 4*depth, and
ties break on watchlist order -- your own ordering, not an invented one.
Weights put every player's first grader query above any player's second
search, so the top of the plan is one graded search each for the deepest
markets rather than four searches for one player. Nothing here touches
valuation: this ordering decides what to type into eBay, never what a card is
worth.

WHAT THE TOOL CANNOT KNOW: WHICH SEARCHES YOU ALREADY HAVE. eBay's alert
emails do not say which saved search produced a listing, so `coverage_gaps()`
infers coverage from what arrives and can only ever report *an absence of
evidence*, never "you have no such search". Three ways to do better, with
their costs:

  1. Record the alert email's subject line and attribute each listing to the
     digest it arrived in (`ebay_email_alerts.fetch_alert_messages` already
     holds the `Message`). Cost: a small change there plus per-listing
     provenance through `main.py`. It is real attribution rather than
     inference *if* the subject names the saved search -- unverified against
     this inbox, and a search that matched nothing sends no email, so silence
     still would not prove absence.
  2. Keep the list by hand in config. Cost: nothing to build, and it drifts
     the first time a search is added on a phone.
  3. Read the saved-search list from your My eBay page. Ruled out --
     automating a logged-in marketplace UI is design principle #1.

Deliberately NOT generated:
  * eBay search operator syntax -- OR groups `(psa,bgs)` and `-lot`
    exclusions would collapse several suggestions into one slot, but a
    mistyped or unsupported operator fails by silently returning nothing,
    and confirming the behaviour means automating eBay's search. Every query
    here is plain words that AND together the way an ordinary search does.
  * misspelling permutations -- they multiply query count fast and eBay's own
    search already fuzzy-matches most of them, so the noise cost is real and
    the recall gain is speculative. Revisit only with measurement.
  * every parallel name -- same reason. Parallel-level queries belong on
    acquisition targets (config/watchlist.json "target_cards"), where you've
    said you actually want that specific card.
  * product queries for a player with no sport profile -- a hockey player has
    no Prizm Football, and guessing which products a player appears in is how
    a suggestion becomes an invented card.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

#: How many searches the plan asks you to create. The point of a budget is
#: that it is a sitting's work, not a project; raise it when you have worked
#: through one. (eBay's own cap on saved searches is higher than this, but we
#: do not assert a number for it -- the binding limit here is your time.)
DEFAULT_SEARCH_BUDGET = 40

#: No player may take more than this share of the budget, however deep their
#: market. Four searches on Michael Jordan while Connor Bedard has none is
#: not a watchlist strategy.
DEFAULT_MAX_PER_PLAYER = 4

KIND_BROAD = "broad"
KIND_GRADED = "graded"
KIND_SET = "set"
KIND_ATTRIBUTE = "attribute"
KIND_TARGET = "target"


@dataclass(frozen=True)
class Slice:
    """One query shape, plus why it is worth a saved-search slot.

    ``weight`` is the ordering score before the per-player adjustment. The
    absolute numbers mean nothing; the gaps between them are the argument:
    a grader query for any player outranks a second search for any other.
    """

    suffix: str
    rationale: str
    kind: str
    weight: int
    modern_only: bool = False


# --- Grader and grade slices -- the 1.2%-vs->25% gap ---------------------
#
# Grader-wide queries come first: given one slot per player, "PSA" sees every
# PSA grade, and PSA is the largest grader by volume. The per-grade queries
# below it are refinements worth having only once a bare grader query is
# busy enough to crowd its own digest.
GRADED_SLICES = [
    Slice("PSA", "every PSA-graded copy -- graded is 1.2% of what CardPro sees today", KIND_GRADED, 99),
    Slice("SGC", "SGC slabs -- a separate market, and a lot of the vintage volume", KIND_GRADED, 84),
    Slice("BGS", "BGS slabs -- the third grader, priced differently again", KIND_GRADED, 70),
    Slice("PSA 10", "top-pop copies on their own -- worth a slot once a bare PSA digest is full", KIND_GRADED, 68),
    Slice("PSA 9", "the volume grade -- graded copies nearest the price this report shops at", KIND_GRADED, 66),
    Slice("BGS 9.5", "BGS gem tier -- its own market, and the BGS grade with real depth", KIND_GRADED, 46),
]

# --- Attribute slices -- what makes a copy scarce (src/desirability.py) ---
ATTRIBUTE_SLICES = [
    Slice("rookie", "rookie cards, where most of the money and liquidity is", KIND_ATTRIBUTE, 76,
          modern_only=True),
    Slice("auto", "autographs -- a different market from the base card", KIND_ATTRIBUTE, 72),
    Slice("/99", "serial-numbered parallels; the print run is what makes them scarce", KIND_ATTRIBUTE, 55),
    Slice("refractor prizm", "premium parallels from the two dominant modern lines", KIND_ATTRIBUTE, 50),
]

#: Every slice that applies to any player, best first. Kept as (suffix,
#: rationale) pairs because that is the shape the report renders.
UNIVERSAL_SLICES = sorted(GRADED_SLICES + ATTRIBUTE_SLICES, key=lambda s: -s.weight)
PLAYER_SLICES = [(s.suffix, s.rationale) for s in UNIVERSAL_SLICES]

#: Weight of a player's first product query, and the step down for each
#: further one. The first product query outranks every attribute slice; the
#: fourth does not.
SET_SLICE_TOP_WEIGHT = 88
SET_SLICE_STEP = 10

#: Sport-specific product slices, best-known product first, split by era.
#: Every name here must appear in ``card_identity.SET_KEYWORDS`` -- a product
#: CardPro cannot recognise in a title cannot improve set_name resolution,
#: which is the only reason these queries exist (there is a test for this).
#:
#: Hockey is Upper Deck's product lines only. Panini's NHL products stopped
#: before the one hockey player on this watchlist had a card, so suggesting
#: "Prizm" there would name a card that does not exist.
#:
#: The classic lists are a mix of the players' own playing-era products and
#: the modern lines that still print them -- both are things the corpus has
#: actually seen (Dick Butkus in Spectra and Certified, Gale Sayers in Prizm,
#: Michael Jordan in Hoops and Finest). A player whose cards predate all of
#: it gets ``products=False`` instead of a guess: 1950s-60s cards say
#: "Topps", the brand words are deliberately absent from SET_KEYWORDS (see
#: the comment there), and which modern retro product reprints a given
#: 1950s star is exactly the kind of thing this module refuses to assume.
#: That is a vocabulary limitation, not one a saved search can fix.
SET_SLICES_BY_SPORT = {
    "football": {
        "modern": ["Prizm", "Optic", "Select", "Mosaic", "Contenders"],
        "classic": ["Prizm", "Select", "Certified", "Spectra", "Contenders"],
    },
    "basketball": {
        "modern": ["Prizm", "Optic", "Select", "Mosaic", "Hoops"],
        "classic": ["Hoops", "Finest", "Stadium Club", "Prizm", "Select"],
    },
    "baseball": {
        "modern": ["Topps Chrome", "Bowman Chrome", "Finest", "Stadium Club", "Heritage"],
        "classic": ["Stadium Club", "Finest", "Archives", "Topps Chrome", "Heritage"],
    },
    "hockey": {
        "modern": ["Young Guns", "SP Authentic", "O-Pee-Chee", "Upper Deck Ice", "Synergy"],
        "classic": ["O-Pee-Chee", "SP Authentic", "Upper Deck Ice"],
    },
}


@dataclass(frozen=True)
class PlayerProfile:
    """What the generator needs to know about a player, and nothing more.

    ``sport`` picks the product vocabulary and ``products=False`` turns
    product queries off for a player the vocabulary cannot serve at all.
    ``era`` is "modern" or "classic"
    and only decides whether a rookie-card query is worth a slot -- a classic
    player's rookie is a four-figure card wrapped in reprints, neither of
    which this report can act on. ``market_depth`` (1-3) is a hand-set
    ordering hint for how much graded inventory exists for that player at
    all; it is edited freely, it is never a claim about a card, and it feeds
    nothing but the order of this list.
    """

    sport: Optional[str] = None
    era: str = "modern"
    market_depth: int = 2
    products: bool = True


DEFAULT_PROFILE = PlayerProfile()

#: The current watchlist (config/watchlist.json). A player who is not here
#: still gets every grader, grade and attribute query -- they just get no
#: product queries, because guessing a sport is how you suggest a card that
#: does not exist.
PLAYER_PROFILES = {
    "Michael Jordan": PlayerProfile("basketball", "classic", 3),
    "Walter Payton": PlayerProfile("football", "classic", 2),
    # No product queries: his cards predate every product name the extractor
    # knows, and guessing which modern retro set reprints him is a guess.
    "Ernie Banks": PlayerProfile("baseball", "classic", 2, products=False),
    "Ryne Sandberg": PlayerProfile("baseball", "classic", 2),
    "Dick Butkus": PlayerProfile("football", "classic", 2),
    "Scottie Pippen": PlayerProfile("basketball", "classic", 2),
    "Frank Thomas": PlayerProfile("baseball", "classic", 2),
    "Gale Sayers": PlayerProfile("football", "classic", 2),
    "Caleb Williams": PlayerProfile("football", "modern", 3),
    "Rome Odunze": PlayerProfile("football", "modern", 2),
    "Luther Burden": PlayerProfile("football", "modern", 1),
    "Colston Loveland": PlayerProfile("football", "modern", 1),
    "Josh Giddey": PlayerProfile("basketball", "modern", 2),
    "Matas Buzelis": PlayerProfile("basketball", "modern", 1),
    "Caleb Wilson": PlayerProfile("basketball", "modern", 1),
    "Connor Bedard": PlayerProfile("hockey", "modern", 3),
    "Munetaka Murakami": PlayerProfile("baseball", "modern", 2),
    "Kyle Teel": PlayerProfile("baseball", "modern", 1),
    "Colson Montgomery": PlayerProfile("baseball", "modern", 1),
    "Pete Crow-Armstrong": PlayerProfile("baseball", "modern", 3),
}


@dataclass(frozen=True)
class SuggestedSearch:
    query: str
    rationale: str
    player: str
    priority: int = 0
    kind: str = KIND_BROAD

    def as_line(self) -> str:
        return f'"{self.query}"  --  {self.rationale}'


def profile_for(player: str) -> PlayerProfile:
    """The profile for a player, or the safe default for one we don't know."""
    return PLAYER_PROFILES.get(str(player).strip(), DEFAULT_PROFILE)


def _player_bonus(profile: PlayerProfile) -> int:
    return 4 * max(1, min(3, profile.market_depth))


def _set_slices(sport: Optional[str], era: str = "modern") -> list:
    """Product queries for a sport, or none at all when we don't know the
    sport. Falls back to the modern list when an era has none of its own."""
    if not sport:
        return []
    by_era = SET_SLICES_BY_SPORT.get(str(sport).lower(), {})
    names = by_era.get(era) or by_era.get("modern") or []
    slices = []
    for index, name in enumerate(names):
        slices.append(
            Slice(
                suffix=name,
                rationale=(
                    f"{name} -- naming a real {str(sport).lower()} product is how set_name "
                    "resolves, and it resolves for 17% of listings today"
                ),
                kind=KIND_SET,
                weight=SET_SLICE_TOP_WEIGHT - SET_SLICE_STEP * index,
            )
        )
    return slices


def for_player(player: str, sport: Optional[str] = None, include_sets: bool = True) -> list:
    """The saved searches worth having for one player, best first.

    Returns the broad player query first (that's the one that exists today),
    then the narrow slices that pull the parts of the market the broad query
    structurally misses, ordered by priority.

    ``sport`` overrides the player's profile; leaving it out uses the profile,
    which is how the daily run gets product queries without knowing anything
    about the watchlist.
    """
    profile = profile_for(player)
    sport = sport or profile.sport
    bonus = _player_bonus(profile)

    searches = [
        SuggestedSearch(
            query=player,
            rationale="broad catch-all -- this is the only query type CardPro has today",
            player=player,
            priority=1000,
            kind=KIND_BROAD,
        )
    ]

    candidates = list(UNIVERSAL_SLICES)
    if include_sets and profile.products:
        candidates += _set_slices(sport, profile.era)

    for slice_ in sorted(candidates, key=lambda s: -s.weight):
        if slice_.modern_only and profile.era != "modern":
            continue
        searches.append(
            SuggestedSearch(
                query=f"{player} {slice_.suffix}",
                rationale=slice_.rationale,
                player=player,
                priority=slice_.weight + bonus,
                kind=slice_.kind,
            )
        )
    return searches


def catalogue(players: list, sport_by_player: Optional[dict] = None) -> list:
    """Every candidate for every player -- the pool `plan()` chooses from.

    Exposed so "how many did you consider" is answerable, not so anyone
    creates all of them.
    """
    sport_by_player = sport_by_player or {}
    return [
        search
        for player in players
        for search in for_player(player, sport_by_player.get(player))
        if search.kind != KIND_BROAD
    ]


def _slice_text(player: str, query: str) -> str:
    """The part of a query that isn't the player's name -- "PSA 10" out of
    "Caleb Williams PSA 10"."""
    return str(query).lower().replace(str(player).lower(), "").strip()


def _looks_covered(player: str, query: str, observed) -> bool:
    """Whether anything observed for this player is evidence of this search.

    Matches in both directions: an observation may be a coarse marker ("psa")
    that covers a finer slice ("psa 10"), or a full query string that contains
    the slice. Either way it counts as evidence -- and evidence is all this
    ever is, see the module docstring.
    """
    distinguishing = _slice_text(player, query)
    if not distinguishing:
        return True
    return any(seen in distinguishing or distinguishing in seen for seen in observed if seen)


def plan(
    players: list,
    sport_by_player: Optional[dict] = None,
    budget: int = DEFAULT_SEARCH_BUDGET,
    max_per_player: int = DEFAULT_MAX_PER_PLAYER,
    covered_by_player: Optional[dict] = None,
) -> list:
    """The ranked, budgeted list of searches to go and create, best first.

    Ranking is score-then-watchlist-order (see the module docstring). Anything
    with evidence of existing coverage in ``covered_by_player`` is dropped
    before ranking, so the budget is spent on gaps rather than on searches you
    may already have. ``budget=None`` returns the whole ranked catalogue --
    for inspection, not for a person to go and create.
    """
    sport_by_player = sport_by_player or {}
    covered_by_player = covered_by_player or {}

    scored = []
    for player_order, player in enumerate(players):
        observed = {str(q).lower() for q in covered_by_player.get(player, ())}
        kept = 0
        for slice_order, search in enumerate(for_player(player, sport_by_player.get(player))):
            if search.kind == KIND_BROAD:
                continue  # the broad query is the one we know exists
            if _looks_covered(player, search.query, observed):
                continue
            if kept >= max_per_player:
                break
            kept += 1
            scored.append((-search.priority, player_order, slice_order, search))

    scored.sort(key=lambda item: item[:3])
    if budget is not None and budget >= 0:
        scored = scored[:budget]
    return [item[3] for item in scored]


def for_target(target) -> list:
    """One precise query per acquisition target (see src/targets.py). A target
    is a card you've explicitly said you want, so a narrow query for exactly
    it is worth the extra search slot in a way that speculative parallel
    permutations are not.
    """
    parts = [str(part) for part in (target.year, target.set_name, target.player, target.parallel) if part]
    if target.card_number:
        parts.append(f"#{target.card_number}")
    if target.grader and target.grade:
        parts.append(f"{target.grader} {target.grade}")
    query = " ".join(parts)
    return [
        SuggestedSearch(
            query=query,
            rationale=f"acquisition target: {target.label}",
            player=target.player,
            priority=1000,
            kind=KIND_TARGET,
        )
    ]


def coverage_gaps(
    players: list,
    observed_queries_by_player: dict,
    sport_by_player: Optional[dict] = None,
    budget: int = DEFAULT_SEARCH_BUDGET,
    max_per_player: int = DEFAULT_MAX_PER_PLAYER,
) -> dict:
    """{player: [SuggestedSearch, ...]} for searches that look absent.

    `observed_queries_by_player` is what CardPro has actually seen arrive --
    keyed by player, a set of lowercase query strings (or any strings that
    identify which searches produced results). A slice counts as covered when
    its distinguishing suffix appears in something observed for that player.

    This is a heuristic on purpose. eBay's alert emails don't reliably say
    which saved search produced a listing, so this can only say "there is no
    evidence of coverage here", never "you definitely have no such search".
    The report wording has to match that -- it suggests, it doesn't assert.

    The result is the budgeted plan, grouped by player, players in the order
    their best suggestion ranks. It is the same finite list `plan()` returns:
    the report prints what you should go and do today, not everything that
    could be done.
    """
    searches = plan(
        players,
        sport_by_player=sport_by_player,
        budget=budget,
        max_per_player=max_per_player,
        covered_by_player=observed_queries_by_player,
    )
    gaps = OrderedDict()
    for search in searches:
        gaps.setdefault(search.player, []).append(search)
    return gaps
