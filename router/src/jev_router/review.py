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

import argparse
import asyncio
import datetime as dt
import html
import json
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
    """id -> what the run did with it: {"status": route|skip, "bucket": key or None, "author": handle or None}."""
    path = work / "ingested.md"
    out = {}
    if path.exists():
        for m in re.finditer(r"(?:/([^/\s]+))?/status/(\d+)\s*\|[^|]*\|\s*(\w+)\s*(?:\|\s*(\w+))?",
                             path.read_text(encoding="utf-8")):
            out[m.group(2)] = {"status": m.group(3), "bucket": m.group(4) if m.group(3) == "route" else None,
                               "author": m.group(1)}
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


def build(cfg: dict, say=print, reuse: bool = False) -> dict:
    """Score every processed bookmark. reuse=True keeps the scores already on the desk (same topics)
    and scores only bookmarks processed since, so opening the desk after a run costs pennies or nothing."""
    from types import SimpleNamespace
    tax_path = app.taxonomy_path(cfg)
    tax = Taxonomy.load(tax_path)
    bookmarks = collect_bookmarks(app.work())
    if not bookmarks:
        raise SystemExit("  No processed bookmarks yet. The desk fills up after the first real run.")
    kept = []
    if reuse and deck_path().exists():
        old = json.loads(deck_path().read_text(encoding="utf-8"))
        if old.get("topics_hash") == desk.topics_hash(topic_list(tax)):
            kept = [SimpleNamespace(item_id=i["id"], topics=i["scores"], primary=i["placed"], status=i["status"],
                                    input_tokens=0, error=None) for i in old["items"] if i["id"] in bookmarks]
    fresh = {i: b for i, b in bookmarks.items() if i not in {k.item_id for k in kept}}
    if fresh:
        say(f"  Scoring {len(fresh)} bookmarks against your {len(tax.topics)} topics with Jev (a few cents)...")
    results = kept + (asyncio.run(score(fresh, tax)) if fresh else [])
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
    say(f"  Desk ready: {len(items)} items, {close} close calls"
        + (f" ({deck['tokens']:,} Jev input tokens)." if deck["tokens"] else ", no new scoring needed."))
    return deck


# ---------- after the desk: Unsorted bookmarks you sorted ----------

def set_ledger(work: Path, changes: dict[str, str]) -> None:
    """Mark bookmarks as routed to a topic in the ledger (id -> topic key), keeping each line's date."""
    path = work / "ingested.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines):
        m = re.search(r"status/(\d+)", line)
        if m and m.group(1) in changes:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                lines[n] = f"{parts[0]} | {parts[1]} | route | {changes[m.group(1)]}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sorted_unsorted(cfg: dict, tax: Taxonomy) -> tuple[dict[str, str], list[str]]:
    """Verdicts on bookmarks the run didn't file: {id: topic} to write up, [ids] marked Don't keep."""
    from . import unsorted
    led = ledger(app.work())
    store = desk.Verdicts(verdicts_path()).load()
    keys = {t.key for t in tax.topics}
    light = unsorted.notes_by_id(unsorted.folder(cfg, tax))
    to_write = {i: v["verdict"] for i, v in store.items()
                if v.get("verdict") in keys and led.get(i, {}).get("status") == "skip"}
    to_remove = [i for i, v in store.items() if v.get("verdict") == desk.DROP and i in light]
    return to_write, to_remove


def finish_sorting(cfg: dict, say=print, ask=None) -> int:
    """Offer to write full notes for Unsorted bookmarks given a topic, and remove the ones marked Don't keep."""
    from . import notes as notes_mod, report, unsorted
    tax = Taxonomy.load(app.taxonomy_path(cfg))
    to_write, to_remove = sorted_unsorted(cfg, tax)
    ask = ask or (lambda q: input(f"  {q} [Y/n] ").strip().lower() in ("", "y", "yes"))
    light = unsorted.notes_by_id(unsorted.folder(cfg, tax))
    done = 0
    if to_remove and ask(f"Remove the {len(to_remove)} Unsorted notes you marked Don't keep?"):
        for i in to_remove:
            light[i].unlink(missing_ok=True)
        say(f"  Removed {len(to_remove)} light notes.")
    if to_write and ask(f"Write full notes for the {len(to_write)} bookmarks you sorted out of Unsorted? "
                        f"(Haiku, about 2,000 Claude tokens each)"):
        app.haiku_env(cfg)
        bookmarks = collect_bookmarks(app.work())
        scores = {}
        if deck_path().exists():
            scores = {i["id"]: i["scores"] for i in json.loads(deck_path().read_text(encoding="utf-8"))["items"]}
        work_dir = tuning() / "desk" / "sorted"
        work_dir.mkdir(parents=True, exist_ok=True)
        ids = [i for i in to_write if i in bookmarks]
        (work_dir / "pending.json").write_text(json.dumps({"bookmarks": [bookmarks[i] for i in ids]}), encoding="utf-8")
        (work_dir / "routes.json").write_text(json.dumps(
            [{"item_id": i, "status": "route", "primary": to_write[i], "topics": scores.get(i) or {to_write[i]: 0.0}}
             for i in ids]), encoding="utf-8")
        result = notes_mod.cmd_write(argparse.Namespace(
            pending=str(work_dir / "pending.json"), routes=str(work_dir / "routes.json"),
            taxonomy=str(app.taxonomy_path(cfg)), out=str(work_dir / "preview"), ids=None, to_vault=True))
        written = {i: topic for i, topic, _ in result["written"]} | {i: to_write[i] for i in result["present"]}
        for i in written:
            if i in light:
                light[i].unlink(missing_ok=True)
        set_ledger(app.work(), written)
        done = len(result["written"])
        say(f"  {done} full notes written" + (f"; {len(result['failed'])} failed (try again later)." if result["failed"] else "."))
    report.write_note(cfg)
    return done


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
    stale = deck_file.exists() and json.loads(deck_file.read_text(encoding="utf-8")).get("topics_hash") != current
    if stale and not rebuild:
        say(f"  Your topics changed since the desk was built; `{app.command('review --rebuild')}` re-scores everything.")
    else:
        app.haiku_env(cfg)
        build(cfg, say, reuse=not rebuild)
    store = desk.Verdicts(verdicts_path())
    if import_file:
        import_legacy(Path(import_file), tax, store, say)
    desk.serve(deck_file, verdicts_path(), current, open_browser, say)
    finish_sorting(cfg, say)
    return 0
