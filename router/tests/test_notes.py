"""Note-writer invariants that need no model calls."""

from jev_router.notes import note_schema, verified_quotes


def test_only_verbatim_quotes_survive():
    src = {"post": "He said \u201cLive forever!\u201d and   walked off.", "images": "", "linked": ""}
    kept, dropped = verified_quotes([
        {"quote": 'He said "Live forever!"', "from": "post"},  # curly quotes and spacing normalize
        {"quote": "Live for never", "from": "post"},  # paraphrase
        {"quote": "He said", "from": "images"},  # right words, wrong source
    ], src)
    assert [q["quote"] for q in kept] == ['He said "Live forever!"']
    assert len(dropped) == 2


def test_howto_only_on_ai_notes():
    assert "howto" in note_schema("ai")["required"]
    for bucket in ("knowledge_work", "general_knowledge", "life_hackery", "journalism"):
        assert "howto" not in note_schema(bucket)["properties"]


def test_typography_does_not_count_as_a_misquote():
    src = {"post": "Have Opus vibe-code a quick \U0001d604\U0001d5f2\U0001d5f2\U0001d5f8\U0001d5f9\U0001d606 calendar. "
                   "I follow \u201cGenetic method\u201d by Toeplitz \u2014 and Lakatos.", "images": "", "linked": ""}
    kept, dropped = verified_quotes([
        {"quote": "vibe-code a quick weekly calendar", "from": "post"},  # styled letters
        {"quote": "I follow 'Genetic method' by Toeplitz - and Lakatos.", "from": "post"},  # quote marks, dash
        {"quote": "Have Opus ... weekly calendar", "from": "post"},  # elision
        {"quote": "calendar ... Have Opus", "from": "post"},  # elision out of order
    ], src)
    assert len(kept) == 3 and dropped[0]["quote"].startswith("calendar")


def _bookmark():
    long_article = "\n\n".join(f"Paragraph {i}. " + "word " * 50 for i in range(300))  # ~15k words, no budget cut
    return {
        "id": "1", "text": "the post itself", "author": "a", "tweetUrl": "https://x.com/a/status/1",
        "quoteContext": {"id": "2", "author": "b", "text": "quoted &amp; kept", "tweetUrl": "https://x.com/b/status/2"},
        "nestedQuote": {"author": "c", "text": "deeper"},
        "imageText": "line one\n\nline two",
        "links": [
            {"type": "tweet", "expanded": "https://x.com/b/status/2", "content": {"id": "2", "author": "b", "text": "quoted &amp; kept"}},
            {"type": "tweet", "expanded": "https://x.com/a/status/1", "content": {"id": "1", "author": "a", "text": "the post itself"}},
            {"type": "article", "expanded": "https://ex.com/p", "content": {"title": "Piece", "text": long_article}},
            {"type": "article", "expanded": "https://wsj.com/p", "content": {"title": "Paywalled", "description": "teaser", "paywalled": True}},
        ],
    }


def test_full_text_is_one_folded_callout_holding_everything_but_the_post():
    from jev_router.notes import FULL_TEXT, full_text
    lines = full_text(_bookmark())
    assert lines[0] == FULL_TEXT
    assert all(l == ">" or l.startswith("> ") for l in lines[1:])  # a bare line would end the callout
    body = "\n".join(lines)
    assert "the post itself" not in body  # already under "The post"
    assert body.count("quoted & kept") == 1  # quote and its t.co link are the same post
    assert "deeper" in body and "line two" in body and "teaser" in body
    assert "Paragraph 299." in body  # article text is not cut to the note-writer's budget


def test_full_text_empty_when_nothing_beyond_the_post():
    from jev_router.notes import full_text
    assert full_text({"id": "1", "text": "just a post", "links": []}) == []


def test_backfill_is_idempotent(tmp_path):
    import json
    from types import SimpleNamespace
    from jev_router.notes import FULL_TEXT, cmd_backfill
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"bookmarks": [_bookmark()]}), encoding="utf-8")
    note = tmp_path / "vault" / "n.md"
    note.parent.mkdir()
    note.write_text('---\ntweet_id: "1"\n---\n\n# Note\n', encoding="utf-8")
    args = SimpleNamespace(pending=[str(pending)], notes=[str(note.parent)], ids=None, apply=True, show=0)
    cmd_backfill(args)
    once = note.read_text(encoding="utf-8")
    cmd_backfill(args)
    assert note.read_text(encoding="utf-8") == once
    assert once.count(FULL_TEXT) == 1 and once.startswith('---\ntweet_id: "1"\n---\n\n# Note\n\n> [!abstract]')
