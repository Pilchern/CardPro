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

MANY SALES AT ONCE. One card usually has several recent sales on the page
you are already looking at, and typing them one invocation at a time is the
friction this script exists to remove. Copy the results and paste them in:

    python -m scripts.add_sold_comp --paste \\
        --from-title "2024 Panini Prizm Caleb Williams Silver Prizm #301 PSA 10"

That reads every date-and-price pair it can (src/sold_comp_import.py), files
them all under the one identity, and prints the lot. It writes nothing until
you add --confirm -- the opposite default to the single-sale form above,
because there you typed the two numbers yourself and here a parser worked
them out of a page.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import card_identity, matcher, sold_comp_import, sold_comps  # noqa: E402


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
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Read many sales of ONE card from text you copied off a sold-results page, "
        "given on stdin. Nothing is written without --confirm.",
    )
    parser.add_argument(
        "--paste-file",
        dest="paste_file",
        type=Path,
        help="Same as --paste, reading the copied text from a file instead of stdin.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually write the pasted sales. Without it --paste only shows you what it read.",
    )
    parser.add_argument("--player", help="Player name, spelled as on the watchlist.")
    parser.add_argument(
        "--price",
        type=float,
        help="Item price in USD, EXCLUDING shipping. Required unless --paste/--paste-file "
        "is used, where the prices come out of the pasted text.",
    )
    parser.add_argument(
        "--date",
        help="Date the card SOLD, YYYY-MM-DD. Required unless --paste/--paste-file is used.",
    )
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


def _read_sales(path):
    """``(document, sales)`` from ``path``, or ``None`` after printing why not.

    Deliberately fatal where sold_comps.load() is forgiving: load() skips a
    corrupt file so the daily scan still runs, but a writer that treated
    "unreadable" as "empty" would replace hand-typed data that exists nowhere
    else with whatever it was about to add.
    """
    try:
        document = sold_comps.read_document(path)
    except ValueError as exc:
        print(f"Refusing to write: {exc}", file=sys.stderr)
        print("Fix the file by hand first -- it holds data that exists nowhere else.", file=sys.stderr)
        return None
    sales = document.get("sales")
    if sales is None:
        sales = []
    elif not isinstance(sales, list):
        print(f"Refusing to write: 'sales' in {path} is not a list.", file=sys.stderr)
        return None
    return document, list(sales)


def _pasted_text(args):
    """The copied text, or None after saying why there is none."""
    if args.paste_file:
        try:
            return args.paste_file.read_text()
        except OSError as exc:
            print(f"Could not read {args.paste_file}: {exc}", file=sys.stderr)
            return None
    if sys.stdin.isatty():
        print("Paste the copied sold results, then press Ctrl-D:\n", file=sys.stderr)
    return sys.stdin.read()


def _sale_from_row(args, row) -> dict:
    """One JSON entry for one parsed row: the identity you gave once, the
    price and date the page gave, and nothing invented in between."""
    sale = build_sale(args)
    sale["price"] = row.price
    sale["date"] = row.date
    return sale


