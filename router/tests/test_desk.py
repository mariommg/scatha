"""The sorting desk and scatha's deck builder.

What these pin: a click saves exactly one item and never loses another item's answer
(the old review page lost a definition to a snapshot race); only the launch token on
this machine can read or write; close calls mean the same thing everywhere; a post is
shown in English when it isn't English, reusing the note's translation before paying
for one; carried-over answers never overwrite answers given on the desk.
"""

import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from jev_router import Taxonomy, app, desk, notes, review, topics


def deck_with(n_topics=7, items=None):
    ts = [{"key": f"t{i}", "label": f"Topic {i}", "question": "q", "yes_means": "y", "no_means": "n"} for i in range(n_topics)]
    items = items or [{"id": str(i), "author": f"a{i}", "placed": "t0", "status": "route",
                       "scores": {t["key"]: 0.1 for t in ts} | {"t0": 0.9}} for i in range(3)]
    return {"title": "test", "topics": ts, "cutoff": 0.3, "topics_hash": desk.topics_hash(ts), "items": items}


# ---------- verdict store ----------

def test_put_touches_only_its_item(tmp_path):
    store = desk.Verdicts(tmp_path / "v.json")
    store.put("1", {"verdict": "t0"})
    store.put("2", {"verdict": "drop"})
    store.put("1", None)
    assert store.load() == {"2": {"verdict": "drop"}}


