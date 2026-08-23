"""Hand-entered SOLD comps -- the only real transaction data this project has.

Every other price CardPro sees is an *asking* price: a number a seller typed
into a listing, biased high (sellers start high and reduce) and biased toward
whatever was freshly dumped on the market that week. The comp engine knows
this and refuses to let an asking-basis comp reach "high" confidence
(comps.assess_comp_match downgrades anything whose basis is not BASIS_SOLD),
which is why the report has to hedge every line as "below the median ask"
rather than "below market". See docs/CARDPRO_2_AUDIT.md 1.2 -- valuation is
the bottleneck everything else compounds off.

There is no free, automatable, ToS-respecting sold-price feed (audit 6):
eBay's Marketplace Insights API is documented as closed to new users,
Terapeak is manual-only behind a Store subscription, Card Ladder has no
public API, and scraping any of them breaches their terms. What is left is
the user looking a card up on 130point.com -- free, and the best sold view in
the hobby because it shows eBay *and* Goldin results including accepted Best
Offers, which eBay's own completed-listings UI hides -- and typing the number
in.

THE TRADEOFF, stated plainly: hand entry does not scale and this corpus will
always be sparse. Twenty sold comps is a realistic ceiling; twenty thousand
asking prices is a Tuesday. It is still worth it, because sparse-and-true
beats dense-and-wrong: one real transaction for a card you are about to buy
outranks the entire asking-price hierarchy built on top of it. The
corresponding danger is symmetric and worth stating too -- a *wrong* entry
here is worse than no entry at all, because the engine will trust it more
than anything else in the corpus. Validation below is therefore loud and
refuses rather than repairs.

What this module does NOT do: no network, no scraping, no clock reads, no
guessing. It reads a hand-edited JSON file and emits observation dicts in
exactly the shape comps.CompEngine consumes (the same shape
price_history.deduped_observations() produces), tagged basis="sold". A
missing file is the normal case and returns [] -- most runs will have none.

Prices are stored EXCLUDING shipping, matching main.record_observations,
which records listing.price (item price) into the asking corpus. Folding
shipping into a sold price would make sold comps systematically higher than
the asking comps they sit beside and quietly manufacture a discount on every
card. `shipping` is carried through as its own field for callers that want
landed cost; it is never added to `price` here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from src import comps

logger = logging.getLogger(__name__)

#: Where the hand-edited file lives by default. Passed explicitly everywhere
#: (config.py owns real path wiring); this is here so scripts/add_sold_comp.py
#: and a human at a REPL don't have to reconstruct it.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "sold_comps.json"

DATE_FORMAT = "%Y-%m-%d"

#: The three fields a sale is worthless without. A price with no date cannot
#: be recency-weighted or aged out; a price with no player cannot be bucketed
#: at all; a sale with no price is not a sale.
REQUIRED_FIELDS = ("player", "price", "date")


def _text(value) -> Optional[str]:
    """Blank/None -> None, else a stripped string. Mirrors comps._clean: a
    blank string must not survive as a known-and-matching identity value."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _price(value) -> Optional[float]:
    """A usable positive, finite price, or None. Strings are accepted because
    this file is typed by hand; "$348" and "348.00" both mean 348."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("$").replace(",", "")
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _year(value) -> Optional[int]:
    """A plain integer year, or None. Matches comps._clean_year: a year that
    isn't a year is unknown, never coerced -- a wrong year is a wrong card.
    price_history stores ints, so this stores ints, so 2024 and "2024" cannot
    end up in two different buckets.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _valid_date(value) -> Optional[str]:
    """The date as a canonical YYYY-MM-DD string, or None if unparseable.
    Deliberately strict: comps._parse_date uses the same format, so anything
    it cannot read would be dropped later anyway -- better to say so here,
    with the offending entry named, than to lose it silently downstream.
    """
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, DATE_FORMAT).strftime(DATE_FORMAT)
    except ValueError:
        return None


