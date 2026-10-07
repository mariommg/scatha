"""Smaug consumer, second half: read image posts, then write Obsidian notes.

Haiku runs through headless Claude Code (`claude -p --model haiku`), so it draws
on the user's Claude plan rather than an API key. Each call has no tools, no MCP
servers, and thinking off: one request in, one JSON object out.

    # before routing: read images on posts that are mostly pictures
    uv run python -m jev_router.notes describe PENDING.json

    # after routing: write notes (preview folder by default, never the vault)
    uv run python -m jev_router.notes write PENDING.json ROUTES.json TAXONOMY.yaml --out DIR [--ids ...]
    uv run python -m jev_router.notes write ... --to-vault
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import unicodedata
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import Taxonomy

CLAUDE_FLAGS = [
    "-p", "--model", "haiku", "--tools", "", "--strict-mcp-config",
    "--disable-slash-commands", "--no-session-persistence",
    "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
]
NOTE_WORDS = 6000  # linked text sent to Haiku per note
LONG_READ_WORDS = 1500
SHORT_TEXT_WORDS = 80  # below this, a post with photos is "mostly pictures"

def system_prompt() -> str:
    """READER_PROFILE (config: reader_profile) tells Haiku who the notes are for, e.g. their tools for how-tos."""
    profile = os.environ.get("READER_PROFILE", "").strip()
    return SYSTEM + (f" About the reader: {profile}" if profile else "")


SYSTEM = (
    "You turn saved X (Twitter) bookmarks into notes for the reader's personal knowledge base. "
    "Write in plain US English. Do not use em-dashes. Work only from the material given; "
    "when something is unclear, say less rather than guess. There is no length target: "
    "a thin post gets a short note, a rich piece gets the room it needs. Never pad."
)

NOTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "pull_quotes", "key_points", "tags", "language", "post_in_english"],
    "properties": {
        "title": {"type": "string", "description": "Descriptive note title, under 80 characters, no clickbait"},
        "summary": {"type": "string", "description": "What this is and why it is useful, in as few sentences as it takes"},
        "pull_quotes": {
            "type": "array", "maxItems": 2,
            "description": (
                "Zero to two passages copied EXACTLY, character for character, from the material: the lede, "
                "the main point, or the line that best brings the piece back to mind. Use them when the "
                "material has real prose worth quoting; return an empty list for thin posts."
            ),
            "items": {
                "type": "object", "additionalProperties": False, "required": ["quote", "from"],
                "properties": {
                    "quote": {"type": "string", "description": "Verbatim text, 1 to 3 sentences"},
                    "from": {"type": "string", "enum": ["post", "images", "linked"],
                             "description": "Which part of the material it was copied from"},
                },
            },
        },
        "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                       "description": "As many points as the material supports, no more. Each point must add information not already in the summary or another point. If the substance is in a video or anything else you cannot see, say so in one point."},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5,
                 "description": "Lowercase-kebab topic tags"},
        "language": {"type": "string", "description": "ISO 639-1 code of the post's language"},
        "post_in_english": {"type": ["string", "null"],
                            "description": "English translation of the post text if it is not English, else null"},
    },
}

HOWTO = {
    "type": ["object", "null"],
    "description": (
        "Fill this when the item shares a recipe, prompt, skill, tool, setup, or technique someone could "
        "reuse; null for news, opinion, or commentary. Ground every field in the material; where the material "
        "leaves a step out, say what is missing instead of inventing it."
    ),
    "additionalProperties": False,
    "required": ["what_they_did", "claimed_benefits", "steps_to_reproduce", "needs"],
    "properties": {
        "what_they_did": {"type": "string", "description": "How the author did it, concretely"},
        "claimed_benefits": {"type": "array", "items": {"type": "string"},
                             "description": "Benefits the author claims, attributed as claims"},
        "steps_to_reproduce": {"type": "array", "items": {"type": "string"},
                               "description": "Ordered steps for the reader to try it in their own setup"},
        "needs": {"type": "array", "items": {"type": "string"},
                  "description": "Accounts, tools, files, or prerequisites the steps need"},
    },
}


def note_schema(bucket: str) -> dict:
    """The HOWTO section is asked for on AI notes only."""
    if bucket != "ai":
        return NOTE_SCHEMA
    s = json.loads(json.dumps(NOTE_SCHEMA))
    s["properties"]["howto"] = HOWTO
    s["required"].append("howto")
    return s


def _norm(s: str) -> str:
    """Compare text, not typography: Unicode styled letters, quote marks, dashes, spacing."""
    s = unicodedata.normalize("NFKC", html.unescape(s or ""))  # NFKC maps styled letters like 𝘄 to w
    s = re.sub(r"[\"'‘’“”«»`]", "", s)
    s = re.sub(r"[‐-―]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _found(quote: str, hay: str) -> bool:
    """Every ellipsis-separated fragment appears, in order."""
    pos = 0
    for frag in re.split(r"\.\.\.|…|\[\.\.\.\]", quote):
        frag = _norm(frag).strip(" .,;:")
        if not frag:
            continue
        i = hay.find(frag, pos)
        if i < 0:
            return False
        pos = i + len(frag)
    return pos > 0


def verified_quotes(quotes: list[dict], sources: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Keep only quotes that really appear in the part of the material they claim."""
    kept, dropped = [], []
    for q in quotes or []:
        (kept if _found(q.get("quote", ""), _norm(sources.get(q.get("from"), ""))) else dropped).append(q)
    return kept, dropped


