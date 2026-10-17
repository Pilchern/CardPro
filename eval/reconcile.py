"""Reconcile two independent label files into eval/labels.json.

    python -m eval.reconcile labels-A.json labels-B.json

An item is VERIFIED when both labelers agree on is_single, deal_tier and,
for singles, on year, set_name, parallel, card_number, grader and grade
(case/space-insensitive; "Panini " prefix ignored). Otherwise UNVERIFIED and
excluded from scoring. The agreed values are A's (they match B's).
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "labels.json"
FIELDS = ("year", "set_name", "parallel", "card_number", "grader", "grade")


def norm(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace("panini ", "").replace("-", " ").lstrip("#")
    return s or None


def disagreements(a, b):
    out = []
    if a["is_single"] != b["is_single"]:
        out.append("is_single")
    if a["deal_tier"] != b["deal_tier"]:
        out.append("deal_tier")
    if a["is_single"] and b["is_single"]:
        for f in FIELDS:
            if norm(a["identity"].get(f)) != norm(b["identity"].get(f)):
                out.append(f)
    return out


def main(a_path, b_path):
    a = {x["eval_id"]: x for x in json.loads(Path(a_path).read_text())}
    b = {x["eval_id"]: x for x in json.loads(Path(b_path).read_text())}
    merged, field_dis = [], {}
    for eid in sorted(a):
        if eid not in b:
            continue
        dis = disagreements(a[eid], b[eid])
        rec = dict(a[eid])
        rec["status"] = "UNVERIFIED" if dis else "VERIFIED"
        rec["disagreements"] = dis
        rec["labeler_b"] = {"identity": b[eid]["identity"], "deal_tier": b[eid]["deal_tier"],
                            "is_single": b[eid]["is_single"]}
        for f in dis:
            field_dis[f] = field_dis.get(f, 0) + 1
        merged.append(rec)
    OUT.write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n")
    v = sum(r["status"] == "VERIFIED" for r in merged)
    print(f"{len(merged)} items, {v} VERIFIED, {len(merged) - v} UNVERIFIED; disagreements by field: {field_dis}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
