"""Run the whole daily pipeline against the REAL config, without a network.

The test suite covers `main.run()` with a synthetic project directory and
fixture listings. That is the right shape for a test and it does not answer
the question this script exists for: does the pipeline work with YOUR
config/settings.json, YOUR watchlist, and YOUR corpus, and what does the
email actually look like this morning?

Those are different questions, and the second one has found things the first
could not -- a config key that loads but is never read, a section that is
empty because a rule can never fire, four wrapped lines of comp-quality
caveats printed on every card in a section that shows no comp.

What is faked, and only this: the IMAP fetch (replaced by the sample titles
below or a file you pass) and the SMTP send (replaced by a hard failure, so
a rehearsal that somehow tries to email you fails loudly instead). Everything
between them is the real pipeline reading the real config.

Always a dry run. Nothing is emailed and no state file is written.

    python -m scripts.rehearse_run
    python -m scripts.rehearse_run --titles my_titles.txt
    python -m scripts.rehearse_run --out /tmp/today.txt

A titles file is one listing per line: TITLE | PRICE | SHIPPING, where
SHIPPING may be blank for unknown.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: A spread that exercises the interesting paths: a standout auto, a numbered
#: patch rookie, pocket change with something to it, a legend well over the
#: ceiling, a reprint, and a lot. Deliberately not real listings -- this is
#: about the shape of the email, and inventing prices for real cards would be
#: putting invented numbers somewhere they could be mistaken for measured
#: ones.
SAMPLE_TITLES = [
    ("2024 Panini Prizm Caleb Williams Silver Prizm RC #301", 8.99, None),
    ("2024 Topps Chrome Pete Crow-Armstrong Auto Refractor /499 #RA-PCA", 42.00, 4.99),
    ("2023-24 Upper Deck Connor Bedard Young Guns RC #451", 12.50, 0.0),
    ("2024 Panini Prizm Rome Odunze Orange Ice /249 RC #322", 24.99, None),
    ("1986 Fleer Michael Jordan #57 PSA 8", 1450.00, 15.0),
    ("2024 Panini Donruss Caleb Williams #301 RC", 1.99, 1.25),
    ("2024 Topps Luther Burden III #150 RC Patch Relic /99", 28.00, 4.99),
    ("Walter Payton 1984 Topps #34", 6.00, 3.50),
    ("2024 Panini Prizm Josh Giddey #14", 2.25, 1.00),
    ("Frank Thomas 1990 Leaf #300 RC PSA 9", 34.99, 4.99),
    ("2024 Bowman Chrome Colson Montgomery 1st Auto BCP-83 /150", 89.99, 0.0),
    ("2024 Panini Prizm Ryne Sandberg Reprint #22", 3.00, None),
    ("Scottie Pippen 1992 Fleer Ultra #45 lot of 3", 1.50, None),
    ("2024 Panini Select Matas Buzelis Concourse RC #45", 3.50, None),
    ("2024 Topps Chrome Kyle Teel Refractor RA-KT Auto", 55.00, 4.99),
]


def read_titles(path: Path) -> list:
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise SystemExit("{}:{}: expected TITLE | PRICE [| SHIPPING]".format(path, number))
        title, price = parts[0], float(parts[1])
        shipping = float(parts[2]) if len(parts) > 2 and parts[2] else None
        rows.append((title, price, shipping))
    return rows


def _refuse_to_send(*_args, **_kwargs):
    raise AssertionError(
        "A rehearsal tried to send email. It is always a dry run; this is a bug."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.rehearse_run")
    parser.add_argument("--titles", type=Path, help="File of TITLE | PRICE [| SHIPPING] lines.")
    parser.add_argument("--out", type=Path, help="Write the rendered email here as well.")
    args = parser.parse_args(argv)

    rows = read_titles(args.titles) if args.titles else SAMPLE_TITLES

    # load_config requires these; nothing here connects to anything.
    os.environ.setdefault("GMAIL_ADDRESS", "rehearsal@example.com")
    os.environ.setdefault("GMAIL_APP_PASSWORD", "not-a-real-password")
    os.environ.setdefault("EMAIL_TO", "rehearsal@example.com")
    # The eBay API path would need real credentials and a network, so force
    # the alert-email path -- which is the one that actually runs daily.
    os.environ.pop("EBAY_CLIENT_ID", None)
    os.environ.pop("EBAY_CLIENT_SECRET", None)

    from src import main as main_module

    def fake_fetch(*_args, counters=None, **_kwargs):
        if counters is not None:
            counters["messages"] = 1
        return [
            {
                "title": title,
                "url": "https://www.ebay.com/itm/rehearsal-{}".format(index),
                "price": price,
                "shipping_price": shipping,
                "listing_type": "fixed_price",
                "bid_count": None,
                "has_best_offer": False,
                "time_left_text": None,
            }
            for index, (title, price, shipping) in enumerate(rows)
        ]

    main_module.ebay_email_alerts.fetch_alert_listings = fake_fetch
    main_module.emailer.send_email = _refuse_to_send

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        main_module.run(argparse.Namespace(dry_run=True))
    rendered = buffer.getvalue()

    print(rendered)
    if args.out:
        args.out.write_text(rendered)
        print("(also written to {})".format(args.out), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
