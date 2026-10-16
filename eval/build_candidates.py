"""Draw the stratified eval sample from the stored corpus (seeded, reproducible).

    python -m eval.build_candidates  -> eval/candidates.json
"""
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "ebay_alert_price_history.json"
OUT = Path(__file__).resolve().parent / "candidates.json"

NOISE_RE = re.compile(r"\b(lot|lots|reprint|rp|custom|you pick|u pick|pick your|mystery|break|"
                      r"not psa|like|style|facsimile|novelty|fan made|art card|aceo|digital)\b", re.I)
SERIAL_RE = re.compile(r"/\s*\d{1,4}\b")

STRATA = [
    ("graded", 20, lambda r: r["_market"] == "graded"),
    ("numbered", 20, lambda r: SERIAL_RE.search(r["title"]) is not None),
    ("noise_title", 22, lambda r: NOISE_RE.search(r["title"]) is not None),
    ("key_complete", 20, lambda r: all(r.get(f) for f in ("year", "set_name", "parallel"))),
    ("missing_set", 20, lambda r: r.get("year") and not r.get("set_name")),
    ("missing_parallel", 20, lambda r: r.get("set_name") and not r.get("parallel")),
    ("missing_year", 10, lambda r: not r.get("year")),
    ("cheap_under_15", 8, lambda r: r["price"] < 15),
]


def main():
    data = json.loads(CORPUS.read_text())
    rows = []
    for key, obs in data.items():
        player, market = key.split("|")
        for r in obs:
            if not r.get("title"):
                continue
            r = dict(r, player=player, _market=market)
            rows.append(r)
    rng = random.Random(20261016)
    rng.shuffle(rows)
    chosen, seen = [], set()
    for name, n, pred in STRATA:
        got = 0
        for r in rows:
            if got >= n:
                break
            if r["id"] in seen or not pred(r):
                continue
            seen.add(r["id"])
            chosen.append({"eval_id": "E{:03d}".format(len(chosen) + 1), "stratum": name,
                           "id": r["id"], "player": r["player"], "title": r["title"],
                           "price": r["price"], "date": r["date"],
                           "title_truncated": r.get("title_truncated", False)})
            got += 1
    # Engine would-flag listings (true deals or false positives -- the labels decide).
    import sys
    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timezone
    from scripts import replay_corpus
    from src import price_history
    obs = replay_corpus.reextract(price_history.deduped_observations(data))
    _, _, _, _, flagged = replay_corpus.replay_new(obs, 3, datetime(2026, 10, 16, tzinfo=timezone.utc))
    by_id = {r["id"]: r for r in rows}
    for _, _, _, o in flagged:
        r = by_id.get(o["id"])
        if r is None or r["id"] in seen:
            continue
        seen.add(r["id"])
        chosen.append({"eval_id": "E{:03d}".format(len(chosen) + 1), "stratum": "engine_flag",
                       "id": r["id"], "player": r["player"], "title": r["title"],
                       "price": r["price"], "date": r["date"],
                       "title_truncated": r.get("title_truncated", False)})
    # 75/25 split, stratified: every 4th item of each stratum goes to holdout.
    per = {}
    for c in chosen:
        i = per.get(c["stratum"], 0)
        per[c["stratum"]] = i + 1
        c["split"] = "holdout" if i % 4 == 3 else "tune"
    OUT.write_text(json.dumps(chosen, indent=1, ensure_ascii=False) + "\n")
    print(len(chosen), "candidates;", sum(c["split"] == "holdout" for c in chosen), "holdout")


if __name__ == "__main__":
    main()