API_MODEL = "claude-haiku-4-5"


def haiku(content: list[dict], schema: dict | None = None) -> tuple[dict | str, dict]:
    """One Haiku call through the configured backend. Returns (answer, usage).

    NOTES_BACKEND=claude-code (default) runs headless Claude Code on the user's plan;
    NOTES_BACKEND=anthropic-api calls the API with ANTHROPIC_API_KEY (pay as you go).
    """
    if os.environ.get("NOTES_BACKEND") == "anthropic-api":
        return _haiku_api(content, schema)
    return _haiku_claude_code(content, schema)


def _api_schema(schema: dict) -> dict:
    """Structured outputs accept a subset of JSON Schema; drop array-length limits (the prompt states them)."""
    if isinstance(schema, dict):
        return {k: _api_schema(v) for k, v in schema.items() if k not in ("minItems", "maxItems")}
    if isinstance(schema, list):
        return [_api_schema(v) for v in schema]
    return schema


def _haiku_api(content: list[dict], schema: dict | None) -> tuple[dict | str, dict]:
    import anthropic
    kwargs = {"model": API_MODEL, "max_tokens": 16000, "system": system_prompt(),
              "messages": [{"role": "user", "content": content}]}
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": _api_schema(schema)}}
    resp = anthropic.Anthropic().messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("the API declined this item (refusal)")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return (json.loads(text) if schema else text), usage


def _haiku_claude_code(content: list[dict], schema: dict | None) -> tuple[dict | str, dict]:
    args = [os.environ.get("CLAUDE_BIN", "claude"), *CLAUDE_FLAGS, "--system-prompt", system_prompt()]
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    msg = {"type": "user", "message": {"role": "user", "content": content}}
    env = os.environ | {"MAX_THINKING_TOKENS": "0"}
    proc = subprocess.run(args, input=json.dumps(msg) + "\n", capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=300)
    result = None
    for line in proc.stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "result":
            result = d
    if not result or result.get("is_error"):
        raise RuntimeError(f"claude -p failed: {(result or {}).get('result') or proc.stderr[-500:]}")
    answer = result.get("structured_output") if schema else result.get("result")
    return answer, result.get("usage") or {}


def _words(s: str | None) -> list[str]:
    return (s or "").split()


def _photos(b: dict) -> list[str]:
    return [m["url"] for m in b.get("media") or [] if m.get("type") == "photo" and m.get("url")]


def post_text(b: dict) -> str:
    return html.unescape(b.get("threadText") or b.get("text") or "").strip()


# ---------- describe: images -> text, before routing ----------

def describe_images(b: dict) -> tuple[str, dict]:
    content = [{"type": "image", "source": {"type": "url", "url": u}} for u in _photos(b)[:4]]
    content.append({"type": "text", "text": (
        "These images are attached to the X post below. Transcribe any text they contain, in order. "
        "If an image has no text, describe it in one sentence. Output plain text only.\n\n"
        f"Post by @{b.get('author')}: {post_text(b)}")})
    return haiku(content)


