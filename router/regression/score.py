"""Score a routes.json against a regression set of reviewed verdicts.

    uv run python regression/score.py ROUTES.json regression/x-bookmarks-2026-10-06.json

Compares two placement rules on the same Jev answers: the main-topic Choice
(among supported topics) and the strongest Noul. Labels made before Life
Hackery existed accept either general_knowledge or life_hackery.
"""
import json
import sys

routes = {r["item_id"]: r for r in json.load(open(sys.argv[1], encoding="utf-8"))}
gold = json.load(open(sys.argv[2], encoding="utf-8"))["verdicts"]
OK_AS = {"general_knowledge": {"general_knowledge", "life_hackery"}}

hits = {"choice": 0, "strongest": 0}
n = 0
for v in gold:
    if not v["bucket"] or v["id"] not in routes:
        continue
    r = routes[v["id"]]
    accept = OK_AS.get(v["bucket"], {v["bucket"]})
    strongest = max(r["topics"], key=r["topics"].get)
    n += 1
    hits["choice"] += r["primary"] in accept
    hits["strongest"] += strongest in accept
    if r["primary"] not in accept or strongest not in accept:
        t = {k: round(p, 2) for k, p in r["topics"].items()}
        print(f"  @{v['author']}: wanted {v['bucket']}; choice rule -> {r['primary']}, strongest -> {strongest}  {t}")
print(f"{n} verdicts: choice rule {hits['choice']}/{n}, strongest-Noul rule {hits['strongest']}/{n}")
