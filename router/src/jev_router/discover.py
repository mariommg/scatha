"""Topic discovery: topics your bookmarks need that you haven't named yet.

Onboarding asks for topics before scatha has seen a single bookmark, so some kinds of
bookmark have no home. They pile up in Unsorted or squeak in under a weak fit. This finds
them:

    pool        bookmarks that fit no topic well (in Unsorted, or best score under WEAK)
                and that you haven't answered on the desk
    propose     one Haiku call: at most 3 new topics, each with at least 5 example
                bookmarks from the pool, defined by what you'd do with them
    check       Jev scores each candidate on every bookmark (a few cents), and per candidate
                the report says what it would rescue from the pool, which filed bookmarks it
                also fits (and under which topic), which it would pull away from their topic,
                and which of your desk answers it disagrees with

A candidate that rescues fewer than MIN_RESCUED is set aside: too small to be worth a
topic. Topics you said "Not for me" to (tuning/not-for-me.json) are shown to Haiku so it
doesn't propose them again. Nothing changes your topics until you pick Add; then the new
topic goes into topics.yaml (old copy kept), the desk and every filed note's topic list
learn it from the scores already in hand, and notes are never moved.

    scatha discover          propose and check (one Haiku call, a few cents of Jev)
    scatha discover --pool   just list the pool (free)
    scatha discover --last   decide on the newest saved run again (free)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path

from . import Router, Taxonomy, Topic, app, desk, review, topics, unsorted
from .smaug import bookmark_state

WEAK = 0.45  # a bookmark whose best topic scores under this barely fits anywhere
MAX_CANDIDATES = 3
MIN_MEMBERS = 5  # examples Haiku must name for a candidate
MIN_RESCUED = 5  # pool bookmarks Jev must agree on, or the candidate is set aside


def not_for_me_path() -> Path:
    return review.tuning() / "not-for-me.json"


def not_for_me() -> list[dict]:
    try:
        return json.loads(not_for_me_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def runs_dir() -> Path:
    return review.tuning() / "discovery"


# ---------- the pool ----------

def pool(deck: dict, answers: dict[str, dict], led: dict[str, dict]) -> list[dict]:
    """Deck items that fit no topic well and have no answer yet, newest first."""
    answered = {i for i, v in answers.items() if v.get("verdict") not in (None, desk.UNSURE)}
    out = []
    for it in deck["items"]:
        if it["id"] in answered:
            continue
        best = max(it["scores"].values()) if it["scores"] else 0.0
        if led.get(it["id"], {}).get("status") == "skip" or best < WEAK:
            out.append(it)
    return out


def gist(it: dict, n: int = 400) -> str:
    """What Haiku sees of one bookmark: the post, the titles of its links, a little image text."""
    parts = [" ".join((it.get("text") or "").split())[:n]]
    titles = [l["title"] for l in it.get("links") or [] if l.get("title")]
    if titles:
        parts.append("Links: " + "; ".join(t[:120] for t in titles[:3]))
    if it.get("image_text"):
        parts.append("Image text: " + " ".join(it["image_text"].split())[:200])
    return " | ".join(p for p in parts if p)


# ---------- propose ----------

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["topics"],
    "properties": {"topics": {"type": "array", "maxItems": MAX_CANDIDATES, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["label", "use", "question", "yes_means", "no_means", "examples"],
        "properties": {
            "label": {"type": "string", "description": "Short folder-style name, 1-3 words"},
            "use": {"type": "string", "description": "One sentence: what the person would do with these bookmarks"},
            "question": {"type": "string", "description": "One plain yes/no question, e.g. 'Is this item about X?'"},
            "yes_means": {"type": "string", "description": "What counts as yes: concrete kinds of posts, 1-3 sentences"},
            "no_means": {"type": "string", "description": "What counts as no, naming near-misses that belong to the existing topics"},
            "examples": {"type": "array", "items": {"type": "string"}, "description": "ids of pool bookmarks that belong"},
        }}}},
}


def prompt(items: list[dict], tax: Taxonomy, refused: list[dict]) -> str:
    existing = [{"topic": t.label or t.key, "means": t.yes_means} for t in tax.topics]
    return (
        "A person saves X (Twitter) posts. Each one is filed under one of their topics below. The posts listed "
        "under 'unfiled' fit none of those topics well. Find what is missing: propose at most "
        f"{MAX_CANDIDATES} new topics, only where at least {MIN_MEMBERS} unfiled posts clearly share one. "
        "Define each topic by what the person would do with the posts (learn a skill, share a laugh, follow a "
        "story), not by surface features like format or source. Don't propose a topic that repeats an existing "
        "one or one the person already turned down. A small classifier model will read question, yes_means and "
        "no_means and answer yes or no for every saved post, so keep each to one narrow judgment, use concrete "
        "examples of posts that count, avoid double negatives, and in no_means name the nearby things that "
        "belong to the existing topics. List the ids of the unfiled posts that belong in examples. If nothing "
        "is shared by enough posts, return no topics.\n\n"
        + json.dumps({"existing_topics": existing,
                      "turned_down": [{"topic": r.get("label"), "use": r.get("use")} for r in refused],
                      "unfiled": [{"id": it["id"], "post": gist(it)} for it in items]}, indent=1, ensure_ascii=False)
    )


def clean(raw: dict, pool_ids: set[str], tax: Taxonomy, refused: list[dict]) -> list[dict]:
    """Keep only well-formed candidates: real pool examples, enough of them, a new name."""
    taken = {(t.label or t.key).lower() for t in tax.topics} | {(r.get("label") or "").lower() for r in refused}
    out = []
    for c in (raw or {}).get("topics", [])[:MAX_CANDIDATES]:
        label = (c.get("label") or "").strip()
        examples = list(dict.fromkeys(str(e) for e in c.get("examples", []) if str(e) in pool_ids))
        if not label or label.lower() in taken or len(examples) < MIN_MEMBERS:
            continue
        taken.add(label.lower())
        out.append({"key": "new_" + topics.slug(label), "label": label, "use": c.get("use", "").strip(),
                    "question": c["question"].strip(), "yes_means": c["yes_means"].strip(),
                    "no_means": c["no_means"].strip(), "examples": examples})
    return out


def propose(items: list[dict], tax: Taxonomy, refused: list[dict], pool_ids: set[str]) -> tuple[list[dict], dict]:
    from .notes import haiku
    answer, usage = haiku([{"type": "text", "text": prompt(items, tax, refused)}], SCHEMA)
    return clean(answer, pool_ids, tax, refused), usage


# ---------- check ----------

async def _score(cands: list[dict], states: dict[str, dict], model: str | None, line: float):
    tax = Taxonomy(tuple(Topic(c["key"], c["question"], c["yes_means"], c["no_means"]) for c in cands),
                   route_at=line, review_at=line, model=model)
    return await Router(tax).route_many(list(states.items()))


def score(cands: list[dict], bookmarks: dict[str, dict], model: str | None, line: float) -> tuple[dict, int]:
    """{bookmark id: {candidate key: p}} for every bookmark, and Jev's input tokens."""
    states = {i: bookmark_state(b)[0] for i, b in bookmarks.items()}
    results = asyncio.run(_score(cands, states, model, line))
    return ({r.item_id: r.topics for r in results if r.status != "error"},
            sum(r.input_tokens or 0 for r in results))


