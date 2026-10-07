"""Smaug adapter: pending-bookmarks.json -> Jev routing table (dry pass).

    uv run python -m jev_router.smaug PENDING.json TAXONOMY.yaml --out DIR

Writes routes.json (full probabilities) and routes.md (table, borderline first).
Nothing is written to any vault.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
from dataclasses import asdict
from pathlib import Path

from . import Router, Taxonomy

EXCERPT_WORDS = 3000  # total linked text per bookmark; Jev accuracy falls as unrelated text grows


def _words(s: str | None) -> list[str]:
    return (s or "").split()


def _excerpt(s: str | None, limit: int = EXCERPT_WORDS) -> str | None:
    w = _words(s)
    if not w:
        return None
    return " ".join(w[:limit]) + (" [...]" if len(w) > limit else "")


def _link_state(link: dict, limit: int = EXCERPT_WORDS) -> tuple[dict, int]:
    """One link -> (state fragment, readable word count)."""
    c = link.get("content") or {}
    kind = link.get("type")
    out: dict = {"url": link.get("expanded"), "kind": kind}
    body = None
    if kind == "github":
        out["repo"] = c.get("fullName")
        out["description"] = c.get("description")
        body = c.get("readme")
    elif kind == "x-article":
        out["title"] = c.get("title")
        body = c.get("content") or c.get("previewText")
    elif kind == "article":
        out["title"] = c.get("title")
        out["summary"] = c.get("description")
        out["byline"] = c.get("byline")
        body = c.get("text")
        if c.get("paywalled"):
            out["note"] = "paywalled; only the public title and summary are available"
    elif kind == "tweet" and c.get("text"):
        out["quoted_post"] = f"@{c.get('author')}: {c.get('text')}"
    if body:
        out["text"] = _excerpt(body, limit)
    return {k: v for k, v in out.items() if v}, len(_words(body))


def bookmark_state(b: dict) -> tuple[dict, int]:
    text = b.get("threadText") or b.get("text") or ""
    links, words = [], len(_words(text))
    readable = [l for l in b.get("links", []) if l.get("type") not in ("media", "image")]
    # Split the word budget across links, and dedupe links Smaug saw twice
    seen, unique = set(), []
    for link in readable:
        if link.get("expanded") not in seen:
            seen.add(link.get("expanded"))
            unique.append(link)
    per_link = max(300, EXCERPT_WORDS // max(1, len(unique)))
    for link in unique:
        frag, n = _link_state(link, per_link)
        if len(frag) > 2:  # more than url + kind
            links.append(frag)
        words += n
    post = {
        "author": f"@{b.get('author')} ({b.get('authorName')})",
        "author_bio": b.get("authorBio"),
        "text": text,
        "text_in_attached_images": b.get("imageText"),
        "is_thread": bool(b.get("threadText")),
    }
    words += len(_words(b.get("imageText")))
    if b.get("mentions"):
        post["mentioned_accounts"] = {f"@{m['handle']}": m["bio"] for m in b["mentions"]}
    if b.get("quoteContext"):
        post["quoting"] = f"@{b['quoteContext']['author']}: {b['quoteContext']['text']}"
    if b.get("nestedQuote"):
        nq = b["nestedQuote"]
        post["quoting"] = post.get("quoting", "") + f"\n  which quotes @{nq['author']}: {nq['text']}"
    if b.get("replyContext"):
        post["replying_to"] = f"@{b['replyContext']['author']}: {b['replyContext']['text']}"
    state = {"bookmarked_post": {k: v for k, v in post.items() if v}}
    if links:
        state["linked_content"] = links
    return state, words


def _gist(b: dict, n: int = 90) -> str:
    s = " ".join(html.unescape(b.get("threadText") or b.get("text") or "").split())
    return (s[:n] + "...") if len(s) > n else s


def write_table(results, bookmarks: dict, tax: Taxonomy, path: Path) -> None:
    keys = [t.key for t in tax.topics]
    order = {"review": 0, "error": 1, "route": 2, "skip": 3}

    def uncertainty(r):  # closest-to-a-threshold first within each status
        best = max(r.topics.values(), default=0)
        return min(abs(best - tax.route_at), abs(best - tax.review_at))

    rows = sorted(results, key=lambda r: (order[r.status], uncertainty(r)))
    head = ["status", "main topic", *keys, "words", "post", "goes to"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        b = bookmarks[r.item_id]
        dest = Path(r.destination).name if r.destination else ""
        probs = [f"{r.topics.get(k, 0):.2f}" for k in keys]
        post = f"[@{b['author']}]({b['tweetUrl']}): {_gist(b)}".replace("|", "/")
        lines.append("| " + " | ".join([r.status, r.primary, *probs, str(r.extra.get("words", "")), post, dest]) + " |")
    counts = {s: sum(r.status == s for r in results) for s in order}
    tokens = sum(r.input_tokens or 0 for r in results)
    summary = (
        f"# Dry-pass routing table\n\n{len(results)} bookmarks: "
        + ", ".join(f"{v} {k}" for k, v in counts.items() if v)
        + f". Thresholds: route >= {tax.route_at}, review >= {tax.review_at}. "
        f"Model {tax.model}. Input tokens {tokens:,} (about ${tokens * 0.042 / 1e6:.4f}).\n\n"
        "Numbers are Jev's probability that the bookmark is about each topic. "
        "Review rows come first, closest-to-the-line first.\n\n"
    )
    path.write_text(summary + "\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pending")
    ap.add_argument("taxonomy")
    ap.add_argument("--out", default=".")
    args = ap.parse_args()
    await route_file(args.pending, args.taxonomy, args.out)


async def route_file(pending: str | Path, taxonomy: str | Path, out_dir: str | Path) -> list:
    tax = Taxonomy.load(taxonomy)
    bookmarks = {str(b["id"]): b for b in json.loads(Path(pending).read_text(encoding="utf-8"))["bookmarks"]}
    items, words = [], {}
    for bid, b in bookmarks.items():
        state, n = bookmark_state(b)
        items.append((bid, state))
        words[bid] = n

    results = await Router(tax).route_many(items)
    for r in results:
        r.extra["words"] = words[r.item_id]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "routes.json").write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    write_table(results, bookmarks, tax, out / "routes.md")
    errors = [r for r in results if r.status == "error"]
    print(f"{len(results)} routed -> {out / 'routes.md'}" + (f"; {len(errors)} errors, first: {errors[0].error}" if errors else ""))
    return results


if __name__ == "__main__":
    asyncio.run(main())