def test_concurrent_saves_all_survive(tmp_path):
    store = desk.Verdicts(tmp_path / "v.json")
    threads = [threading.Thread(target=store.put, args=(str(i), {"verdict": "t0"})) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(store.load()) == {str(i) for i in range(24)}


def test_record_rejects_unknown_and_clears_empty():
    d = deck_with(3)
    item = d["items"][0]
    with pytest.raises(ValueError):
        desk.record_for(item, d, "not_a_topic", "")
    assert desk.record_for(item, d, None, "  ") is None
    note_only = desk.record_for(item, d, None, "torn between two")
    assert note_only["verdict"] is None and note_only["note"] == "torn between two"
    r = desk.record_for(item, d, "drop", "")
    assert r["bucket"] is None and r["jev"] == "t0" and r["topics_hash"] == d["topics_hash"]
    assert desk.record_for(item, d, "t2", "")["bucket"] == "t2"


# ---------- server ----------

@pytest.fixture
def server(tmp_path):
    d = deck_with(7)
    srv = desk.make_server(d, desk.Verdicts(tmp_path / "v.json"), current_hash="different")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield SimpleNamespace(base=base, token=srv.token, store=tmp_path / "v.json", srv=srv)
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None, token=None, host=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    if token:
        req.add_header("X-Desk-Token", token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null") if "json" in r.headers.get("Content-Type", "") else r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, None


def test_server_needs_token_and_local_host(server):
    assert call(server.base, "/api/deck")[0] == 403
    assert call(server.base, "/api/deck", token="wrong")[0] == 403
    assert call(server.base, "/api/deck", token=server.token, host="evil.example")[0] == 403
    assert call(server.base, "/api/verdict", {"id": "0", "verdict": "t1"})[0] == 403
    assert not server.store.exists()


def test_server_saves_and_serves(server):
    code, page = call(server.base, "/")
    assert code == 200 and "Sorting Desk" in page
    code, data = call(server.base, "/api/deck", token=server.token)
    assert code == 200 and len(data["deck"]["topics"]) == 7 and data["stale"] is True
    assert call(server.base, "/api/verdict", {"id": "1", "verdict": "t6", "note": "x"}, server.token)[0] == 200
    assert call(server.base, "/api/verdict", {"id": "1", "verdict": "bogus"}, server.token)[0] == 400
    assert call(server.base, "/api/verdict", {"id": "nope", "verdict": "t1"}, server.token)[0] == 400
    saved = json.loads(server.store.read_text(encoding="utf-8"))["verdicts"]
    assert saved["1"]["verdict"] == "t6" and saved["1"]["note"] == "x"
    assert call(server.base, "/api/deck", token=server.token)[1]["verdicts"]["1"]["verdict"] == "t6"


# ---------- close calls ----------

def test_closeness():
    tie = desk.closeness({"a": 0.62, "b": 0.58, "c": 0.1}, 0.3)
    assert tie["close"] and tie["pair"] == ["a", "b"] and tie["gap"] == 0.04 and not tie["near_cut"]
    clear = desk.closeness({"a": 0.9, "b": 0.5}, 0.3)
    assert not clear["close"]
    near = desk.closeness({"a": 0.27, "b": 0.05}, 0.3)
    assert near["close"] and near["near_cut"] and near["pair"] is None
    low_tie = desk.closeness({"a": 0.12, "b": 0.11}, 0.3)  # both far below the line: dropped either way
    assert not low_tie["close"]


def test_topics_hash_ignores_labels():
    a = [{"key": "ai", "label": "AI", "question": "q", "yes_means": "y", "no_means": "n"}]
    b = [dict(a[0], label="Artificial intelligence")]
    c = [dict(a[0], no_means="changed")]
    assert desk.topics_hash(a) == desk.topics_hash(b) != desk.topics_hash(c)


# ---------- English on the desk ----------

TRANSLATED_NOTE = """---
title: "Ollaya runs decision models locally"
tweet_id: "42"
bucket: ai
---

# Ollaya runs decision models locally

> A local runtime for Jev-style models.

## Key points

- point

## The post

> Recently Jev and Kev shook up developers.
>
> Second paragraph.

> [!quote]- Original (ko)
> 최근 Jev와 Kev로

[@x on X](https://x.com/x/status/42)
"""


def test_parse_note_reads_translation():
    n = review.parse_note(TRANSLATED_NOTE)
    assert n["tweet_id"] == "42" and n["bucket"] == "ai" and n["language"] == "ko"
    assert n["english"] == "Recently Jev and Kev shook up developers.\n\nSecond paragraph."
    assert n["summary"] == "A local runtime for Jev-style models."
    english = TRANSLATED_NOTE.replace("> [!quote]- Original (ko)\n> 최근 Jev와 Kev로\n", "")
    assert review.parse_note(english)["english"] is None


@pytest.mark.parametrize("text,expected", [
    ("Windows keeps a thumbnail of every photo you've ever deleted.", True),
    ("My top 3 skill makers", True),
    ("최근 Jev와 Kev로 개발자 판이 발칵 뒤집히더니, 결국 끝판왕이 나와버렸음.", False),
    ("Hoy les comparto una herramienta que cambió por completo mi forma de trabajar con datos", False),
    ("https://t.co/abc", True),
])
def test_looks_english(text, expected):
    assert review.looks_english(text) is expected


def result(i, **scores):
    return SimpleNamespace(item_id=i, topics=scores, primary=max(scores, key=scores.get), status="route", input_tokens=10, error=None)


def test_make_item_prefers_note_translation():
    b = {"id": "42", "text": "최근 Jev와 Kev로", "tweetUrl": "u", "author": "x"}
    note = review.parse_note(TRANSLATED_NOTE) | {"path": "p"}
    item = review.make_item(b, result("42", ai=0.9, kw=0.2), note, {"language": "ko", "english": "haiku version"}, 0.3)
    assert item["text"].startswith("Recently Jev") and item["original"] == "최근 Jev와 Kev로" and item["language"] == "ko"
    haiku_only = review.make_item(b, result("42", ai=0.9, kw=0.2), None, {"language": "ko", "english": "haiku version"}, 0.3)
    assert haiku_only["text"] == "haiku version"
    english = review.make_item(dict(b, text="plain"), result("42", ai=0.9), None, {"language": "en", "english": None}, 0.3)
    assert english["text"] == "plain" and english["original"] is None and english["language"] is None


# ---------- building scatha's deck ----------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path))
    vault = tmp_path / "vault"
    vault.mkdir()
    ts = [{"label": "AI", **{k: topics.PRESETS["AI"][k] for k in ("question", "yes_means", "no_means")}, "destination": str(vault)},
          {"label": "Life hacks", **{k: topics.PRESETS["Life hacks"][k] for k in ("question", "yes_means", "no_means")}, "destination": str(tmp_path / "lh")}]
    topics.write_taxonomy(ts, tmp_path / "topics.yaml")
    app.save_config({})
    work = tmp_path / "work"
    (work / "runs" / "old").mkdir(parents=True)
    (work / "runs" / "new").mkdir(parents=True)
    old = [{"id": "1", "text": "old copy", "tweetUrl": "https://x.com/a/status/1", "author": "a"},
           {"id": "9", "text": "not in the ledger", "tweetUrl": "https://x.com/a/status/9", "author": "a"}]
    new = [{"id": "1", "text": "new copy", "tweetUrl": "https://x.com/a/status/1", "author": "a"},
           {"id": "42", "text": "최근 Jev와 Kev로 개발자 판이", "tweetUrl": "https://x.com/x/status/42", "author": "x"},
           {"id": "7", "text": "Hoy les comparto una herramienta que cambió mi forma de trabajar", "tweetUrl": "https://x.com/b/status/7", "author": "b"}]
    import os, time
    (work / "runs" / "old" / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": old}), encoding="utf-8")
    t = time.time()
    os.utime(work / "runs" / "old" / "pending-bookmarks.json", (t - 100, t - 100))
    (work / "runs" / "new" / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": new}), encoding="utf-8")
    (work / "ingested.md").write_text(
        "# ledger\n\n- https://x.com/a/status/1 | 2026-10-06 | route | ai\n"
        "- https://x.com/x/status/42 | 2026-10-06 | route | ai\n- https://x.com/b/status/7 | 2026-10-10 | skip | life_hackery\n",
        encoding="utf-8")
    (vault / "note.md").write_text(TRANSLATED_NOTE, encoding="utf-8")

    async def fake_score(bookmarks, tax):
        return [result(i, ai=0.5, life_hacks=0.46) for i in bookmarks]

    calls = []

    def fake_haiku(content, schema=None):
        calls.append(content[0]["text"])
        return {"posts": [{"id": "7", "language": "es", "english": "Today I share a tool"}]}, {}

    monkeypatch.setattr(review, "score", fake_score)
    monkeypatch.setattr(notes, "haiku", fake_haiku)
    return SimpleNamespace(path=tmp_path, calls=calls)


def test_collect_takes_newest_copy_in_ledger(home):
    got = review.collect_bookmarks(app.work())
    assert set(got) == {"1", "42", "7"} and got["1"]["text"] == "new copy"


def test_build_translates_only_unnoted_and_caches(home):
    deck = review.build(app.load_config(), say=lambda *a: None)
    items = {i["id"]: i for i in deck["items"]}
    assert len(home.calls) == 1 and '<post id="7">' in home.calls[0] and '<post id="42">' not in home.calls[0]
    assert items["7"]["text"] == "Today I share a tool" and items["7"]["language"] == "es"
    assert items["42"]["text"].startswith("Recently Jev") and items["42"]["note"]["title"]
    assert items["7"]["filed"] is None and items["1"]["filed"] == "ai"
    assert all(i["close"] and i["pair"] for i in deck["items"])
    review.build(app.load_config(), say=lambda *a: None)
    assert len(home.calls) == 1  # cached


def test_import_legacy_never_overwrites_desk_answers(home, tmp_path):
    tax = Taxonomy.load(app.home() / "topics.yaml")
    store = desk.Verdicts(review.verdicts_path())
    store.put("1", {"verdict": "ai", "source": "desk"})
    legacy = tmp_path / "old.json"
    legacy.write_text(json.dumps({"accept": {"life_hacks": ["life_hacks", "ai"]}, "verdicts": [
        {"id": "1", "author": "a", "bucket": "life_hacks", "note": ""},
        {"id": "2", "author": "b", "bucket": "life_hacks", "note": "n"},
        {"id": "3", "author": "c", "bucket": None, "note": "couldn't read it"},
        {"id": "4", "author": "d", "bucket": "retired_topic", "note": ""}]}), encoding="utf-8")
    assert review.import_legacy(legacy, tax, store, say=lambda *a: None) == 1
    v = store.load()
    assert v["1"]["verdict"] == "ai" and v["2"]["accept"] == ["life_hacks", "ai"] and v["2"]["source"] == "old.json"
    assert "3" not in v and "4" not in v