def assess(cands: list[dict], scores: dict[str, dict], deck: dict, answers: dict[str, dict],
           led: dict[str, dict], pool_ids: set[str], line: float) -> list[dict]:
    """Per candidate: what it would rescue, overlap, pull away, and disagree with. Pure: no model calls."""
    items = {it["id"]: it for it in deck["items"]}
    labels = {t["key"]: t["label"] for t in deck["topics"]}
    out = []
    for c in cands:
        k = c["key"]
        fits = {i for i, s in scores.items() if s.get(k, 0.0) >= line}
        rescued = sorted(fits & pool_ids, key=lambda i: (-scores[i][k], i))
        overlap, moves, conflicts = {}, [], []
        for i in sorted(fits - pool_ids, key=lambda i: (-scores[i][k], i)):
            it = items.get(i)
            if not it:
                continue
            best = max(it["scores"], key=it["scores"].get) if it["scores"] else None
            if led.get(i, {}).get("status") == "route" and best:
                topic = labels.get(led[i].get("bucket") or best, best)
                overlap[topic] = overlap.get(topic, 0) + 1
                if scores[i][k] > it["scores"][best]:
                    moves.append(i)
        for i in sorted(fits):
            v = (answers.get(i) or {}).get("verdict")
            if v == desk.DROP:
                conflicts.append({"id": i, "said": "not worth keeping"})
            elif v in labels and i in items and scores[i][k] > items[i]["scores"].get(v, 0.0):
                conflicts.append({"id": i, "said": labels[v]})
        out.append(c | {
            "rescued": rescued, "agreed": sum(1 for e in c["examples"] if e in fits),
            "overlap": dict(sorted(overlap.items(), key=lambda kv: -kv[1])), "moves": moves,
            "conflicts": conflicts, "kept": len(rescued) >= MIN_RESCUED,
        })
    return out


