"""Score a routes.json against a regression set of reviewed verdicts.

    uv run python regression/score.py ROUTES.json ~/.scatha/tuning/verdicts.json

Compares two placement rules on the same Jev answers: the main-topic Choice
(among supported topics) and the strongest Noul. Reads the sorting desk's
verdicts.json (only topic answers count; "drop" and "unsure" are skipped) or an
older review file (a list of {id, author, bucket}). A verdict's "accept" list
names every topic that counts as right (old labels made before a topic split).
Labels in the 2026-10-06 file predate Life Hackery and accept either.
"""
import json
import sys

routes = {r["item_id"]: r for r in json.load(open(sys.argv[1], encoding="utf-8"))}
gold = json.load(open(sys.argv[2], encoding="utf-8"))["verdicts"]
if isinstance(gold, dict):  # the desk's verdicts.json: {id: record}
    gold = [dict(v, id=k) for k, v in gold.items()]
OK_AS = {"general_knowledge": {"general_knowledge", "life_hackery"}} if "2026-10-06" in sys.argv[2] else {}

hits = {"choice": 0, "strongest": 0}
n = 0
for v in gold:
    if not v["bucket"] or v["id"] not in routes:
        continue
    r = routes[v["id"]]
    accept = set(v.get("accept") or OK_AS.get(v["bucket"], {v["bucket"]}))
    strongest = max(r["topics"], key=r["topics"].get)
    n += 1
    hits["choice"] += r["primary"] in accept
    hits["strongest"] += strongest in accept
    if r["primary"] not in accept or strongest not in accept:
        t = {k: round(p, 2) for k, p in r["topics"].items()}
        print(f"  @{v['author']}: wanted {v['bucket']}; choice rule -> {r['primary']}, strongest -> {strongest}  {t}")
print(f"{n} verdicts: choice rule {hits['choice']}/{n}, strongest-Noul rule {hits['strongest']}/{n}")
