"""Seeds sold comps for one card from text you copied off a page.

CardPro has never observed a sale, so every automated number in it compares
one asking price to other asking prices. Sold data isn't hidden -- it's on
eBay's Sold filter, in Terapeak, on PSA's Auction Prices Realized, on
130point -- but no legitimate route lets a program go fetch it. You looking
at a page and copying it is legitimate, and always has been.

    # 1. open the sold search for the card (tick "Sold items"!)
    python -m scripts.generate_searches --sold

    # 2. select the results, copy, then paste into this and press Ctrl-D
    python -m scripts.import_sold_comps \\
        --player "Caleb Williams" --year 2024 --set Prizm --parallel Silver \\
        --number 301 --grader PSA --grade 10

It prints what it found and changes nothing. Re-run with --confirm to write.

    ... --confirm < comps.txt

Raw card: omit --grader and --grade. Check coverage with --status.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sold_comp_import, sold_comps
from src.config import CONFIG_DIR

COMPS_PATH = CONFIG_DIR / "sold_comps.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show which watchlist players have sold comps, then exit")
    parser.add_argument("--player")
    parser.add_argument("--year", type=int)
    parser.add_argument("--set", dest="set_name")
    parser.add_argument("--parallel")
    parser.add_argument("--number", dest="card_number")
    parser.add_argument("--grader", help="PSA/BGS/SGC/CGC -- omit for a raw card")
    parser.add_argument("--grade", help="e.g. 10 or 9.5 -- omit for a raw card")
    parser.add_argument("--confirm", action="store_true", help="actually write (default is preview only)")
    parser.add_argument("--source", default="pasted", help="where you copied this from")
    args = parser.parse_args()

    if args.status:
        _print_status()
        return
    if not args.player:
        parser.error("--player is required (or use --status)")
    if bool(args.grader) != bool(args.grade):
        parser.error("give both --grader and --grade, or neither (raw)")

    if sys.stdin.isatty():
        print("Paste the copied sold results, then press Ctrl-D:\n", file=sys.stderr)
    text = sys.stdin.read()
    if not text.strip():
        print("Nothing pasted.", file=sys.stderr)
        sys.exit(1)

    try:
        sales = sold_comp_import.parse_pasted_sales(text)
    except sold_comp_import.ImportRefused as refusal:
        print(f"\nRefused to import.\n\n{refusal}\n", file=sys.stderr)
        sys.exit(1)

    if not sales:
        print(
            "\nThe text mentions sold items but no date+price pair could be read from it.\n"
            "Copying the results list (rather than the whole page) usually parses cleanly.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    grading = f"{args.grader} {args.grade}" if args.grader else "raw"
    print(f"\n{len(sales)} sale(s) parsed for {args.player} {args.year or ''} "
          f"{args.set_name or ''} {args.parallel or ''} {grading}".replace("  ", " "))
    print("-" * 64)
    for sale in sales:
        flag = "  <- YEAR ASSUMED, check this" if sale.year_inferred else ""
        print(f"  {sale.date}   ${sale.price:>10,.2f}{flag}")
    print("-" * 64)

    prices = sorted(s.price for s in sales)
    median = prices[len(prices) // 2] if len(prices) % 2 else (prices[len(prices) // 2 - 1] + prices[len(prices) // 2]) / 2
    print(f"  median ${median:,.2f}   range ${prices[0]:,.2f} - ${prices[-1]:,.2f}   "
          f"confidence {sold_comps.confidence_for(len(sales)).upper()}")

    if any(s.year_inferred for s in sales):
        print("\n  Some dates had no year in the source and this year was assumed.")
        print("  A wrong year makes a sale look fresher than it is, so check those rows.")

    if not args.confirm:
        print("\nPreview only -- nothing written. Re-run with --confirm to save.")
        return

    _write(args, sales)


def _write(args, sales) -> None:
    with open(COMPS_PATH) as f:
        data = json.load(f)

    key = sold_comps.identity_key(
        args.player, args.year, args.set_name, args.parallel,
        args.card_number, args.grader, args.grade,
    )
    new = [{"price": s.price, "date": s.date, "source": args.source} for s in sales]

    for entry in data.setdefault("comps", []):
        if sold_comps.identity_key(
            entry.get("player"), entry.get("year"), entry.get("set_name"),
            entry.get("parallel"), entry.get("card_number"),
            entry.get("grader"), entry.get("grade"),
        ) == key:
            existing = {(s.get("price"), s.get("date")) for s in entry.get("sales", [])}
            added = [s for s in new if (s["price"], s["date"]) not in existing]
            entry.setdefault("sales", []).extend(added)
            entry.pop("_note", None)
            skipped = len(new) - len(added)
            break
    else:
        data["comps"].append({
            "player": args.player, "year": args.year, "set_name": args.set_name,
            "parallel": args.parallel, "card_number": args.card_number,
            "grader": args.grader, "grade": args.grade, "sales": new,
        })
        added, skipped = new, 0

    tmp = COMPS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(COMPS_PATH)

    total = _sales_recorded(data, key)
    note = f" ({skipped} already recorded, skipped)" if skipped else ""
    print(f"\nWrote {len(added)} sale(s){note}. This card now has {total} recorded sale(s): "
          f"{sold_comps.confidence_for(total).upper()} confidence.")


def _print_status() -> None:
    """Where the sold-comp store is thin, so lookup effort goes somewhere
    useful rather than at whatever card came to mind."""
    with open(CONFIG_DIR / "watchlist.json") as f:
        players = json.load(f)["players"]
    by_player: dict[str, int] = {}
    for obs in sold_comps.load_observations(COMPS_PATH):
        name = obs.get("player") or ""
        by_player[name] = by_player.get(name, 0) + 1

    covered = [p for p in players if by_player.get(p)]
    print(f"\nSold-comp coverage: {len(covered)}/{len(players)} watchlist player(s)\n")
    for player in players:
        count = by_player.get(player, 0)
        mark = "x" if count else " "
        detail = f"{count} sale(s)" if count else "no sold comps -- every figure is ask-based"
        print(f"  [{mark}] {player:<22} {detail}")
    print(
        "\nA player with no sold comps can only ever be measured against asking prices.\n"
        "Seed the cards you'd actually buy:  python -m scripts.import_sold_comps --help"
    )


def _sales_recorded(data: dict, key: tuple) -> int:
    """How many sales this card now has, counted from the file we just
    wrote. Reads the raw entries rather than sold_comps.load_observations,
    which returns CompEngine observations and would count the same thing
    the long way round."""
    for entry in data.get("comps", []):
        if sold_comps.identity_key(
            entry.get("player"), entry.get("year"), entry.get("set_name"),
            entry.get("parallel"), entry.get("card_number"),
            entry.get("grader"), entry.get("grade"),
        ) == key:
            return len(entry.get("sales", []))
    return 0


if __name__ == "__main__":
    main()