# ---------- the whole thing ----------

def run(cfg: dict, say=print, pool_only: bool = False) -> dict | None:
    tax = Taxonomy.load(app.taxonomy_path(cfg))
    app.haiku_env(cfg)  # Jev's key for scoring new bookmarks; Haiku for translations and the proposal
    deck = review.build(cfg, say=say, reuse=True)
    answers = desk.Verdicts(review.verdicts_path()).load()
    led = review.ledger(app.work())
    items = pool(deck, answers, led)
    pool_ids = {it["id"] for it in items}
    in_unsorted = sum(1 for i in pool_ids if led.get(i, {}).get("status") == "skip")
    say(f"  Pool: {len(items)} bookmarks that fit no topic well ({in_unsorted} in Unsorted, "
        f"{len(items) - in_unsorted} filed on a weak fit), not counting {len(unsorted.judged_drops())} "
        "you said aren't worth keeping.")
    if pool_only:
        for it in items:
            say(f"    {it['id']}  @{it.get('author')}  {gist(it, 100)[:110]}")
        return None
    if len(items) < MIN_MEMBERS:
        say(f"  Too few to find a pattern in (a topic needs at least {MIN_MEMBERS}). Nothing to propose.")
        return None

    refused = not_for_me()
    say(f"  Asking Haiku what these have in common (one call)...")
    cands, usage = propose(items, tax, refused, pool_ids)
    if not cands:
        say("  Haiku found no group big enough to be a topic. Your topics cover what you save.")
        return None
    say(f"  {len(cands)} ideas: {', '.join(c['label'] for c in cands)}. "
        f"Checking each against all {len(deck['items'])} bookmarks with Jev (a few cents)...")
    bookmarks = {i: b for i, b in review.collect_bookmarks(app.work()).items() if i in {it['id'] for it in deck['items']}}
    scores, tokens = score(cands, bookmarks, tax.model, tax.route_at)
    found = assess(cands, scores, deck, answers, led, pool_ids, tax.route_at)
    caught = set().union(*(set(c["rescued"]) for c in found if c["kept"])) if found else set()
    result = {"at": dt.datetime.now().isoformat(timespec="seconds"), "line": tax.route_at,
              "pool": sorted(pool_ids), "candidates": found, "missed": sorted(pool_ids - caught),
              "scores": scores, "haiku_usage": usage, "jev_tokens": tokens,
              "topics_hash": deck.get("topics_hash")}
    runs_dir().mkdir(parents=True, exist_ok=True)
    path = runs_dir() / f"{dt.datetime.now():%Y-%m-%d_%H%M}.json"
    desk._write_json(path, result)
    say("")
    say(text(result, {it["id"]: it for it in deck["items"]}))
    say(f"\n  Saved: {path}")
    return result


