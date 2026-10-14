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

from . import Taxonomy, github

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
                "Zero to two passages copied EXACTLY, character for character, from the material: whole "
                "sentences, no cuts or ellipses. Pick the lede, "
                "the main point, or the line that best brings the piece back to mind. Use them when the "
                "material has real prose worth quoting; return an empty list for thin posts."
            ),
            "items": {
                "type": "object", "additionalProperties": False, "required": ["quote", "from"],
                "properties": {
                    "quote": {"type": "string", "description": "Verbatim text, 1 to 3 whole consecutive sentences"},
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


def note_schema() -> dict:
    """The note's shape. HOWTO is offered on every topic; Haiku fills it only when the post shares
    something to reuse, so the content decides, not the topic it was filed under."""
    s = json.loads(json.dumps(NOTE_SCHEMA))
    s["properties"]["howto"] = HOWTO
    s["required"].append("howto")
    return s


QUOTE_MAX = 600  # characters, after widening to whole sentences
_MARKS = "\"'‘’“”«»`"
_CLOSERS = "\"'’”»)]"
_ENDS = ".!?"
_BULLET = re.compile(r"^(?:[-*•]|\d+[.)])\s+")  # a list item's marker is layout, not words


def _comparable(text: str) -> tuple[str, list[int]]:
    """Text with typography set aside (styled letters, quote marks, dashes, spacing, case),
    plus each character's position in `text`, so a match can be read back from the source."""
    out, at = [], []
    for i, c in enumerate(text):
        if c in _MARKS:
            continue
        if c.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                at.append(i)
            continue
        for d in unicodedata.normalize("NFKC", c).lower():  # NFKC maps styled letters like 𝘄 to w
            out.append("-" if "‐" <= d <= "―" else d)
            at.append(i)
    return "".join(out), at


def _starts_sentence(text: str, i: int) -> bool:
    j = i
    while j > 0 and text[j - 1] in " \t":
        j -= 1
    if j == 0 or text[j - 1] == "\n":
        return True
    if j == i and not text[i].isupper():  # "output.Capital": extraction glued two paragraphs
        return False
    while j > 0 and text[j - 1] in _CLOSERS:
        j -= 1
    return j > 0 and text[j - 1] in _ENDS


def _ends_sentence(text: str, e: int) -> bool:
    if e >= len(text) or text[e] == "\n":
        return True
    k = e
    while k > 0 and text[k - 1] in _CLOSERS:
        k -= 1
    return k > 0 and text[k - 1] in _ENDS and (text[e].isspace() or text[e].isupper())


def _source_passage(quote: str, source: str) -> str | None:
    """The whole sentences of `source` holding `quote` as one unbroken run of words, in the
    source's own characters; None if it isn't there. No cuts: "a ... b" only matches a
    source that says "a ... b". Widening to whole sentences keeps a quoted half-sentence
    from shedding the "I don't think" in front of it."""
    text = unicodedata.normalize("NFC", html.unescape(source or ""))
    hay, at = _comparable(text)
    # Edge punctuation can differ ("expensive." for "expensive, restricting..."): the source's sentence is shown anyway
    needle = _comparable(unicodedata.normalize("NFC", html.unescape(quote or "")))[0].strip(" .,;:")
    if not needle:
        return None
    i = hay.find(needle)
    while i >= 0:
        j = i + len(needle)
        whole_words = ((i == 0 or not (hay[i - 1].isalnum() and hay[i].isalnum()))
                       and (j == len(hay) or not (hay[j - 1].isalnum() and hay[j].isalnum())))
        if whole_words:
            s, e = at[i], at[j - 1] + 1
            while not _starts_sentence(text, s) and e - s <= QUOTE_MAX:
                s -= 1
            while not _ends_sentence(text, e) and e - s <= QUOTE_MAX:
                e += 1
            return _BULLET.sub("", text[s:e]) if e - s <= QUOTE_MAX else None
        i = hay.find(needle, i + 1)
    return None


def verified_quotes(quotes: list[dict], sources: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Keep quotes found in the part of the material they claim, rewritten as the source's own
    text (see _source_passage). The note never shows the model's copy, only the source's."""
    kept, dropped = [], []
    for q in quotes or []:
        passage = _source_passage(q.get("quote", ""), sources.get(q.get("from"), ""))
        if passage:
            kept.append(q | {"quote": passage})
        else:
            dropped.append(q)
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


def quote_sources(b: dict, translated: bool) -> dict[str, str]:
    """The text a quote may come from, per `from` label: everything Haiku was shown (material),
    uncut. A translated post can't be quoted: the reader sees the translation, not the original."""
    linked = [l.get("title") or "" for l in _linked(b)] + [l.get("summary") or "" for l in _linked(b)]
    linked += [l.get("text") or "" for l in _linked(b)]
    linked += [(l.get("content") or {}).get("text") or "" for l in b.get("links", []) if l.get("type") == "tweet"]
    return {
        "post": "" if translated else post_text(b) + "\n" + (b.get("quoteContext") or {}).get("text", ""),
        "images": b.get("imageText") or "",
        "linked": "\n".join(filter(None, linked)),
    }


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


def repos_in(b: dict) -> list[str]:
    """'owner/repo' for each GitHub repo the bookmark links to, in link order."""
    return list(dict.fromkeys(filter(None, (github.repo_of(l["url"]) for l in _linked(b)))))


def the_repo(names: list[str], vitals: dict) -> dict | None:
    """The vital signs for the front matter: only when every linked repo has one owner (one project,
    perhaps in parts), and then the first repo's. A list of repos from several owners isn't about any
    one of them; their signs go on their own lines under Links."""
    owners = {n.split("/")[0].lower() for n in names}
    return vitals.get(names[0]) if len(owners) == 1 else None


def render(b: dict, r: dict, note: dict, words: int, vitals: dict | None = None) -> str:
    """vitals: repo vital signs by 'owner/repo' (github.vitals)."""
    vitals = vitals or {}
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
    ]
    only = the_repo(repos_in(b), vitals)
    if only:
        fm += github.front_matter(only)
    fm.append("---")
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
        body += [link_line(l["url"], l.get("title"), vitals) for l in links]
    extra = full_text(b)
    if extra:
        body += [""] + extra
    return "\n".join(fm + [""] + body) + "\n"


# ---------- full text: a folded copy of the source material, against link rot ----------

FULL_TEXT = "> [!abstract]- Full text"


def _quoted(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]


def link_line(url: str, title: str | None, vitals: dict) -> str:
    """A Links bullet. A repo is titled owner/repo (slugging ate the slash) and carries its vital signs."""
    repo = github.repo_of(url)
    if repo:
        return f"- [{repo}]({url})" + (github.inline(vitals[repo]) if repo in vitals else "")
    return f"- [{_slug_title(title) if title else url}]({url})"


def lookup_vitals(names, cache: dict) -> None:
    """Fill cache with vital signs for names not yet in it. Stops quietly at GitHub's rate limit."""
    for n in names:
        if n in cache:
            continue
        try:
            cache[n] = github.vitals(n)
        except github.RateLimited:
            print("  GitHub rate limit reached; remaining notes go out without repo vital signs (set GITHUB_TOKEN to raise it)")
            cache.update({m: None for m in names if m not in cache})
            return


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
        note, usage = haiku(content, note_schema())
        sources = quote_sources(b, translated=bool(note.get("post_in_english")))
        note["pull_quotes"], dropped = verified_quotes(note.get("pull_quotes"), sources)
        for q in dropped:
            print(f"  dropped a quote not found verbatim in {q.get('from')} (@{b['author']}): {q.get('quote', '')!r}")
        return r, b, note, words, usage

    tokens, written, failed, repo_cache = 0, [], [], {}
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
            lookup_vitals(repos_in(b), repo_cache)
            vitals = {n: v for n, v in repo_cache.items() if v}
            path.write_text(render(b, r, note, words, vitals), encoding="utf-8")
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


LINK_RE = re.compile(r"^- \[[^\]]*\]\((https?://[^)\s]+)\)(.*)$")  # group 2: ' · stars...' once annotated


def links_section(text: str) -> tuple[str, str, str]:
    """(before, the lines under '## Links', after): the section ends at the next heading or callout."""
    head, sep, rest = text.partition("\n## Links\n")
    if not sep:
        return text, "", ""
    ends = [i for i in (rest.find("\n## "), rest.find("\n> [!")) if i >= 0]
    cut = min(ends) if ends else len(rest)
    return head + sep, rest[:cut], rest[cut:]


def linked_repos(section: str, unannotated: bool = False) -> list[str]:
    names = []
    for line in section.split("\n"):
        m = LINK_RE.match(line)
        repo = github.repo_of(m.group(1)) if m else None
        if repo and not (unannotated and m.group(2).startswith(" · ")):
            names.append(repo)
    return list(dict.fromkeys(names))


def add_vitals(text: str, vitals: dict) -> str:
    """Annotated repo lines under Links, and front matter keys if the note links just one repo,
    for a note written before vital signs."""
    head, section, tail = links_section(text)
    names = linked_repos(section)
    if not any(n in vitals for n in names):
        return text
    lines = []
    for line in section.split("\n"):
        m = LINK_RE.match(line)
        lines.append(link_line(m.group(1), None, vitals) if m and github.repo_of(m.group(1)) else line)
    only = the_repo(names, vitals)
    if only:
        end = head.index("\n---", 3)
        head = head[:end] + "\n" + "\n".join(github.front_matter(only)) + head[end:]
    return head + "\n".join(lines) + tail


def cmd_repo_vitals(args) -> None:
    """Add repo vital signs to notes written before they existed. No LLM; GitHub API only; idempotent."""
    todo = {}
    for folder in args.notes:
        for f in sorted(Path(folder).rglob("*.md")):
            text = f.read_text(encoding="utf-8")
            if not re.search(r'^tweet_id: "', text, re.M) or re.search(r"^repo_checked:", text, re.M):
                continue
            names = linked_repos(links_section(text)[1], unannotated=True)
            if names:
                todo[f] = (text, names)
    cache: dict = {}
    lookup_vitals(list(dict.fromkeys(n for _, ns in todo.values() for n in ns)), cache)
    vitals = {n: v for n, v in cache.items() if v}
    changed, left = [], 0
    for f, (text, names) in todo.items():
        new = add_vitals(text, vitals)
        if new == text:
            left += 1
            continue
        changed.append(f)
        if args.apply:
            f.write_text(new, encoding="utf-8")
    verb = "added to" if args.apply else "would be added to (dry run; pass --apply)"
    print(f"repo vital signs {verb} {len(changed)} notes; {left} link repos GitHub couldn't describe this time; "
          f"{len(vitals)} of {len(cache)} repos looked up")
    for f in changed[:args.show]:
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
    v = sub.add_parser("repo-vitals")
    v.add_argument("--notes", nargs="+", required=True)
    v.add_argument("--apply", action="store_true")
    v.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    {"describe": cmd_describe, "write": cmd_write, "backfill": cmd_backfill,
     "repo-vitals": cmd_repo_vitals}[args.cmd](args)


if __name__ == "__main__":
    main()