def describe(sale: dict) -> str:
    """A short human label for one sale, used in warnings and CLI output so a
    rejected entry can actually be found in the file."""
    if not isinstance(sale, dict):
        return repr(sale)
    parts = [
        str(sale.get("year") or ""),
        _text(sale.get("set_name")) or "",
        _text(sale.get("parallel")) or "",
        ("#" + _text(sale.get("card_number"))) if _text(sale.get("card_number")) else "",
    ]
    card = " ".join(p for p in parts if p)
    grader, grade = _text(sale.get("grader")), _text(sale.get("grade"))
    slab = f"{grader.upper()} {grade}" if grader and grade else "raw"
    player = _text(sale.get("player")) or "?"
    price = sale.get("price")
    date = _text(sale.get("date")) or "?"
    return f"{player} {card} [{slab}] ${price} on {date}".replace("  ", " ")


def validation_error(sale) -> Optional[str]:
    """None if this sale can become a trustworthy observation, else a plain
    sentence saying what is wrong with it.

    Split out from the loader so scripts/add_sold_comp.py can refuse to WRITE
    something the loader would later refuse to READ. A file that silently
    contains entries that never load is the worst outcome here: the user
    believes they have sold comps and the report keeps saying "asking".
    """
    if not isinstance(sale, dict):
        return "entry is not a JSON object"
    for field in REQUIRED_FIELDS:
        if sale.get(field) is None or (isinstance(sale.get(field), str) and not sale[field].strip()):
            return f"missing '{field}'"
    if _price(sale.get("price")) is None:
        return f"price {sale.get('price')!r} is not a positive number"
    if _valid_date(sale.get("date")) is None:
        return f"date {sale.get('date')!r} is not a valid YYYY-MM-DD date"
    if sale.get("shipping") is not None and _price(sale.get("shipping")) is None:
        return f"shipping {sale.get('shipping')!r} is not a positive number (omit it if there was none)"
    if sale.get("year") is not None and _year(sale.get("year")) is None:
        return f"year {sale.get('year')!r} is not a number"
    grader, grade = _text(sale.get("grader")), _text(sale.get("grade"))
    if grader and not grade:
        # comps.market_key() returns None for a slab with no grade, so this
        # entry would load and then be invisible to every card-level comp.
        # Refuse instead of banking a comp that can never be used.
        return "has a 'grader' but no 'grade' (a slab with no grade has no market)"
    if grade and not grader:
        return "has a 'grade' but no 'grader' (PSA 10 and BGS 10 are different markets)"
    return None


