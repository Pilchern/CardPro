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

import statistics
from dataclasses import dataclass
from typing import Callable, Optional

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

#: A listing older than this (days up) counts half toward a lookup's value:
#: the cards still listed weeks later are the ones the market already
#: passed on.
RECENT_DAYS = 14


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
    card_number: Optional[str] = None
    #: comps.printing_variant: (autograph, relic, numbering).
    variant: Optional[tuple] = None
    median_ask: Optional[float] = None
    score: float = 0.0

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
    def add_command(self) -> str:
        """The command that records the answer, with everything CardPro
        already knows filled in.

        The remaining two fields are the two only the reader can supply --
        what it sold for and when. Printing a half-typed command is
        deliberate: the identity flags here are the ones the ENGINE will key
        the comp on, so a comp entered this way matches the listings it is
        meant to match. Hand-typing "Silver" where the extractor said "Silver
        Prizm" produces a sold comp that silently never matches anything,
        which is worse than not entering it -- it looks like progress.
        """
        parts = ["python -m scripts.add_sold_comp", '--player "{}"'.format(self.player)]
        if self.year:
            parts.append("--year {}".format(self.year))
        if self.set_name:
            parts.append('--set "{}"'.format(self.set_name))
        if self.parallel:
            parts.append('--parallel "{}"'.format(self.parallel))
        if self.card_number:
            parts.append("--card-number {}".format(self.card_number))
        if self.variant:
            autograph, relic, numbering = self.variant
            if autograph:
                parts.append("--auto")
            if relic:
                parts.append("--relic {}".format(relic))
            if numbering and numbering[0] == "numbered":
                parts.append("--print-run {}".format(numbering[1]))
        if self.market and self.market[0] == "graded":
            parts.append("--grader {} --grade {}".format(self.market[1], self.market[2]))
            if self.market[3]:
                parts.append("--qualifier {}".format(self.market[3]))
        parts.append("--price ? --date ?")
        return " ".join(parts)

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
            self.parallel if self.parallel != comps.BASE_PARALLEL else None,
            "#" + self.card_number if self.card_number else None,
        ]
        if self.market and self.market[0] == "graded":
            parts.append("{} {}".format(self.market[1], self.market[2]))
            if self.market[3]:
                parts.append(self.market[3])
        return " ".join(p for p in parts if p)


def _identity_key(player, year, set_name, parallel, card_number, market, variant) -> Optional[tuple]:
    """The EXACT comp key (comps._key_exact): a sold comp entered from a
    suggestion has to land in the bucket of the listings it was suggested
    for, and same_card without the card number or the variant pooled a
    base card with its inserts and its autos -- a suggestion that could
    never be matched. None when any part is unknown."""
    if None in (year, set_name, parallel, card_number, market, variant) or not set_name:
        return None
    return (player, year, set_name, parallel, card_number, market, variant)


def _listing_identity(listing) -> Optional[tuple]:
    """The exact key for a listing, the way CompEngine.lookup builds it."""
    identity = getattr(listing, "card_identity", None)
    if identity is None:
        return None
    # CardIdentity fields are Field objects carrying value + confidence, not
    # bare values; .value is the extracted answer and None means "unknown".
    year = identity.year.value
    card_number = comps._clean_card_number(identity.card_number.value)
    relic = "patch" if identity.is_patch.value else ("relic" if identity.is_memorabilia.value else None)
    return _identity_key(
        listing.player, year, identity.set_name.value,
        comps._base_parallel(identity.parallel.value, identity.is_base.value, card_number,
                             listing.card_type, year),
        card_number,
        comps.market_key(listing.card_type, listing.grader, listing.grade, listing.qualifier),
        comps.printing_variant(
            is_autograph=identity.is_autograph.value, relic=relic, print_run=identity.print_run.value,
            is_serial_numbered=identity.is_serial_numbered.value,
            title_truncated=bool(getattr(listing, "title_truncated", False)),
        ),
    )


def _observation_identity(obs: dict) -> Optional[tuple]:
    year = obs.get("year")
    card_number = comps._clean_card_number(obs.get("card_number"))
    card_type = obs.get("card_type")
    return _identity_key(
        obs.get("player"), year, obs.get("set_name"),
        comps._base_parallel(obs.get("parallel"), obs.get("is_base"), card_number,
                             card_type.lower() if card_type else None, year),
        card_number,
        comps.market_key(card_type, obs.get("grader"), obs.get("grade"), obs.get("qualifier")),
        comps.variant_of_observation(obs),
    )


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
    max_price: Optional[Callable[[float, bool], float]] = None,
) -> list[CompRequest]:
    """Rank the card identities worth a 130point lookup.

    ``listings`` are this run's evaluated Listings; ``sold_observations`` are
    the sold-basis observations already on file (sold_comps.load()). Only
    identities that appear on at least ``min_listings`` listings are
    suggested, and identities that already have ``min_comps_required`` sold
    comps are dropped -- they are done, and asking for more would make the
    list never shrink, which is how a good suggestion turns into nagging.

    ``max_price(median_ask, graded)`` is the economics MAX: the most you
    could pay and still net the profit floor if the card sold at that
    median. An identity where it is 0 is dropped -- a sold comp for a card
    no buy price can make money on unlocks nothing you would act on.

    Ordering is by value of the lookup: listings waiting, weighted by
    recency (a listing over RECENT_DAYS old counts half), times their median
    ask; then fewest sales still needed; then the identity, so the output is
    stable run to run rather than reshuffling on dict order.
    """
    waiting: dict[tuple, int] = {}
    weight: dict[tuple, float] = {}
    asks: dict[tuple, list] = {}
    example: dict[tuple, str] = {}
    for listing in listings:
        key = _listing_identity(listing)
        if key is None:
            continue
        waiting[key] = waiting.get(key, 0) + 1
        age = getattr(listing, "age_days", None)
        weight[key] = weight.get(key, 0.0) + (0.5 if age is not None and age > RECENT_DAYS else 1.0)
        if getattr(listing, "price", None) is not None:
            asks.setdefault(key, []).append(listing.price)
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
        player, year, set_name, parallel, card_number, market, variant = key
        median_ask = statistics.median(asks[key]) if asks.get(key) else None
        if max_price is not None and median_ask is not None and max_price(median_ask, market[0] == "graded") <= 0:
            continue
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
                card_number=card_number,
                variant=variant,
                median_ask=median_ask,
                score=weight[key] * (median_ask or 0.0),
            )
        )

    requests.sort(
        key=lambda r: (
            -r.score,
            r.still_needed,
            r.player,
            r.year or 0,
            r.set_name or "",
            r.parallel or "",
            r.card_number or "",
            r.market,
        )
    )
    return requests[:limit] if limit is not None else requests
