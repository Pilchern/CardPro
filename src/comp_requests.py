"""Which sold comps to go and get, ranked by what each one would unlock.

The bottleneck in this project is not the valuation engine, it is the data
underneath it: every price CardPro sees is an *asking* price, so no comp can
reach "high" confidence and, on a young corpus, very few can declare a deal
at all (docs/CARDPRO_2_AUDIT.md 1.2). The one fix available without paid
data or scraping is a human looking a card up on 130point and typing the
number in -- see src/sold_comps.py, which explains why that is worth doing
and why twenty entries is a realistic ceiling.

Twenty entries against hundreds of listings is only worth it if they are the
RIGHT twenty. Left to pick by hand you would naturally enter comps for
whatever card caught your eye, which is uncorrelated with what the engine is
stuck on. This module picks instead: it looks at what the run actually saw,
finds the card identities that appear often enough to be worth a lookup, and
ranks them by how many of today's listings each sold comp would move from
"context only" to a level allowed to declare a deal.

The honest limits of that ranking, stated up front:

* It can only rank identities the extractor RESOLVED. A listing whose set or
  parallel is unknown cannot be matched to any sold comp, however many you
  enter, so those listings are counted and reported as a separate number
  rather than silently excluded -- if that number is the large one, the work
  to do is identity extraction, not data entry.
* "Would unlock" means "would give these listings a flag-eligible comp
  level". It is not a promise that any of them turn out to be deals. Most
  will not; a comp that says the card is fairly priced is a successful
  valuation, not a wasted lookup.
* Ranking by listings-per-lookup optimises effort, not portfolio value. A
  single lookup for a card you actually want to buy beats five for cards you
  do not, which is why the suggestion is advice in a footer and never an
  instruction.

No network, no clock, no writes. Pure function of what it is handed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src import comps

#: How many suggestions the report should print. Enough to choose from, few
#: enough to actually do in one sitting -- the whole point is that this is a
#: task a human finishes, not a queue that accuses them.
DEFAULT_LIMIT = 5

#: An identity has to show up at least this often before it is worth asking
#: someone to go and look it up. One listing is not a pattern, and a sold
#: comp entered for a one-off is a lookup spent on a card that may never
#: appear again.
DEFAULT_MIN_LISTINGS = 2


@dataclass(frozen=True)
class CompRequest:
    """One card identity worth looking up, and what it would buy you."""

    player: str
    year: Optional[int]
    set_name: Optional[str]
    parallel: Optional[str]
    market: tuple
    listings_waiting: int
    sold_on_file: int
    still_needed: int
    example_url: Optional[str]

    @property
    def market_label(self) -> str:
        """How to say the market out loud: "raw", or "PSA 10" / "PSA 10 OC"."""
        if not self.market:
            return "unknown"
        if self.market[0] != "graded":
            return self.market[0]
        _, grader, grade, qualifier = self.market
        label = "{} {}".format(grader, grade)
        return "{} {}".format(label, qualifier) if qualifier else label

    @property
    def card_label(self) -> str:
        parts = [str(self.year) if self.year else None, self.set_name, self.parallel]
        return " ".join(p for p in parts if p)

    @property
    def search_query(self) -> str:
        """What to paste into 130point's search box.

        Deliberately the same words a seller would put in a title, in the
        order they would put them, because that is what the sold-listing
        search is matching against. The grade goes in for a slab and stays
        out for a raw card -- searching "raw" returns nothing.
        """
        parts = [
            str(self.year) if self.year else None,
            self.set_name,
            self.player,
            self.parallel,
        ]
        if self.market and self.market[0] == "graded":
            parts.append("{} {}".format(self.market[1], self.market[2]))
            if self.market[3]:
                parts.append(self.market[3])
        return " ".join(p for p in parts if p)


def _identity_key(
    player: str,
    year,
    set_name,
    parallel,
    market: tuple,
) -> tuple:
    return (player, year, set_name, parallel, market)


def _listing_identity(listing) -> Optional[tuple]:
    """The same_card key for a listing, or None if it has no complete one.

    Mirrors comps._key_same_card deliberately rather than sharing code with
    it: that function consumes prepared observation dicts and this consumes
    Listing objects. What must not drift is the RULE -- year, set, parallel
    and a known market are all required, because that is exactly the key a
    sold comp would have to match to be usable.
    """
    identity = getattr(listing, "card_identity", None)
    if identity is None:
        return None
    # CardIdentity fields are Field objects carrying value + confidence, not
    # bare values; .value is the extracted answer and None means "unknown".
    year = identity.year.value
    set_name = identity.set_name.value
    parallel = identity.parallel.value
    if year is None or not set_name or not parallel:
        return None
    market = comps.market_key(
        listing.card_type, listing.grader, listing.grade, listing.qualifier
    )
    if market is None:
        return None
    return _identity_key(listing.player, year, set_name, parallel, market)


def _observation_identity(obs: dict) -> Optional[tuple]:
    year = obs.get("year")
    set_name = obs.get("set_name")
    parallel = obs.get("parallel")
    if year is None or not set_name or not parallel:
        return None
    market = comps.market_key(
        obs.get("card_type"), obs.get("grader"), obs.get("grade"), obs.get("qualifier")
    )
    if market is None:
        return None
    return _identity_key(obs.get("player"), year, set_name, parallel, market)


def unidentified_count(listings) -> int:
    """Listings no sold comp could ever help, because we don't know what they are.

    Reported alongside the suggestions so the footer can say which problem is
    the bigger one. If this number dwarfs the suggestions, entering sold
    comps is the wrong next move and fixing src/card_identity.py is the right
    one -- the report should be able to say that rather than making it look
    like data entry is always the answer.
    """
    return sum(1 for listing in listings if _listing_identity(listing) is None)


def build_requests(
    listings,
    sold_observations=(),
    *,
    min_comps_required: int = 3,
    min_listings: int = DEFAULT_MIN_LISTINGS,
    limit: int = DEFAULT_LIMIT,
) -> list[CompRequest]:
    """Rank the card identities worth a 130point lookup.

    ``listings`` are this run's evaluated Listings; ``sold_observations`` are
    the sold-basis observations already on file (sold_comps.load()). Only
    identities that appear on at least ``min_listings`` listings are
    suggested, and identities that already have ``min_comps_required`` sold
    comps are dropped -- they are done, and asking for more would make the
    list never shrink, which is how a good suggestion turns into nagging.

    Ordering is: most listings unlocked first, then fewest sales still
    needed (finish the cheap ones), then the identity itself so the output
    is stable run to run rather than reshuffling on dict order.
    """
    waiting: dict[tuple, int] = {}
    example: dict[tuple, str] = {}
    for listing in listings:
        key = _listing_identity(listing)
        if key is None:
            continue
        waiting[key] = waiting.get(key, 0) + 1
        if key not in example and getattr(listing, "url", None):
            example[key] = listing.url

    on_file: dict[tuple, int] = {}
    for obs in sold_observations:
        key = _observation_identity(obs)
        if key is None:
            continue
        on_file[key] = on_file.get(key, 0) + 1

    requests = []
    for key, count in waiting.items():
        if count < min_listings:
            continue
        have = on_file.get(key, 0)
        still_needed = max(0, min_comps_required - have)
        if still_needed == 0:
            continue
        player, year, set_name, parallel, market = key
        requests.append(
            CompRequest(
                player=player,
                year=year,
                set_name=set_name,
                parallel=parallel,
                market=market,
                listings_waiting=count,
                sold_on_file=have,
                still_needed=still_needed,
                example_url=example.get(key),
            )
        )

    requests.sort(
        key=lambda r: (
            -r.listings_waiting,
            r.still_needed,
            r.player,
            r.year or 0,
            r.set_name or "",
            r.parallel or "",
            r.market,
        )
    )
    return requests[:limit] if limit is not None else requests
