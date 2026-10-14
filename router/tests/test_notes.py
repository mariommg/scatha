"""Note-writer invariants that need no model calls."""

import pytest

from jev_router.notes import note_schema, verified_quotes


def test_howto_is_offered_on_every_topic_and_may_be_empty():
    # Whether a note gets a how-to depends on the post (does it share something to reuse?),
    # not on which topic it landed in or what that topic is called
    s = note_schema()
    assert "howto" in s["required"]
    assert "null" in s["properties"]["howto"]["type"]


def test_a_note_without_a_howto_has_no_section():
    from jev_router.notes import render
    b = {"id": "9", "text": "an opinion", "author": "x", "tweetUrl": "https://x.com/x/status/9", "links": []}
    note = {"title": "T", "summary": "s", "tags": [], "key_points": ["k"], "howto": None}
    assert "## How to" not in render(b, {"primary": "journalism", "topics": {"journalism": 0.9}}, note, 10)


def quote(text, src="post"):
    return {"quote": text, "from": src}


def kept_quotes(quotes, post, images=""):
    kept, _ = verified_quotes(quotes, {"post": post, "images": images, "linked": ""})
    return [q["quote"] for q in kept]


def test_quotes_come_from_the_claimed_source_or_not_at_all():
    post = "He said “Live forever!” and walked off."
    assert kept_quotes([quote("Live for never"),  # paraphrase
                        quote("He said", "images")], post, images="nothing like it") == []  # right words, wrong source


def test_the_note_shows_the_source_characters_not_the_models():
    post = "He said “Live   forever!” and walked off. Then it rained."
    # Haiku's straight quotes, single spaces, and lowercase still find the passage; the note gets the original
    assert kept_quotes([quote('he said "Live forever!" and walked off.')], post) == \
        ["He said “Live   forever!” and walked off."]


def test_typography_does_not_count_as_a_misquote():
    post = ("Have Opus vibe-code a quick 𝘄𝗲𝗲𝗸𝗹𝘆 calendar.\n"
            "I follow “Genetic method” by Toeplitz — and Lakatos. Cafe\u0301 &amp; more.")  # e + combining accent
    assert kept_quotes([quote("Have Opus vibe-code a quick weekly calendar."),  # styled letters
                        quote("I follow 'Genetic method' by Toeplitz - and Lakatos.")], post) == [  # marks, dash
        "Have Opus vibe-code a quick 𝘄𝗲𝗲𝗸𝗹𝘆 calendar.",
        "I follow “Genetic method” by Toeplitz — and Lakatos."]
    assert kept_quotes([quote("Caf\u00e9 & more.")], post) == ["Caf\u00e9 & more."]  # accents, HTML entities


def test_cuts_are_not_allowed():
    post = "The drug is safe. We found no evidence that it works. More trials are planned."
    assert kept_quotes([quote("The drug is safe ... it works.")], post) == []
    assert kept_quotes([quote("The drug is safe… it works.")], post) == []
    # Ellipses the source itself contains are text like any other
    assert kept_quotes([quote("Wait... what?")], "Wait... what? No.") == ["Wait... what?"]


def test_a_partial_sentence_widens_to_the_whole_sentence():
    # Quoting the tail of a sentence must not drop its negation
    post = "Intro line. I do not think scaling alone gets us there. Next point."
    assert kept_quotes([quote("scaling alone gets us there")], post) == \
        ["I do not think scaling alone gets us there."]
    assert kept_quotes([quote("Intro line. I do not")], post) == ["Intro line. I do not think scaling alone gets us there."]
    # A sentence that ends in a closing quote mark keeps it; a line break ends a sentence
    assert kept_quotes([quote("ship it")], "She said “ship it.” Done.") == ["She said “ship it.”"]
    assert kept_quotes([quote("a headline")], "Just a headline\nThe body.") == ["Just a headline"]


def test_a_match_inside_a_word_does_not_count():
    assert kept_quotes([quote("said it")], "He unsaid it.") == []
    assert kept_quotes([quote("it work")], "Does it work? Yes.") == ["Does it work?"]  # sanity: whole words fine


def test_punctuation_at_the_edges_is_forgiven_since_the_source_sentence_is_shown():
    # Haiku ends a half-sentence with "." where the source goes on after a comma (4 of 399 vault quotes)
    post = "Inference is prohibitively expensive, restricting large parts of the web. Next."
    assert kept_quotes([quote("Inference is prohibitively expensive.")], post) == \
        ["Inference is prohibitively expensive, restricting large parts of the web."]