def cmd_describe(args) -> None:
    path = Path(args.pending)
    data = json.loads(path.read_text(encoding="utf-8"))
    todo = [b for b in data["bookmarks"]
            if _photos(b) and not b.get("imageText") and len(_words(post_text(b))) < SHORT_TEXT_WORDS
            and not any(l.get("type") in ("article", "x-article", "github") for l in b.get("links", []))]
    print(f"{len(todo)} mostly-image posts to read")
    def safe(b):
        try:
            return describe_images(b)
        except Exception as e:  # leave imageText unset; the post is routed on its text and retried next run
            return None, {"error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for b, (text, usage) in zip(todo, pool.map(safe, todo)):
            if text is None:
                print(f"  FAILED reading images for @{b['author']}: {usage['error'][:200]}")
                continue
            b["imageText"] = text
            print(f"  @{b['author']}: {len(_words(text))} words from {len(_photos(b))} images ({usage.get('input_tokens')} in)")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- write: routed bookmark -> Obsidian note ----------

def _linked(b: dict) -> list[dict]:
    seen, out = set(), []
    for l in b.get("links", []):
        url = l.get("expanded")
        if l.get("type") in ("media", "image", "tweet") or not url or url in seen:
            continue
        seen.add(url)
        c = l.get("content") or {}
        out.append({
            "url": url, "kind": l.get("type"),
            "title": c.get("title") or c.get("fullName"),
            "summary": c.get("description"),
            "text": c.get("text") or c.get("content") or c.get("readme") or c.get("previewText"),
        })
    return out


def material(b: dict) -> tuple[dict, int]:
    links = _linked(b)
    budget = NOTE_WORDS // max(1, len(links))
    words = len(_words(post_text(b))) + len(_words(b.get("imageText")))
    linked = []
    for l in links:
        w = _words(l.pop("text"))
        words += len(w)
        if w:
            l["text"] = " ".join(w[:budget]) + (" [...]" if len(w) > budget else "")
        linked.append({k: v for k, v in l.items() if v})
    m = {
        "author": f"@{b.get('author')} ({b.get('authorName')})",
        "author_bio": b.get("authorBio"),
        "post": post_text(b),
        "text_in_attached_images": b.get("imageText"),
        "quoting": b["quoteContext"]["text"] if b.get("quoteContext") else None,
        "linked_content": linked or None,
    }
    return {k: v for k, v in m.items() if v}, words


def _slug_title(t: str) -> str:
    t = re.sub(r'[<>:"/\\|?*#^\[\]]', "", t).strip().rstrip(".")
    return re.sub(r"\s+", " ", t)[:100] or "Untitled"


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def render(b: dict, r: dict, note: dict, words: int) -> str:
    created = dt.datetime.strptime(b["createdAt"], "%a %b %d %H:%M:%S %z %Y").date().isoformat() if b.get("createdAt") else ""
    tags = list(dict.fromkeys(note["tags"]))
    if words >= LONG_READ_WORDS:
        tags.append("long-read")
    if b.get("threadText"):
        tags.append("thread")
    if note.get("post_in_english"):
        tags.append("translated")
    if b.get("imageText"):
        tags.append("from-images")
    links = _linked(b)
    fm = [
        "---",
        f"title: {_yaml_str(note['title'])}",
        f"source: {b['tweetUrl']}",
        f"tweet_id: \"{b['id']}\"",
        f"author: \"@{b['author']}\"",
        f"posted: {created}",
        f"captured: {dt.date.today().isoformat()}",
        f"bucket: {r['primary']}",
        f"bucket_score: {r['topics'][r['primary']]:.2f}",
        "tags: [" + ", ".join(tags) + "]",
        f"words: {words}",
        "---",
    ]
    body = [f"# {note['title']}", "", f"> {note['summary']}", ""]
    label = {"post": "the post", "images": "the post's images (transcribed)", "linked": "the linked piece"}
    for q in note.get("pull_quotes") or []:
        body += [f"> [!quote] From {label[q['from']]}"]
        body += [f"> {line}" if line else ">" for line in q["quote"].splitlines()]
        body += [""]
    body += ["## Key points", ""]
    body += [f"- {p}" for p in note["key_points"]]
    howto = note.get("howto")
    if howto:
        body += ["", "## How to", "", "**What they did.** " + howto["what_they_did"]]
        if howto["claimed_benefits"]:
            body += ["", "**Claimed benefits**", ""] + [f"- {x}" for x in howto["claimed_benefits"]]
        if howto["needs"]:
            body += ["", "**You'll need**", ""] + [f"- {x}" for x in howto["needs"]]
        if howto["steps_to_reproduce"]:
            body += ["", "**To try it**", ""] + [f"{i}. {x}" for i, x in enumerate(howto["steps_to_reproduce"], 1)]
    body += ["", "## The post", ""]
    original = post_text(b)
    if note.get("post_in_english"):
        body += [f"> {line}" if line else ">" for line in note["post_in_english"].splitlines()]
        body += ["", f"> [!quote]- Original ({note['language']})"]
        body += [f"> {line}" if line else ">" for line in original.splitlines()]
    else:
        body += [f"> {line}" if line else ">" for line in original.splitlines()]
    body += ["", f"[@{b['author']} on X]({b['tweetUrl']})"]
    if links:
        body += ["", "## Links", ""]
        body += [f"- [{_slug_title(l['title']) if l.get('title') else l['url']}]({l['url']})" for l in links]
    extra = full_text(b)
    if extra:
        body += [""] + extra
    return "\n".join(fm + [""] + body) + "\n"


# ---------- full text: a folded copy of the source material, against link rot ----------

FULL_TEXT = "> [!abstract]- Full text"


def _quoted(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]


def full_text(b: dict) -> list[str]:
    """Everything the note was written from except the post itself (already under 'The post'), uncut."""
    sections, seen = [], {str(b.get("id"))}

    def tweet(label: str, t: dict | None) -> None:
        key = str(t.get("id") or t.get("text")) if t else None
        if not t or not t.get("text") or key in seen:
            return
        seen.add(key)
        url = t.get("tweetUrl")
        head = f"**{label} @{t.get('author')}**" + (f" ([on X]({url}))" if url else "")
        sections.append([head, ""] + html.unescape(t["text"]).strip().splitlines())

    tweet("In reply to", b.get("replyContext"))
    tweet("Quoting", b.get("quoteContext"))
    tweet("Which quotes", b.get("nestedQuote"))
    if b.get("imageText"):
        sections.append(["**Text in the images** (transcribed)", ""] + b["imageText"].strip().splitlines())
    for l in b.get("links", []):
        if l.get("type") == "tweet":
            tweet("Linked post by", l.get("content"))
    for l in _linked(b):
        title = _slug_title(l["title"]) if l.get("title") else l["url"]
        if l.get("text"):
            sections.append([f"**[{title}]({l['url']})**", ""] + l["text"].strip().splitlines())
        elif l.get("summary"):
            sections.append([f"**[{title}]({l['url']})** (summary only; the full text was not fetched)", ""]
                            + l["summary"].strip().splitlines())
    if not sections:
        return []
    lines = [FULL_TEXT]
    for i, s in enumerate(sections):
        lines += ([">"] if i else []) + _quoted("\n".join(s))
    return lines


def existing_ids(folder: Path) -> set[str]:
    ids = set()
    for f in folder.glob("*.md"):
        m = re.search(r'^tweet_id: "(\d+)"', f.read_text(encoding="utf-8", errors="ignore"), re.M)
        if m:
            ids.add(m.group(1))
    return ids


def cmd_write(args) -> dict:
    tax = Taxonomy.load(args.taxonomy)
    dest = {t.key: t.destination for t in tax.topics}
    bms = {str(b["id"]): b for b in json.loads(Path(args.pending).read_text(encoding="utf-8"))["bookmarks"]}
    routes = [r for r in json.loads(Path(args.routes).read_text(encoding="utf-8")) if r["status"] == "route"]
    if args.ids:
        routes = [r for r in routes if r["item_id"] in set(args.ids)]

    def folder_for(r) -> Path:
        return Path(dest[r["primary"]]) if args.to_vault else Path(args.out) / Path(dest[r["primary"]]).name

    done = {}
    for r in routes:
        f = folder_for(r)
        if f not in done:
            done[f] = existing_ids(f) if f.exists() else set()
    todo = [r for r in routes if r["item_id"] not in done[folder_for(r)]]
    present = [r["item_id"] for r in routes if r["item_id"] in done[folder_for(r)]]
    print(f"{len(todo)} notes to write ({len(present)} already present)")

    def one(r):
        try:
            return note_for(r)
        except Exception as e:  # one bad note must not sink the batch; it is retried next run
            return r, bms[r["item_id"]], None, 0, {"error": f"{type(e).__name__}: {e}"}

    def note_for(r):
        b = bms[r["item_id"]]
        m, words = material(b)
        content = [{"type": "text", "text": "Write a note for this bookmark.\n\n" + json.dumps(m, ensure_ascii=False, indent=1)}]
        note, usage = haiku(content, note_schema(r["primary"]))
        sources = {
            "post": post_text(b) + "\n" + (b.get("quoteContext") or {}).get("text", ""),
            "images": b.get("imageText") or "",
            "linked": "\n".join((l.get("content") or {}).get("text") or (l.get("content") or {}).get("content") or ""
                                for l in b.get("links", [])),
        }
        if note.get("post_in_english"):  # the reader sees the translation; a verbatim quote would be untranslated
            sources["post"] = ""
        note["pull_quotes"], dropped = verified_quotes(note.get("pull_quotes"), sources)
        for q in dropped:
            print(f"  dropped a quote not found verbatim in {q.get('from')} (@{b['author']}): {q.get('quote', '')!r}")
        return r, b, note, words, usage

    tokens, written, failed = 0, [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r, b, note, words, usage in pool.map(one, todo):
            if note is None:
                failed.append((r["item_id"], usage["error"]))
                print(f"  FAILED @{b['author']}: {usage['error'][:200]}")
                continue
            folder = folder_for(r)
            folder.mkdir(parents=True, exist_ok=True)
            name = _slug_title(note["title"])
            path = folder / f"{name}.md"
            if path.exists():
                path = folder / f"{name} (@{b['author']}).md"
            path.write_text(render(b, r, note, words), encoding="utf-8")
            tokens += (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            written.append((r["item_id"], r["primary"], str(path)))
            print(f"  {r['primary']:17} {path.name}")
    print(f"done; {len(written)} written, {len(failed)} failed; {tokens:,} tokens through Claude Code")
    return {"written": written, "failed": failed, "present": present, "tokens": tokens}


def cmd_backfill(args) -> None:
    """Append the full-text callout to notes written before it existed. No LLM, no fetch; idempotent."""
    bms = {}
    for p in args.pending:
        for b in json.loads(Path(p).read_text(encoding="utf-8"))["bookmarks"]:
            bms[str(b["id"])] = b
    added, has, empty, unknown = [], 0, 0, 0
    for folder in args.notes:
        for f in sorted(Path(folder).rglob("*.md")):
            text = f.read_text(encoding="utf-8")
            m = re.search(r'^tweet_id: "(\d+)"', text, re.M)
            if not m:
                continue
            if args.ids and m.group(1) not in args.ids:
                continue
            b = bms.get(m.group(1))
            if b is None:
                unknown += 1
                continue
            if FULL_TEXT in text:
                has += 1
                continue
            extra = full_text(b)
            if not extra:
                empty += 1
                continue
            added.append(f)
            if args.apply:
                f.write_text(text.rstrip("\n") + "\n\n" + "\n".join(extra) + "\n", encoding="utf-8")
    verb = "added to" if args.apply else "would be added to (dry run; pass --apply)"
    print(f"full text {verb} {len(added)} notes; {has} already had it; {empty} have nothing beyond the post; "
          f"{unknown} notes have no raw bookmark in the given files")
    for f in added[:args.show]:
        print(f"  {f}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("describe")
    d.add_argument("pending")
    w = sub.add_parser("write")
    w.add_argument("pending")
    w.add_argument("routes")
    w.add_argument("taxonomy")
    w.add_argument("--out", default="notes-preview")
    w.add_argument("--ids", nargs="*")
    w.add_argument("--to-vault", action="store_true")
    f = sub.add_parser("backfill")
    f.add_argument("--pending", nargs="+", required=True)
    f.add_argument("--notes", nargs="+", required=True)
    f.add_argument("--ids", nargs="*")
    f.add_argument("--apply", action="store_true")
    f.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    {"describe": cmd_describe, "write": cmd_write, "backfill": cmd_backfill}[args.cmd](args)


if __name__ == "__main__":
    main()