def add_pasted(args) -> int:
    """Add every sale in a block of copied text, all for the one card named
    by the identity flags.

    Preview by default, write only with --confirm -- the opposite default to
    the single-sale path in main(), and deliberately so. There you looked at one
    card and typed its two numbers yourself; here a parser guessed how many
    sales are in a page and which figure goes with which date, and a wrong
    sold comp is worse than no sold comp. The extra keystroke buys you a
    look at every row before any of it becomes market value.
    """
    text = _pasted_text(args)
    if text is None:
        return 2
    if not text.strip():
        print("Nothing pasted -- no text on stdin.", file=sys.stderr)
        return 2

    try:
        rows = sold_comp_import.parse_pasted_sales(text)
    except sold_comp_import.ImportRefused as refusal:
        print(f"\nRefused to import.\n\n{refusal}\n", file=sys.stderr)
        return 2

    if not rows:
        print(
            "That text mentions sold items, but no date-and-price pair could be read out of it.\n"
            "Copying the results list (rather than the whole page) usually parses cleanly.",
            file=sys.stderr,
        )
        return 2

    sales = [_sale_from_row(args, row) for row in rows]
    for sale in sales:
        problem = sold_comps.validation_error(sale)
        if problem is not None:
            # All-or-nothing: a partial import leaves you unsure which rows
            # off the page you now hold, which is the state this whole
            # script exists to avoid.
            print(f"Refusing to add any of these sales: one of them {problem}.", file=sys.stderr)
            print(f"  {sold_comps.describe(sale)}", file=sys.stderr)
            return 2

    read = _read_sales(args.path)
    if read is None:
        return 2
    document, existing = read

    # A sale already in the file, and a row the page listed twice, are both
    # "do not add again" -- but they are different mistakes to have made, so
    # the preview names them differently.
    known = {sold_comps.sale_id(sale) for sale in existing if isinstance(sale, dict)}
    seen_here = set()
    fresh, states, duplicates = [], [], 0
    for sale, row in zip(sales, rows):
        identifier = sold_comps.sale_id(sale)
        if identifier in known:
            states.append("already in the file")
            duplicates += 1
        elif identifier in seen_here:
            states.append("repeat of a row above")
            duplicates += 1
        else:
            seen_here.add(identifier)
            states.append("new")
            fresh.append((sale, row))

    print(f"\nRead {len(rows)} sale(s) for {sold_comps.describe(sales[0])}")
    print("-" * 68)
    for row, state in zip(rows, states):
        flag = "  <- YEAR ASSUMED, check this" if row.year_inferred else ""
        print(f"  {row.date}   ${row.price:>10,.2f}   {state}{flag}")
    print("-" * 68)
    if any(row.year_inferred for row in rows):
        print(
            "\n  Some dates carried no year in the source and this year was assumed.\n"
            "  A wrong year makes a stale sale look fresh, and freshness is what\n"
            "  decides whether a comp counts at all -- check those rows."
        )
    if duplicates:
        print(f"\n  {duplicates} of these will not be added again.")

    if not fresh:
        print("\nNothing new to add.")
        return 0

    if not args.confirm:
        print(
            f"\nPreview only -- nothing written. {len(fresh)} sale(s) would be added.\n"
            "Check the rows above against the page, then re-run with --confirm."
        )
        return 0

    document["sales"] = existing + [sale for sale, _ in fresh]
    sold_comps.write_document(args.path, document)
    print(f"\nAdded {len(fresh)} sold comp(s) to {args.path}.")
    print("  basis:  sold  -- outranks every asking price in the corpus")
    print(f"  total:  {len(document['sales'])} sold comp(s)")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    pasting = bool(args.paste or args.paste_file)
    if args.paste and args.paste_file:
        print("Give --paste (stdin) or --paste-file, not both.", file=sys.stderr)
        return 2
    if pasting and (args.price is not None or args.date is not None):
        # Silently ignoring them would be worse: --price with --paste reads
        # like "use this price", and it cannot mean that for a block of
        # sales that each carry their own.
        print(
            "--price and --date describe one sale. With --paste, each sale's price and "
            "date come out of the pasted text -- drop them.",
            file=sys.stderr,
        )
        return 2
    if not pasting:
        missing = [name for name in ("price", "date") if getattr(args, name) is None]
        if missing:
            print(
                f"Missing required argument(s): {', '.join('--' + name for name in missing)}.",
                file=sys.stderr,
            )
            return 2
    if args.confirm and not pasting:
        print("--confirm only applies to --paste; a single sale writes unless --dry-run.", file=sys.stderr)
        return 2

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

    if pasting:
        return add_pasted(args)

    sale = build_sale(args)

    problem = sold_comps.validation_error(sale)
    if problem is not None:
        print(f"Refusing to add this sale: {problem}", file=sys.stderr)
        print(f"  {sold_comps.describe(sale)}", file=sys.stderr)
        return 2

    read_file = _read_sales(args.path)
    if read_file is None:
        return 2
    document, sales = read_file
    sales = sales + [sale]
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