def test_paragraphs_glued_by_article_extraction_still_break_sentences():
    # Readability text often drops the break between paragraphs: "economy.Capital"
    post = "We must not base the value of life on output.Capital, it turns out, only fears health.Next one."
    assert kept_quotes([quote("Capital, it turns out, only fears health.")], post) == \
        ["Capital, it turns out, only fears health."]
    assert kept_quotes([quote("Pi is 3.14 or so")], "Pi is 3.14 or so. Yes.") == ["Pi is 3.14 or so."]  # not before a digit


def test_a_list_bullet_is_not_part_of_the_quote():
    post = "Principles:\n- Do not preserve backward compatibility.\n2. Grow the system in layers.\n"
    assert kept_quotes([quote("Do not preserve backward compatibility."),
                        quote("Grow the system in layers.")], post) == \
        ["Do not preserve backward compatibility.", "Grow the system in layers."]


def test_a_quote_that_widens_past_the_cap_is_dropped():
    long = "This one sentence " + "goes on and on " * 60 + "until it ends."
    assert kept_quotes([quote("This one sentence goes on")], long) == []


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


def test_a_note_about_one_repo_carries_its_vital_signs():
    import yaml
    text = _render(_repo_bookmark("https://github.com/a/tool"), {"a/tool": V})
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["repo"] == "a/tool" and fm["repo_stars"] == 949 and fm["repo_archived"] is False
    assert str(fm["repo_pushed"]) == "2026-02-03" and str(fm["repo_checked"]) == "2026-10-08"
    assert "repo_last_release" not in fm  # no releases: no key, not an empty one
    assert "- [a/tool](https://github.com/a/tool) · 949 stars · pushed 2026-02-03\n" in text


def test_one_owners_repos_are_one_project_named_by_the_first():
    import yaml
    b = _repo_bookmark("https://github.com/Paper/stereos", "https://github.com/paper/agentd")
    text = _render(b, {"Paper/stereos": V | {"repo": "Paper/stereos"}, "paper/agentd": V | {"repo": "paper/agentd"}})
    assert yaml.safe_load(text.split("---")[1])["repo"] == "Paper/stereos"


def test_a_note_listing_repos_is_about_none_of_them():
    import yaml
    b = _repo_bookmark("https://github.com/a/tool", "https://github.com/b/other")
    text = _render(b, {"a/tool": V, "b/other": V | {"repo": "b/other", "stars": 3, "archived": True}})
    assert not [k for k in yaml.safe_load(text.split("---")[1]) if k.startswith("repo")]
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


def test_repo_vitals_backfill_annotates_a_repo_list_without_front_matter(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from jev_router import github, notes
    looked = []
    monkeypatch.setattr(github, "vitals", lambda n: looked.append(n) or V | {"repo": n})
    note = tmp_path / "n.md"
    note.write_text('---\ntweet_id: "1"\n---\n\n# N\n\n## Links\n\n- [a](https://github.com/a/tool)\n'
                    '- [b](https://github.com/b/other)\n', encoding="utf-8")
    args = SimpleNamespace(notes=[str(tmp_path)], apply=True, show=0)
    notes.cmd_repo_vitals(args)
    once = note.read_text(encoding="utf-8")
    assert once.startswith('---\ntweet_id: "1"\n---\n') and "· 949 stars" in once.split("## Links")[1]
    notes.cmd_repo_vitals(args)  # annotated lines mark it done: no second lookup, no change
    assert note.read_text(encoding="utf-8") == once and len(looked) == 2


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


def test_quote_sources_cover_everything_haiku_was_shown():
    # 2026-10-14: a LessWrong comment lives in the page's description; the quote was real but dropped
    from jev_router.notes import quote_sources
    b = {"id": "1", "text": "post", "links": [
        {"type": "article", "expanded": "https://lesswrong.com/c", "content": {
            "title": "Which skincare products are evidence-based?",
            "description": "Comment by rosiecam - Sunscreen and retinoids get you almost all the way there."}},
        {"type": "github", "expanded": "https://github.com/a/b", "content": {"fullName": "a/b", "readme": "Install it with pip."}}]}
    src = quote_sources(b, translated=False)
    kept, _ = verified_quotes([{"quote": "Sunscreen and retinoids get you almost all the way there.", "from": "linked"},
                               {"quote": "Install it with pip.", "from": "linked"}], src)
    assert len(kept) == 2
    assert quote_sources(b, translated=True)["post"] == ""  # a translated post can't be quoted
