"""scatha's sorting desk: every bookmark it has processed, re-scored against today's topics.

    setup review              open the desk (builds it first if there isn't one)
    setup review --rebuild    re-score everything with the current topics.yaml, then open
    setup review --import F   carry over verdicts from an older review file, then open

The deck is built from the batches past runs saved (work/runs/*/pending-bookmarks.json),
limited to the ledger, so it covers what the person has actually seen filed or dropped.
Posts not in English are shown in English: the note writer's translation when there is a
note, otherwise one Haiku translation, cached. Jev always scores the original.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import os
import re
from pathlib import Path

from . import Router, Taxonomy, app, desk, notes
from .smaug import bookmark_state

TRANSLATE_BATCH = 8
EN_WORDS = {"the", "and", "to", "of", "a", "is", "in", "it", "you", "that", "for", "this", "on", "with",
            "i", "my", "your", "are", "be", "how", "what", "just", "not", "was", "can", "if", "they", "we"}


def tuning() -> Path:
    return app.home() / "tuning"


def deck_path() -> Path:
    return tuning() / "desk" / "deck.json"


def verdicts_path() -> Path:
    return tuning() / "verdicts.json"


def translations_path() -> Path:
    return tuning() / "desk" / "translations.json"


# ---------- sources ----------

def ledger(work: Path) -> dict[str, dict]:
    """id -> what the run did with it: {"status": route|skip, "bucket": key or None}."""
    path = work / "ingested.md"
    out = {}
    if path.exists():
        for m in re.finditer(r"status/(\d+)\s*\|[^|]*\|\s*(\w+)\s*(?:\|\s*(\w+))?", path.read_text(encoding="utf-8")):
            out[m.group(1)] = {"status": m.group(2), "bucket": m.group(3) if m.group(2) == "route" else None}
    return out


def ledger_ids(work: Path) -> set[str]:
    return set(ledger(work))


def collect_bookmarks(work: Path) -> dict[str, dict]:
    """The newest saved copy of each ledger bookmark (later runs carry image text and cleaner articles)."""
    want = ledger_ids(work)
    files = sorted(work.glob("runs/*/pending-bookmarks.json")) + sorted(work.glob("run*/pending-bookmarks.json"))
    files.sort(key=lambda p: p.stat().st_mtime)
    out: dict[str, dict] = {}
    for f in files:
        try:
            batch = json.loads(f.read_text(encoding="utf-8")).get("bookmarks", [])
        except (json.JSONDecodeError, OSError):
            continue
        for b in batch:
            if str(b.get("id")) in want:
                out[str(b["id"])] = b
    return out


def parse_note(text: str) -> dict:
    """What the desk shows from an existing note: title, summary, bucket, and the English of a translated post."""
    fm = {}
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
    summary = re.search(r"^# .*\n\n> (.+)$", text, re.M)
    out = {"title": fm.get("title", "").strip("'\""), "bucket": fm.get("bucket"), "tweet_id": fm.get("tweet_id"),
           "summary": summary.group(1).strip() if summary else None, "english": None, "language": None}
    orig = re.search(r"^> \[!quote\]- Original \(([\w-]+)\)$", text, re.M)
    post = re.search(r"^## The post\n\n((?:>.*\n)+)", text, re.M)
    if orig and post:
        out["language"] = orig.group(1)
        out["english"] = "\n".join(re.sub(r"^> ?", "", l) for l in post.group(1).splitlines()).strip()
    return out


def notes_by_id(tax: Taxonomy) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for t in tax.topics:
        if not t.destination or not Path(t.destination).is_dir():
            continue
        for f in Path(t.destination).glob("*.md"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            if not re.search(r'^tweet_id: "\d+"', text, re.M):
                continue
            n = parse_note(text)
            n["path"] = str(f)
            found[n["tweet_id"]] = n
    return found


# ---------- translation ----------

def looks_english(text: str) -> bool:
    """Cheap first pass so only plausible non-English posts cost a Haiku call. Errs toward asking."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    if not words:
        return True
    if sum(1 for w in words if not w.isascii()) > len(words) * 0.2:
        return False
    if len(words) < 6:
        return True
    return sum(1 for w in words if w in EN_WORDS) >= max(1, len(words) * 0.06)


TRANSLATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["posts"],
    "properties": {"posts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "language", "english"],
        "properties": {
            "id": {"type": "string"},
            "language": {"type": "string", "description": "ISO 639-1 code of the post's main language"},
            "english": {"type": ["string", "null"], "description": "Faithful English translation; null if the post is already English"},
        }}}},
}


def translate(posts: dict[str, str], cache: dict[str, dict], say=print) -> dict[str, dict]:
    """posts: id -> text. Fills cache (id -> {language, english}) in small batches; a failed batch is retried next build."""
    todo = [i for i in posts if i not in cache]
    for start in range(0, len(todo), TRANSLATE_BATCH):
        ids = todo[start:start + TRANSLATE_BATCH]
        body = "\n\n".join(f"<post id=\"{i}\">\n{posts[i]}\n</post>" for i in ids)
        prompt = ("For each post below, name its main language. If it is not English, translate the whole post "
                  "into plain, faithful English (keep line breaks, links and @handles; don't summarize). "
                  "If it is English, set english to null.\n\n" + body)
        try:
            answer, _ = notes.haiku([{"type": "text", "text": prompt}], TRANSLATE_SCHEMA)
        except Exception as e:  # keep the desk usable; untranslated posts show as-is
            say(f"  Translation failed for {len(ids)} posts ({str(e)[:120]}); they'll show untranslated.")
            continue
        for p in answer.get("posts", []):
            if p.get("id") in ids:
                cache[p["id"]] = {"language": (p.get("language") or "").lower() or None, "english": p.get("english")}
    return cache


# ---------- deck ----------

def _date(b: dict) -> str | None:
    try:
        return dt.datetime.strptime(b["createdAt"], "%a %b %d %H:%M:%S %z %Y").date().isoformat()
    except (KeyError, ValueError, TypeError):
        return None


def _links(b: dict) -> list[dict]:
    seen, out = set(), []
    for l in b.get("links") or []:
        url, kind = l.get("expanded"), l.get("type")
        if not url or url in seen or kind in ("media", "image") or (kind == "tweet" and str(b["id"]) in url):
            continue
        seen.add(url)
        c = l.get("content") or {}
        out.append({"url": url, "kind": kind, "title": c.get("title") or c.get("fullName")})
    return out


def topic_list(tax: Taxonomy) -> list[dict]:
    return [{"key": t.key, "label": t.label or t.key, "question": t.question, "yes_means": t.yes_means,
             "no_means": t.no_means} for t in tax.topics]


def make_item(b: dict, r, note: dict | None, tr: dict | None, cutoff: float, filed: dict | None = None) -> dict:
    original = notes.post_text(b)
    english, language = None, None
    if note and note.get("english"):
        english, language = note["english"], note["language"]
    elif tr and tr.get("english") and tr.get("language") not in (None, "en"):
        english, language = tr["english"], tr["language"]
    quote = None
    if b.get("quoteContext"):
        quote = f"@{b['quoteContext'].get('author')}: {html.unescape(b['quoteContext'].get('text') or '')}"
    item = {
        "id": str(b["id"]), "url": b.get("tweetUrl"), "author": b.get("author"), "author_name": b.get("authorName"),
        "date": _date(b), "text": english or original, "original": original if english else None,
        "language": language, "quote": quote, "image_text": b.get("imageText"), "links": _links(b),
        "note": {k: note[k] for k in ("title", "summary", "bucket", "path")} if note else None,
        "scores": {k: round(v, 3) for k, v in r.topics.items()}, "placed": r.primary, "status": r.status,
        "filed": (note or {}).get("bucket") or (filed or {}).get("bucket"),  # what actually happened, not today's score
    }
    item.update(desk.closeness(r.topics, cutoff))
    return item


async def score(bookmarks: dict[str, dict], tax: Taxonomy) -> list:
    items = [(i, bookmark_state(b)[0]) for i, b in bookmarks.items()]
    return await Router(tax).route_many(items)