def sale_id(sale: dict) -> str:
    """A stable synthetic listing id, derived only from the sale's own fields.

    CompEngine needs an id for two things: excluding a listing from the comp
    that judges it, and (upstream) collapsing repeat sightings of one listing.
    A hand-entered sale has no eBay item id, so one is derived here by hashing
    the normalised identity + price + date + source.

    Deterministic on purpose -- never random, never the entry's index in the
    file. Re-ordering config/sold_comps.json, or re-typing the same sale after
    an edit, must not change what a sale IS; an index-based id would silently
    re-identify every entry below an insertion.
    """
    fields = _normalised(sale)
    payload = "|".join(
        [
            fields["player"] or "",
            str(fields["year"] or ""),
            fields["set_name"] or "",
            fields["parallel"] or "",
            fields["card_number"] or "",
            fields["grader"] or "",
            fields["grade"] or "",
            fields["qualifier"] or "",
            format(fields["price"], ".4f") if fields["price"] is not None else "",
            fields["date"] or "",
            fields["source"] or "",
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"sold:{digest}"


def _normalised(sale: dict) -> dict:
    """The cleaned field values, shared by sale_id() and to_observation() so
    the id is always a hash of exactly what gets emitted."""
    grader = _text(sale.get("grader"))
    return {
        "player": _text(sale.get("player")),
        "year": _year(sale.get("year")),
        "set_name": _text(sale.get("set_name")),
        "parallel": _text(sale.get("parallel")),
        "card_number": _text(sale.get("card_number")),
        "grader": grader.upper() if grader else None,
        "grade": _text(sale.get("grade")),
        "qualifier": (_text(sale.get("qualifier")) or "").upper() or None,
        "price": _price(sale.get("price")),
        "shipping": _price(sale.get("shipping")),
        "date": _valid_date(sale.get("date")),
        "source": _text(sale.get("source")),
        "note": _text(sale.get("note")),
    }


def to_observation(sale: dict) -> Optional[dict]:
    """One sale -> one CompEngine observation dict, or None if invalid.

    The emitted shape is exactly what price_history.deduped_observations()
    produces, so the two corpora can simply be concatenated and handed to
    CompEngine together. The only difference is basis: BASIS_SOLD, which is
    what lets this observation reach "high" confidence.

    card_type is derived, not asked for: a sale with a grader and a grade is
    a slab, anything else is raw. There is no third answer, and asking the
    user to restate it is one more thing to get wrong.
    """
    if validation_error(sale) is not None:
        return None
    f = _normalised(sale)
    return {
        "price": f["price"],
        "date": f["date"],
        "id": sale_id(sale),
        "player": f["player"],
        "card_type": "graded" if (f["grader"] and f["grade"]) else "raw",
        "year": f["year"],
        "set_name": f["set_name"],
        "parallel": f["parallel"],
        "card_number": f["card_number"],
        "grader": f["grader"],
        "grade": f["grade"],
        "qualifier": f["qualifier"],
        "print_run": None,
        "basis": comps.BASIS_SOLD,
        # Carried, never folded into price -- see the module docstring.
        "shipping": f["shipping"],
        "source": f["source"],
        "note": f["note"],
    }


def parse_sales(sales) -> list[dict]:
    """Validate and convert a list of raw sale entries, skipping bad ones with
    a warning that names the offender.

    Rejected entries are logged at WARNING, never dropped quietly: a sold comp
    the user believes they entered but which never loads is a silent downgrade
    of every valuation of that card back to asking-basis, with nothing in the
    report to say so.
    """
    if not isinstance(sales, list):
        logger.warning("sold comps: 'sales' is %s, expected a list -- ignoring it", type(sales).__name__)
        return []
    observations: list[dict] = []
    seen_ids: dict[str, int] = {}
    for sale in sales:
        problem = validation_error(sale)
        if problem is not None:
            logger.warning("sold comp skipped (%s): %s", problem, describe(sale))
            continue
        obs = to_observation(sale)
        if obs is None:  # unreachable: validation_error already passed
            continue
        # Two byte-identical sales are indistinguishable and would share an
        # id. Suffix the repeats so self-exclusion can't drop both at once.
        # Order-independent: identical entries produce the same id multiset
        # however the file is sorted.
        count = seen_ids.get(obs["id"], 0) + 1
        seen_ids[obs["id"]] = count
        if count > 1:
            obs["id"] = f"{obs['id']}-{count}"
        observations.append(obs)
    return observations


def read_document(path: Path) -> dict:
    """The raw JSON document (comments and all), for editors like
    scripts/add_sold_comp.py that must preserve what they didn't write.

    Raises ValueError on a corrupt or non-object file. A writer must NOT
    treat corruption as "start fresh" -- that would overwrite hand-typed sold
    comps with a single new one. load() is the forgiving path; this is not.
    """
    if not path.exists():
        return {"sales": []}
    with open(path) as f:
        try:
            document = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object, found {type(document).__name__}")
    return document


def write_document(path: Path, document: dict) -> None:
    """Atomic write (temp file + replace), same pattern as
    price_history.save -- an interrupted write must not truncate a file of
    hand-typed data that exists nowhere else."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(document, f, indent=2)
        f.write("\n")
    tmp_path.replace(path)


def load(path: Path) -> list[dict]:
    """Every valid hand-entered sold comp, as CompEngine observation dicts.

    Returns [] for a missing file -- that is the NORMAL case, not an error;
    most users will never create one. Returns [] with a warning for a corrupt
    one, matching price_history.load and dedupe.load_seen: an unparseable
    optional config must not take down the daily run.
    """
    if not path.exists():
        return []
    try:
        document = read_document(path)
    except ValueError as exc:
        logger.warning("%s -- no sold comps loaded (file left in place)", exc)
        return []
    return parse_sales(document.get("sales", []))


def summary(observations) -> str:
    """One line for the report's data-quality footer. Pure -- no clock, no IO.

    Says how many sold comps are in play and how recent they are, because
    "we have sold data" is only meaningful alongside "from when".
    """
    usable = [obs for obs in observations if _valid_date(obs.get("date"))]
    if not usable:
        return "Sold comps: none loaded -- every comp is an asking price."
    dates = sorted(_valid_date(obs["date"]) for obs in usable)
    players = len({_text(obs.get("player")) for obs in usable})
    noun = "sale" if len(usable) == 1 else "sales"
    player_noun = "player" if players == 1 else "players"
    span = dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"
    return f"Sold comps: {len(usable)} hand-entered {noun} across {players} {player_noun} ({span})."
