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
