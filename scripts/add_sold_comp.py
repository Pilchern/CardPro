"""Add one hand-entered sold comp to config/sold_comps.json.

Sold comps are the highest-value data in the project (see src/sold_comps.py
for why), and the whole reason there are so few of them is that entering one
is work. Editing JSON by hand at the end of that work -- after looking the
card up on 130point.com -- is exactly the kind of friction that stops the
habit. This is a one-liner instead:

    python -m scripts.add_sold_comp \\
        --from-title "2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10" \\
        --price 348 --date 2026-08-15

Paste the title, type the two things only you know -- what it sold for and
when -- and CardPro reads the rest with the same parser the daily scan uses.
That last part is the point: a comp entered this way is keyed exactly the way
the listings it will be matched against are keyed, and a hand-typed "Silver"
against an extracted "Silver Prizm" is a comp that silently never matches.
Every field is still available as a flag, and an explicit flag always wins.

It validates BEFORE writing and refuses rather than saving something the
loader would later skip: a file that quietly contains entries which never
load is worse than an error message, because the report would keep saying
"asking" while the user believes they entered sold data.

Existing entries and the file's "_comment"/"_example" keys are preserved, and
the write is atomic (temp file + replace) -- this file is hand-typed data
that exists nowhere else, so a half-written one is unacceptable.

--date is required rather than defaulting to today: the date a card SOLD is
almost never the date you got around to typing it in, and the comp engine
weights by that date.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import card_identity, matcher, sold_comps  # noqa: E402


def identity_from_title(title: str) -> dict:
    """Read a sale's identity out of a pasted listing title.

    Eleven flags per entry is why config/sold_comps.json has been empty since
    the day it was created. The identity is already written on the listing,
    and this project already has a parser for it -- the same one the daily
    scan uses, so a comp entered this way is keyed exactly the way the
    listings it will be matched against are keyed. That last part matters
    more than the typing saved: a hand-typed "Silver" against an extracted
    "Silver Prizm" is a comp that silently never matches anything.

    Everything it could not read comes back absent, and the caller prints
    what it did read so a wrong reading is caught before it is written --
    a wrong sold comp is worse than no sold comp (src/sold_comps.py).
    """
    identity = card_identity.extract_card_identity(title)
    grade_info = matcher.detect_grade_details(title)
    read = {
        "year": identity.year.value,
        "set_name": identity.set_name.value,
        "parallel": identity.parallel.value,
        "card_number": identity.card_number.value,
        "grader": grade_info.grader,
        "grade": grade_info.grade,
        "qualifier": grade_info.qualifier,
    }
    return {key: value for key, value in read.items() if value is not None}


def build_sale(args: argparse.Namespace) -> dict:
    """The JSON entry to append. Fields the user didn't supply are omitted
    entirely rather than written as null -- "unknown" and "explicitly nothing"
    look the same to the loader, and a sparser file is easier to read."""
    fields = [
        ("player", args.player),
        ("year", args.year),
        ("set_name", args.set_name),
        ("parallel", args.parallel),
        ("card_number", args.card_number),
        ("grader", args.grader.upper() if args.grader else None),
        ("grade", args.grade),
        ("qualifier", args.qualifier.upper() if args.qualifier else None),
        ("price", args.price),
        ("shipping", args.shipping),
        ("date", args.date),
        ("source", args.source),
        ("note", args.note),
    ]
    return {key: value for key, value in fields if value is not None}


def _watchlist_players() -> list:
    """Watchlist names, so --from-title can resolve the player too. A config
    that cannot be read is not fatal here -- pass --player instead."""
    try:
        import json

        raw = json.loads((Path(__file__).resolve().parent.parent / "config" / "watchlist.json").read_text())
        return list(raw.get("players") or [])
    except Exception:  # noqa: BLE001 -- any failure means "pass --player"
        return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.add_sold_comp",
        description="Append one sold comp to config/sold_comps.json.",
        epilog="Look sales up on 130point.com. Enter the price you actually saw, "
        "for the exact card. A wrong sold comp is worse than none.",
    )
    parser.add_argument(
        "--from-title",
        dest="from_title",
        help="Paste the listing title and let CardPro read the identity out of it "
        "(the same parser the daily scan uses). Anything you also pass explicitly "
        "wins over what it read. The extracted identity is printed before writing.",
    )
    parser.add_argument("--player", help="Player name, spelled as on the watchlist.")
    parser.add_argument("--price", required=True, type=float, help="Item price in USD, EXCLUDING shipping.")
    parser.add_argument("--date", required=True, help="Date the card SOLD, YYYY-MM-DD.")
    parser.add_argument("--year", type=int, help="Card year, e.g. 2024.")
    parser.add_argument("--set", dest="set_name", help='Set name, e.g. "Prizm".')
    parser.add_argument("--parallel", help='Parallel, e.g. "Silver". Omit for a base card you cannot confirm.')
    parser.add_argument("--card-number", dest="card_number", help="Card number, digits only (no #).")
    parser.add_argument("--grader", help="PSA / BGS / SGC / CSG. Omit for raw.")
    parser.add_argument("--grade", help='Numeric grade as shown, e.g. "10" or "9.5". Required with --grader.')
    parser.add_argument("--qualifier", help='Grade qualifier, e.g. "OC" (off-centre). Rare.')
    parser.add_argument("--shipping", type=float, help="Shipping paid, if any. Never folded into the price.")
    parser.add_argument("--source", default="130point", help="Where you saw it (default: 130point).")
    parser.add_argument("--note", help="Anything worth remembering about this sale.")
    parser.add_argument(
        "--path",
        type=Path,
        default=sold_comps.DEFAULT_PATH,
        help=f"Sold comps file to append to (default: {sold_comps.DEFAULT_PATH}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be added; write nothing.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    read = {}
    if args.from_title:
        read = identity_from_title(args.from_title)
        for key, value in read.items():
            # An explicit flag always wins: you looked at the card, the
            # parser only looked at the title.
            if getattr(args, key, None) in (None, ""):
                setattr(args, key, value)
        if args.player is None:
            matched = matcher.match_players(args.from_title, _watchlist_players())
            if matched:
                args.player = matched[0]
        print("Read from the title:")
        for key in ("year", "set_name", "parallel", "card_number", "grader", "grade", "qualifier"):
            print("  {:<12} {}".format(key, read.get(key, "-- not found")))
        print("  {:<12} {}".format("player", args.player or "-- not found"))
        print("Check that against the card before this is trusted over every asking price.")
        print()

    if not args.player:
        print(
            "Refusing to add this sale: no player. Pass --player, or use --from-title "
            "with a title naming someone on your watchlist.",
            file=sys.stderr,
        )
        return 2

    sale = build_sale(args)

    problem = sold_comps.validation_error(sale)
    if problem is not None:
        print(f"Refusing to add this sale: {problem}", file=sys.stderr)
        print(f"  {sold_comps.describe(sale)}", file=sys.stderr)
        return 2

    try:
        document = sold_comps.read_document(args.path)
    except ValueError as exc:
        # Deliberately fatal, unlike sold_comps.load(): overwriting a corrupt
        # file would destroy every sold comp already typed into it.
        print(f"Refusing to write: {exc}", file=sys.stderr)
        print("Fix the file by hand first -- it holds data that exists nowhere else.", file=sys.stderr)
        return 2

    sales = document.get("sales")
    if not isinstance(sales, list):
        if sales is not None:
            print(f"Refusing to write: 'sales' in {args.path} is not a list.", file=sys.stderr)
            return 2
        sales = []
    sales = list(sales) + [sale]
    document["sales"] = sales

    observation = sold_comps.to_observation(sale)
    verb = "Would add" if args.dry_run else "Added"
    print(f"{verb}: {sold_comps.describe(sale)}")
    print(f"  id:     {observation['id']}  (derived from the sale's own fields, stable across re-ordering)")
    print("  basis:  sold  -- outranks every asking price in the corpus")
    print(f"  file:   {args.path}")
    print(f"  total:  {len(sales)} sold comp(s){' (not written -- dry run)' if args.dry_run else ''}")

    if args.dry_run:
        return 0

    sold_comps.write_document(args.path, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
