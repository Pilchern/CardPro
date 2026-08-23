"""Adds a hand-looked-up SOLD price to config/sold_comps.json.

Manual entry is the whole risk with sold comps: they are the only real
market data CardPro has, and they only exist if entering one is easy enough
that you actually do it. So this takes flags, validates them, merges into
any existing entry for the same card, and prints the lookup links for you.

    python -m scripts.add_sold_comp \\
        --player "Caleb Williams" --year 2024 --set Prizm --parallel Silver \\
        --number 301 --grader PSA --grade 10 \\
        --price 344 --date 2026-08-15 --source "eBay sold"

Raw card: omit --grader and --grade.
Look prices up first with:  python -m scripts.generate_searches --sold
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sold_comps
from src.config import CONFIG_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--player", required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--set", dest="set_name")
    parser.add_argument("--parallel")
    parser.add_argument("--number", dest="card_number")
    parser.add_argument("--grader", help="PSA/BGS/SGC/CGC -- omit for a raw card")
    parser.add_argument("--grade", help="e.g. 10 or 9.5 -- omit for a raw card")
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, the date it SOLD")
    parser.add_argument("--source", default="manual", help="where you looked it up")
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, got {args.date!r}")
    if args.price <= 0:
        parser.error("--price must be positive")
    if bool(args.grader) != bool(args.grade):
        parser.error("give both --grader and --grade, or neither (raw)")

    path = CONFIG_DIR / "sold_comps.json"
    with open(path) as f:
        data = json.load(f)

    key = sold_comps.identity_key(
        args.player, args.year, args.set_name, args.parallel,
        args.card_number, args.grader, args.grade,
    )
    sale = {"price": args.price, "date": args.date, "source": args.source}

    for entry in data.setdefault("comps", []):
        entry_key = sold_comps.identity_key(
            entry.get("player"), entry.get("year"), entry.get("set_name"),
            entry.get("parallel"), entry.get("card_number"),
            entry.get("grader"), entry.get("grade"),
        )
        if entry_key == key:
            entry.setdefault("sales", []).append(sale)
            entry.pop("_note", None)
            break
    else:
        data["comps"].append(
            {
                "player": args.player, "year": args.year, "set_name": args.set_name,
                "parallel": args.parallel, "card_number": args.card_number,
                "grader": args.grader, "grade": args.grade, "sales": [sale],
            }
        )

    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)

    count = _sales_recorded(data, key)
    grading = f"{args.grader} {args.grade}" if args.grader else "raw"
    print(f"Added ${args.price:,.2f} sold {args.date} -- {args.player} {grading}")
    print(f"This card now has {count} recorded sale(s): {sold_comps.confidence_for(count).upper()} confidence.")
    if count < 3:
        print("Two more sales would raise it to MEDIUM; five to HIGH.")


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