def text(result: dict, items: dict[str, dict]) -> str:
    def who(i: str) -> str:
        it = items.get(i, {})
        return f"@{it.get('author')}: {gist(it, 70)[:80]}"

    kept = [c for c in result["candidates"] if c["kept"]]
    lines = [f"Topic ideas from {len(result['pool'])} bookmarks that fit nothing well:"]
    if not kept:
        lines.append(f"  None held up: Jev agreed on fewer than {MIN_RESCUED} bookmarks for each idea.")
    for c in kept:
        lines += ["", f"  {c['label']}: {c['use']}",
                  f"    Would rescue {len(c['rescued'])} of the {len(result['pool'])} "
                  f"(Haiku named {len(c['examples'])}; Jev agreed on {c['agreed']} of those)."]
        lines += [f"      {who(i)}" for i in c["rescued"][:5]]
        if len(c["rescued"]) > 5:
            lines.append(f"      ...and {len(c['rescued']) - 5} more")
        if c["overlap"]:
            lines.append("    Also fits bookmarks already filed: " +
                         ", ".join(f"{n} in {t}" for t, n in c["overlap"].items()) +
                         (f"; it would win {len(c['moves'])} of them (notes are never moved)." if c["moves"] else "."))
        if c["conflicts"]:
            lines.append(f"    Disagrees with {len(c['conflicts'])} of your desk answers:")
            lines += [f"      you said {x['said']}: {who(x['id'])}" for x in c["conflicts"][:5]]
        lines.append(f"    Jev's question: {c['question']}")
    set_aside = [c for c in result["candidates"] if not c["kept"]]
    if set_aside:
        lines += ["", "  Set aside (too few rescued): " +
                  ", ".join(f"{c['label']} ({len(c['rescued'])})" for c in set_aside)]
    lines += ["", f"  Still without a topic: {len(result['missed'])} of {len(result['pool'])}."]
    return "\n".join(lines)


# ---------- the user's call: Add / Not for me / Not now ----------

def forget(c: dict) -> None:
    """Not for me: remembered, and shown to Haiku so it isn't proposed again."""
    refused = not_for_me() + [{"label": c["label"], "use": c.get("use", ""),
                               "at": dt.date.today().isoformat()}]
    desk._write_json(not_for_me_path(), refused)


def check_destination(path: Path) -> str | None:
    """Why a folder can't take notes, or None. Nothing is created here: notes make it when they land."""
    if not path.is_absolute():
        return "Give a full path (starting from a drive or /)."
    if ".obsidian" in path.parts:
        return "That's inside Obsidian's settings folder."
    if path.exists() and not path.is_dir():
        return "That's a file, not a folder."
    parent = next((p for p in [path, *path.parents] if p.exists()), None)
    if parent is None or not os.access(parent, os.W_OK):
        return "Can't write there."
    return None


