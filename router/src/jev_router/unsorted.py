"""Unsorted: where bookmarks that fit none of your topics go, instead of disappearing.

Each becomes a *light* note: built straight from the source (the post, its links, text
in its images, the full text, Jev's scores) with no model call, so it costs nothing and
is still searchable. A callout at the top says why it's here and what to do about it.
Sorting one on the desk (`scatha review`) replaces it with a full note in that topic.

    config.json  "unsorted": {"mode": "light" | "off", "folder": "<path>"}

"off" keeps the old behavior: the bookmark is only listed in the ledger.

    uv run --project router python -m jev_router.unsorted backfill [--apply]
        light notes for bookmarks dropped before Unsorted existed (no model calls)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import Counter
from pathlib import Path

from . import Taxonomy, app, notes

LABEL = "Unsorted"


def folder(cfg: dict, tax: Taxonomy) -> Path:
    """The configured folder, else 'Unsorted' beside most of the topic folders."""
    configured = (cfg.get("unsorted") or {}).get("folder")
    if configured:
        return Path(configured).expanduser()
    parents = Counter(str(Path(t.destination).parent) for t in tax.topics if t.destination)
    base = Path(parents.most_common(1)[0][0]) if parents else app.home()
    return base / LABEL


def mode(cfg: dict) -> str:
    return (cfg.get("unsorted") or {}).get("mode", "light")


def _title(b: dict) -> str:
    """The post's first words; for a bare link, the linked page's title or the text in its images."""
    def first(text: str | None) -> str:
        return " ".join(re.sub(r"https?://\S+", "", text or "").split()[:9])
    titles = [l.get("title") for l in notes._linked(b) if l.get("title")]
    words = first(notes.post_text(b)) or first(titles[0] if titles else "") or first(b.get("imageText")) or "bookmark"
    return notes._slug_title(f"@{b.get('author') or 'unknown'} - {words}")


def callout(scores: dict[str, float], labels: dict[str, str], line: float) -> list[str]:
    best = max(scores, key=scores.get) if scores else None
    guess = f"{labels.get(best, best)} ({scores[best]:.2f})" if best else "none"
    return [
        "> [!note] scatha couldn't match this to your topics",
        f"> Best guess: {guess}; a bookmark needs {line:.2f} to be filed. "
        f"To sort it, type `{app.command('review')}`: picking a topic there writes its full note.",
        f"> If many unsorted notes are alike, you may be missing a topic: `{app.command('discover')}` looks for one.",
    ]


def render(b: dict, scores: dict[str, float], labels: dict[str, str], line: float) -> str:
    created = dt.datetime.strptime(b["createdAt"], "%a %b %d %H:%M:%S %z %Y").date().isoformat() if b.get("createdAt") else ""
    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    fm = [
        "---",
        f"title: {notes._yaml_str(_title(b))}",
        f"source: {b['tweetUrl']}",
        f"tweet_id: \"{b['id']}\"",
        f"author: \"@{b.get('author') or 'unknown'}\"",
        f"posted: {created}",
        f"captured: {dt.date.today().isoformat()}",
        "bucket: unsorted",
        "topics: []",
        "topic_scores: [" + ", ".join(f'"{k} {scores[k]:.2f}"' for k in ranked) + "]",
        "tags: [unsorted]",
        "---",
    ]
    body = [f"# {_title(b)}", "", *callout(scores, labels, line), "", "## The post", ""]
    body += [f"> {l}" if l else ">" for l in notes.post_text(b).splitlines()]
    body += ["", f"[@{b.get('author') or 'unknown'} on X]({b['tweetUrl']})"]
    links = notes._linked(b)
    if links:
        body += ["", "## Links", ""] + [notes.link_line(l["url"], l.get("title"), {}) for l in links]
    if b.get("imageText"):
        body += ["", "## Text in the images", ""] + [f"> {l}" if l else ">" for l in b["imageText"].splitlines()]
    extra = notes.full_text(b)
    if extra:
        body += [""] + extra
    return "\n".join(fm + [""] + body) + "\n"


def notes_by_id(where: Path) -> dict[str, Path]:
    out = {}
    if where.is_dir():
        for f in where.glob("*.md"):
            m = re.search(r'^tweet_id: "(\d+)"', f.read_text(encoding="utf-8", errors="ignore"), re.M)
            if m:
                out[m.group(1)] = f
    return out


def write(items: list[tuple[dict, dict[str, float]]], where: Path, tax: Taxonomy) -> list[Path]:
    """Light notes for (bookmark, scores) pairs; a bookmark that already has one is left alone."""
    labels = {t.key: t.label or t.key for t in tax.topics}
    have = notes_by_id(where)
    written = []
    for b, scores in items:
        if str(b["id"]) in have:
            continue
        written.append(app.write_new(where, [_title(b), f"{_title(b)} ({b['id']})"],
                                     render(b, scores, labels, tax.route_at)))
    if written:
        _remember(where, {str(b["id"]) for b, _ in items})
    return written


def count(cfg: dict, tax: Taxonomy) -> int:
    return len(notes_by_id(folder(cfg, tax)))


def newest(cfg: dict, tax: Taxonomy, n: int = 10) -> list[Path]:
    files = sorted(notes_by_id(folder(cfg, tax)).values(), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:n]


# ---------- backfill: bookmarks dropped before Unsorted existed ----------

def backfill(cfg: dict, apply: bool, say=print) -> int:
    import json
    from . import review
    tax = Taxonomy.load(app.taxonomy_path(cfg))
    led = review.ledger(app.work())
    skipped = {i for i, v in led.items() if v["status"] == "skip"}
    bookmarks = review.collect_bookmarks(app.work())
    deck = review.deck_path()
    scores = {}
    if deck.exists():
        scores = {i["id"]: i["scores"] for i in json.loads(deck.read_text(encoding="utf-8"))["items"]}
    where = folder(cfg, tax)
    have = notes_by_id(where)
    thrown_out = judged_drops()
    todo = [(bookmarks[i], scores.get(i, {})) for i in sorted(skipped)
            if i in bookmarks and i not in have and i not in thrown_out]
    if not apply:
        say(f"{len(todo)} light notes would be written to {where} (dry run; pass --apply)")
        return len(todo)
    written = write(todo, where, tax)
    say(f"{len(written)} light notes written to {where}")
    return len(written)


# ---------- "not worth keeping": light notes the user deleted ----------
#
# Unsorted is scatha's folder, so a light note that disappears from it is read as the user
# saying the bookmark isn't worth keeping, the same answer as Don't keep on the desk. It's
# recorded with the desk's verdicts (source "vault"), stays out of Unsorted and out of
# backfill, and can be changed on the desk like any other answer. Not a deletion: a note
# found elsewhere in the vault (moved), a missing folder (renamed or moved), a bookmark that
# already has an answer. Full notes deleted from topic folders are ignored: people prune
# filed notes for many reasons.

HOLD_OVER = 10  # more gone than this at once looks like a folder emptied or moved: ask first


def _seen_path() -> Path:
    return app.work() / "unsorted-seen.json"


def _load_seen() -> dict:
    """{"folder": the Unsorted folder looked at, "seen": ids there last time, "held": ids waiting for a yes}."""
    import json
    try:
        return json.loads(_seen_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_seen(where: Path, seen: set[str], held: set[str]) -> None:
    import json
    _seen_path().parent.mkdir(parents=True, exist_ok=True)
    _seen_path().write_text(json.dumps({"folder": str(where), "seen": sorted(seen), "held": sorted(held)}),
                            encoding="utf-8")


def _remember(where: Path, ids: set[str]) -> None:
    """Light notes just written, so one deleted before the next check still counts (previews don't)."""
    state = _load_seen()
    if state.get("folder") == str(where):
        _save_seen(where, set(state["seen"]) | ids, set(state.get("held", [])))


def judged_drops() -> set[str]:
    """Bookmarks the user said aren't worth keeping, on the desk or by deleting the light note."""
    from . import desk, review
    return {i for i, v in desk.Verdicts(review.verdicts_path()).load().items() if v.get("verdict") == desk.DROP}


def _vault_root(path: Path) -> Path:
    for p in [path, *path.parents]:
        if (p / ".obsidian").is_dir():
            return p
    return path.parent


def elsewhere(ids: set[str], cfg: dict, tax: Taxonomy) -> set[str]:
    """Which of these bookmarks have a note somewhere in the vaults scatha writes to (moved, not deleted).
    Hidden folders are skipped: Obsidian's .trash keeps a copy of what the user deleted."""
    roots = {_vault_root(Path(t.destination).expanduser()) for t in tax.topics if t.destination}
    roots.add(_vault_root(folder(cfg, tax)))
    found = set()
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(root).parts):
                continue
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:3000]
            except OSError:
                continue
            m = re.search(r'^tweet_id: "(\d+)"', head, re.M)
            if m and m.group(1) in ids:
                found.add(m.group(1))
    return found


