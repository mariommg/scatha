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
        f"> If many unsorted notes are alike, you may be missing a topic: `{app.command('setup topics')}` adds one.",
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
        where.mkdir(parents=True, exist_ok=True)
        path = where / f"{_title(b)}.md"
        if path.exists():
            path = where / f"{_title(b)} ({b['id']}).md"
        path.write_text(render(b, scores, labels, tax.route_at), encoding="utf-8")
        written.append(path)
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
    todo = [(bookmarks[i], scores.get(i, {})) for i in sorted(skipped) if i in bookmarks and i not in have]
    if not apply:
        say(f"{len(todo)} light notes would be written to {where} (dry run; pass --apply)")
        return len(todo)
    written = write(todo, where, tax)
    say(f"{len(written)} light notes written to {where}")
    return len(written)


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
