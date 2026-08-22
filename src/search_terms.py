"""Saved-search query generation -- CardPro's answer to a measured coverage
problem, not a speculative feature.

The measurement (see docs/CARDPRO_2_AUDIT.md §3, failure mode #10): with one
saved search per player, **99.3% of everything CardPro has ever observed was
a raw card and 67% of it was under $25**, median observation $10.64. eBay's
alert digest returns whatever is newest for a query, and what is newest for a
bare player name is overwhelmingly cheap raw filler. The graded market -- the
liquid, high-value half of the hobby, and the only half where comps are dense
enough to value anything precisely -- is effectively invisible.

One query per player cannot fix that, because the problem isn't the number of
listings, it's *which* listings. The fix is several narrow queries per player
that each pull a different slice of the market, so the graded, autographed,
numbered and premium-set inventory actually reaches the pipeline.

This module only GENERATES the query strings. It does not create saved
searches (that's a manual step on eBay's own site, by design -- CardPro does
not automate against a marketplace's UI), and it does not fetch anything. The
daily report prints the queries you don't appear to have coverage for, so
setting them up stays a two-minute copy/paste rather than a research project.

Deliberately NOT generated:
  * misspelling permutations -- they multiply query count fast and eBay's own
    search already fuzzy-matches most of them, so the noise cost is real and
    the recall gain is speculative. Revisit only with measurement.
  * every parallel name -- same reason. Parallel-level queries belong on
    acquisition targets (config/watchlist.json "target_cards"), where you've
    said you actually want that specific card.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Each slice is (suffix, why_it_exists). The "why" is printed in the report so
# a suggested search is never an unexplained instruction.
PLAYER_SLICES = [
    ("PSA 10", "graded top-pop market -- densest comps, and 99% missing from today's data"),
    ("PSA", "all graded copies, any grade -- separate market from raw"),
    ("BGS SGC", "the other graders; their markets price differently from PSA"),
    ("rookie", "rookie cards, where most of the money and liquidity is"),
    ("auto", "autographs -- a different market from the base card"),
    ("/99", "serial-numbered parallels; the print run is what makes them scarce"),
    ("refractor prizm", "premium parallels from the two dominant modern lines"),
]

# Sport-specific set slices. A hockey player has no Prizm Football, so
# suggesting one would just be noise.
SET_SLICES_BY_SPORT = {
    "football": ["Prizm", "Optic", "Select", "Contenders", "Mosaic"],
    "basketball": ["Prizm", "Optic", "Select", "Mosaic", "Hoops"],
    "baseball": ["Topps Chrome", "Bowman Chrome", "Topps", "Heritage", "Stadium Club"],
    "hockey": ["Young Guns", "Upper Deck", "SP Authentic", "Prizm"],
    "vintage": ["Topps", "Fleer", "Bowman"],
}


@dataclass(frozen=True)
class SuggestedSearch:
    query: str
    rationale: str
    player: str

    def as_line(self) -> str:
        return f'"{self.query}"  --  {self.rationale}'


def for_player(player: str, sport: Optional[str] = None, include_sets: bool = True) -> list:
    """The set of saved searches worth having for one player.

    Returns the broad player query first (that's the one that exists today),
    then the narrow slices that pull the parts of the market the broad query
    structurally misses.
    """
    searches = [
        SuggestedSearch(
            query=player,
            rationale="broad catch-all -- this is the only query type CardPro has today",
            player=player,
        )
    ]
    for suffix, rationale in PLAYER_SLICES:
        searches.append(SuggestedSearch(query=f"{player} {suffix}", rationale=rationale, player=player))

    if include_sets and sport:
        for set_name in SET_SLICES_BY_SPORT.get(sport.lower(), []):
            searches.append(
                SuggestedSearch(
                    query=f"{player} {set_name}",
                    rationale=f"{set_name} is a primary {sport} product for this era",
                    player=player,
                )
            )
    return searches


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
        )
    ]


def coverage_gaps(players: list, observed_queries_by_player: dict, sport_by_player: Optional[dict] = None) -> dict:
    """{player: [SuggestedSearch, ...]} for searches that look absent.

    `observed_queries_by_player` is what CardPro has actually seen arrive --
    keyed by player, a set of lowercase query strings (or any strings that
    identify which searches produced results). A slice counts as covered when
    its distinguishing suffix appears in something observed for that player.

    This is a heuristic on purpose. eBay's alert emails don't reliably say
    which saved search produced a listing, so this can only say "there is no
    evidence of coverage here", never "you definitely have no such search".
    The report wording has to match that -- it suggests, it doesn't assert.
    """
    sport_by_player = sport_by_player or {}
    gaps = {}
    for player in players:
        observed = {str(q).lower() for q in observed_queries_by_player.get(player, ())}
        missing = []
        for search in for_player(player, sport_by_player.get(player)):
            if search.query.lower() == player.lower():
                continue  # the broad query is the one we know exists
            distinguishing = search.query.lower().replace(player.lower(), "").strip()
            if not any(distinguishing in seen for seen in observed):
                missing.append(search)
        if missing:
            gaps[player] = missing
    return gaps