def add_topic(cfg: dict, c: dict, scores: dict[str, dict], label: str, destination: Path, say=print) -> str:
    """Add a checked candidate to topics.yaml and bring everything that reads topics up to date, using the
    scores discovery already has (no rescoring): the desk's deck, and the topic lists in filed notes.
    Notes never move. Returns the new topic's key."""
    import yaml
    from types import SimpleNamespace
    from . import decide, notes
    tax_path = app.taxonomy_path(cfg)
    raw = yaml.safe_load(tax_path.read_text(encoding="utf-8"))
    key = topics.slug(label)
    while key in raw["topics"] or key == "none":
        key += "_2"
    backup = review.tuning() / f"topics_before-{key}_{dt.datetime.now():%Y-%m-%d_%H%M}.yaml"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(tax_path.read_text(encoding="utf-8"), encoding="utf-8")
    raw["topics"][key] = {"label": label, "question": c["question"], "yes_means": c["yes_means"],
                          "no_means": c["no_means"], "destination": str(destination)}
    header = "".join(l + "\n" for l in tax_path.read_text(encoding="utf-8").splitlines() if l.startswith("#"))
    tax_path.write_text(header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    tax = Taxonomy.load(tax_path)
    say(f"  Added '{label}' to {tax_path} (the old file is kept as {backup.name}).")

    deck = json.loads(review.deck_path().read_text(encoding="utf-8"))
    for it in deck["items"]:
        it["scores"][key] = round((scores.get(it["id"]) or {}).get(c["key"], 0.0), 3)
        it["status"], it["placed"] = decide(tax, it["scores"])
        it.update(desk.closeness(it["scores"], tax.route_at))
    deck["topics"] = review.topic_list(tax)
    deck["topics_hash"] = desk.topics_hash(deck["topics"])
    desk._write_json(review.deck_path(), deck)

    folders = sorted({t.destination for t in tax.topics if t.destination and Path(t.destination).is_dir()})
    notes.cmd_topics(SimpleNamespace(scores=[str(review.deck_path())], taxonomy=str(tax_path),
                                     notes=folders, apply=True, show=0))
    return key


def last(cfg: dict, say=print) -> dict | None:
    """The newest saved discovery run, if the topics haven't changed since (else its numbers are stale)."""
    runs = sorted(runs_dir().glob("*.json")) if runs_dir().is_dir() else []
    if not runs:
        say(f"  No discovery run saved yet. `{app.command('discover')}` makes one.")
        return None
    result = json.loads(runs[-1].read_text(encoding="utf-8"))
    tax = Taxonomy.load(app.taxonomy_path(cfg))
    if result.get("topics_hash") != desk.topics_hash(review.topic_list(tax)):
        say(f"  Your topics changed since the last discovery run; `{app.command('discover')}` makes a fresh one.")
        return None
    deck = json.loads(review.deck_path().read_text(encoding="utf-8"))
    say(text(result, {it["id"]: it for it in deck["items"]}))
    return result


def decide_each(cfg: dict, result: dict, ui) -> int:
    """Ask about each candidate that held up. `ui` is the setup wizard's prompt helpers. Returns topics added."""
    from . import vaults
    led = review.ledger(app.work())
    added = 0
    for c in [c for c in result["candidates"] if c["kept"]]:
        pick = ui.choose(f"'{c['label']}': what do you want to do?",
                         ["Add it as a topic", "Not for me (don't suggest it again)", "Not now"], default=3)
        if pick == 2:
            forget(c)
            ui.ok(f"Noted. '{c['label']}' won't be suggested again.")
            continue
        if pick == 3:
            continue
        label = ui.ask("Name it", default=c["label"])
        tax = Taxonomy.load(app.taxonomy_path(cfg))
        dest = pick_destination(ui, tax, vaults.find_vaults(), label)
        key = add_topic(cfg, c, result["scores"], label, dest, ui.say)
        added += 1
        unsorted_ids = [i for i in c["rescued"] if led.get(i, {}).get("status") == "skip"]
        if unsorted_ids and ui.yes(f"Write full notes for the {len(unsorted_ids)} bookmarks it rescues from Unsorted "
                                   f"into {dest}? (Haiku, about 2,000 Claude tokens each)"):
            review.write_up(cfg, {i: key for i in unsorted_ids}, ui.say)
        if c["moves"]:
            ui.hint(f"{len(c['moves'])} filed notes now fit '{label}' better than where they are. "
                    "They stay put; their topic lists now include it.")
    if added:
        from . import report
        report.write_note(cfg)
    return added


def pick_destination(ui, tax: Taxonomy, found: list[Path], label: str) -> Path:
    """A folder for the new topic. Nothing assumed: a known vault (then a subfolder), a folder another
    topic already uses (topics may share one), or any path."""
    used = list(dict.fromkeys(Path(t.destination) for t in tax.topics if t.destination))
    options = ([f"A new folder in {v.name}  {ui.dim(str(v))}" for v in found]
               + [f"Share {p.name}/ with another topic  {ui.dim(str(p))}" for p in used]
               + ["Another folder (type a path)"])
    while True:
        pick = ui.choose(f"Where should '{label}' notes go?", options, default=1)
        if pick <= len(found):
            dest = ui.pick_subfolder(found[pick - 1], label)
        elif pick <= len(found) + len(used):
            dest = used[pick - len(found) - 1]
        else:
            dest = Path(ui.ask("Folder path")).expanduser()
        problem = check_destination(dest)
        if not problem:
            return dest
        ui.bad(problem)