def check(cfg: dict, tax: Taxonomy, ask=None, say=print) -> dict:
    """Record light notes deleted from Unsorted since the last look as not worth keeping.
    More than HOLD_OVER at once wait for a yes: `ask` is None in a night run (they stay held)."""
    from . import desk, review
    out = {"recorded": 0, "held": 0}
    if mode(cfg) == "off":
        return out
    where = folder(cfg, tax)
    if not where.is_dir():
        return out | {"missing": True}
    present = set(notes_by_id(where))
    state = _load_seen()
    if state.get("folder") != str(where):  # first look, or the user pointed Unsorted somewhere else
        _save_seen(where, present, set())
        return out
    led = review.ledger(app.work())
    store = desk.Verdicts(review.verdicts_path())
    answered = {i for i, v in store.load().items() if v.get("verdict") not in (None, desk.UNSURE)}
    gone = (set(state["seen"]) | set(state.get("held", []))) - present
    gone = {i for i in gone if led.get(i, {}).get("status") == "skip" and i not in answered}
    if gone:
        gone -= elsewhere(gone, cfg, tax)
    held: set[str] = set()
    if len(gone) > HOLD_OVER:
        if ask is None:
            held, gone = gone, set()
        elif not ask(f"{len(gone)} Unsorted notes are gone since {app.APP_NAME} last looked. "
                     "Count them all as not worth keeping?"):
            gone = set()  # a no is final: they were moved or cleared for some other reason
    if gone:
        scores = _deck_scores()
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for i in sorted(gone):
            store.put(i, {"verdict": desk.DROP, "bucket": None, "note": "", "author": led[i].get("author"),
                          "jev": None, "scores": scores.get(i), "topics_hash": None, "saved_at": now, "source": "vault"})
        say(f"{len(gone)} deleted from Unsorted, counted as not worth keeping.")
    _save_seen(where, present, held)
    return {"recorded": len(gone), "held": len(held)}


def _deck_scores() -> dict[str, dict]:
    import json
    from . import review
    try:
        return {i["id"]: i["scores"] for i in json.loads(review.deck_path().read_text(encoding="utf-8"))["items"]}
    except (OSError, ValueError, KeyError):
        return {}


def held_count() -> int:
    return len(_load_seen().get("held", []))


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m jev_router.unsorted")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    backfill(app.load_config(), args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
