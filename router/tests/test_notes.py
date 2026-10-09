"""Note-writer invariants that need no model calls."""

import pytest

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


V = {"repo": "a/tool", "stars": 949, "pushed": "2026-02-03", "archived": False,
     "last_release": None, "checked": "2026-10-08"}


@pytest.mark.parametrize("url,repo", [
    ("https://github.com/a/tool", "a/tool"),
    ("https://github.com/a/tool/tree/main/src", "a/tool"),
    ("https://github.com/a/tool.git", "a/tool"),
    ("https://www.github.com/a/tool?tab=readme", "a/tool"),
    ("https://github.com/a", None),
    ("https://github.com/orgs/acme/repositories", None),
    ("https://gist.github.com/a/123", None),
    ("https://example.com/a/tool", None),
])
def test_repo_of(url, repo):
    from jev_router.github import repo_of
    assert repo_of(url) == repo


def _repo_bookmark(*urls):
    return {"id": "9", "text": "look", "author": "x", "tweetUrl": "https://x.com/x/status/9",
            "links": [{"type": "github", "expanded": u, "content": {"fullName": u.split("github.com/")[1]}} for u in urls]}


def _render(b, vitals=None):
    from jev_router.notes import render
    note = {"title": "T", "summary": "s", "tags": [], "key_points": ["k"]}
    return render(b, {"primary": "ai", "topics": {"ai": 0.9}}, note, 10, vitals)


def test_front_matter_carries_the_first_repos_vital_signs():
    import yaml
    b = _repo_bookmark("https://github.com/a/tool", "https://github.com/b/other")
    text = _render(b, {"a/tool": V, "b/other": V | {"repo": "b/other", "stars": 3, "archived": True}})
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["repo"] == "a/tool" and fm["repo_stars"] == 949 and fm["repo_archived"] is False
    assert str(fm["repo_pushed"]) == "2026-02-03" and str(fm["repo_checked"]) == "2026-10-08"
    assert "repo_last_release" not in fm  # no releases: no key, not an empty one
    assert "- [a/tool](https://github.com/a/tool) · 949 stars · pushed 2026-02-03\n" in text
    assert "- [b/other](https://github.com/b/other) · 3 stars · pushed 2026-02-03 · archived\n" in text


def test_no_vital_signs_means_no_repo_keys():
    text = _render(_repo_bookmark("https://github.com/a/tool"))
    assert "repo_" not in text and "\nrepo:" not in text
    assert "- [a/tool](https://github.com/a/tool)\n" in text  # owner/repo title, slash intact


def test_repo_vitals_backfill_is_idempotent(tmp_path, monkeypatch):
    import yaml
    from types import SimpleNamespace
    from jev_router import github, notes
    monkeypatch.setattr(github, "vitals", lambda n: V if n == "a/tool" else None)
    note = tmp_path / "n.md"
    note.write_text('---\ntweet_id: "1"\nwords: 5\n---\n\n# N\n\n## Links\n\n- [atool](https://github.com/a/tool)\n'
                    '- [Piece](https://ex.com/p)\n\n> [!abstract]- Full text\n> - [x](https://github.com/z/z)\n',
                    encoding="utf-8")
    before = note.read_text(encoding="utf-8")
    args = SimpleNamespace(notes=[str(tmp_path)], apply=False, show=0)
    notes.cmd_repo_vitals(args)
    assert note.read_text(encoding="utf-8") == before  # dry run
    args.apply = True
    notes.cmd_repo_vitals(args)
    once = note.read_text(encoding="utf-8")
    notes.cmd_repo_vitals(args)
    assert note.read_text(encoding="utf-8") == once
    assert yaml.safe_load(once.split("---")[1])["repo"] == "a/tool"
    assert "- [a/tool](https://github.com/a/tool) · 949 stars · pushed 2026-02-03\n- [Piece]" in once
    assert once.endswith("> - [x](https://github.com/z/z)\n")  # the full-text callout is left alone


@pytest.mark.parametrize("code,remaining,raises", [(404, "59", False), (403, "0", True), (429, None, True), (500, "50", False)])
def test_vitals_survives_api_errors(monkeypatch, code, remaining, raises):
    import email.message, urllib.error
    from jev_router import github
    def boom(*a, **k):
        h = email.message.Message()
        if remaining is not None:
            h["x-ratelimit-remaining"] = remaining
        raise urllib.error.HTTPError("u", code, "no", h, None)
    monkeypatch.setattr(github.urllib.request, "urlopen", boom)
    if raises:
        with pytest.raises(github.RateLimited):
            github.vitals("a/tool")
    else:
        assert github.vitals("a/tool") is None