def build(cfg: dict, say=print) -> dict:
    tax_path = app.taxonomy_path(cfg)
    tax = Taxonomy.load(tax_path)
    bookmarks = collect_bookmarks(app.work())
    if not bookmarks:
        raise SystemExit("  No processed bookmarks yet. The desk fills up after the first real run.")
    say(f"  Scoring {len(bookmarks)} bookmarks against your {len(tax.topics)} topics with Jev (a few cents)...")
    results = asyncio.run(score(bookmarks, tax))
    errors = [r for r in results if r.status == "error"]
    if errors:
        say(f"  {len(errors)} couldn't be scored and are left off (first: {errors[0].error[:120]}).")

    found = notes_by_id(tax)
    filed = ledger(app.work())
    cache_file = translations_path()
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    suspects = {i: notes.post_text(b) for i, b in bookmarks.items()
                if i not in found and not looks_english(notes.post_text(b))}
    if [i for i in suspects if i not in cache]:
        say(f"  Translating {len([i for i in suspects if i not in cache])} posts that may not be in English (Haiku)...")
        translate(suspects, cache, say)
        desk._write_json(cache_file, cache)

    items = [make_item(bookmarks[r.item_id], r, found.get(r.item_id), cache.get(r.item_id), tax.route_at,
                       filed.get(r.item_id))
             for r in results if r.status != "error"]
    items.sort(key=lambda it: it["id"], reverse=True)  # newest first
    deck = {"title": "scatha sorting desk", "topics": topic_list(tax), "cutoff": tax.route_at,
            "topics_hash": desk.topics_hash(topic_list(tax)),
            "built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "model": tax.model, "items": items,
            "tokens": sum(r.input_tokens or 0 for r in results)}
    desk._write_json(deck_path(), deck)
    close = sum(1 for it in items if it["close"])
    say(f"  Desk built: {len(items)} items, {close} close calls ({deck['tokens']:,} Jev input tokens).")
    return deck


# ---------- verdicts from older reviews ----------

def import_legacy(path: Path, tax: Taxonomy, store: desk.Verdicts, say=print) -> int:
    """Carry over a review file ({"verdicts": [{id, author, bucket, note}]}) as unconfirmed answers.

    Answers already given on this desk win. A file that predates a topic split may say which
    current topics an old label stands for: {"note": ..., "accept": {"old_key": ["new", ...]}}.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = {t.key for t in tax.topics}
    accept_map = raw.get("accept") or {}
    have = store.load()
    source = f"{Path(path).name}"
    n = 0
    for v in raw.get("verdicts", []):
        bid, bucket = str(v["id"]), v.get("bucket")
        if bid in have or not bucket or bucket not in keys:
            continue
        rec = {"verdict": bucket, "bucket": bucket, "note": v.get("note", ""), "author": v.get("author"),
               "jev": None, "scores": None, "topics_hash": None, "saved_at": None, "source": source}
        if bucket in accept_map:
            rec["accept"] = [k for k in accept_map[bucket] if k in keys]
        store.put(bid, rec)
        n += 1
    say(f"  Carried over {n} answers from {source} (shown as unconfirmed on the desk).")
    return n


def main(cfg: dict, rebuild: bool = False, import_file: str | None = None, open_browser: bool = True, say=print) -> int:
    tax = Taxonomy.load(app.taxonomy_path(cfg))
    current = desk.topics_hash(topic_list(tax))
    deck_file = deck_path()
    if rebuild or not deck_file.exists():
        app.load_secrets()
        os.environ["CLAUDE_BIN"] = cfg["tools"]["claude"]
        os.environ["NOTES_BACKEND"] = cfg["notes_backend"]
        build(cfg, say)
    elif json.loads(deck_file.read_text(encoding="utf-8")).get("topics_hash") != current:
        say(f"  Your topics changed since the desk was built; run {app.setup_command('review --rebuild')} to re-score.")
    store = desk.Verdicts(verdicts_path())
    if import_file:
        import_legacy(Path(import_file), tax, store, say)
    desk.serve(deck_file, verdicts_path(), current, open_browser, say)
    return 0
