"""Scatha never writes over a file it can't show is its own.

Probes on 2026-10-27 showed a note
written over another note with the same title, a personal note at the fallback name
overwritten, and a personal `scatha.md` replaced by the status note. What these pin: every
bookmark written gets its own file; a personal note at any candidate name survives; a rerun
doesn't write a second note for the same bookmark; the status note moves aside for a
personal note of the same name, and stays where it moved. Real files in a temp folder;
Haiku is faked.
"""

import argparse
import json

import pytest

from jev_router import app, notes, report, topics, unsorted, Taxonomy

B = {"author": "someone", "createdAt": "Sat Oct 24 10:00:00 +0000 2026"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    data, vault = tmp_path / "data", tmp_path / "vault"
    data.mkdir()
    vault.mkdir()
    monkeypatch.setenv(app.HOME_ENV, str(data))
    cfg = app.load_config()
    cfg["unsorted"]["folder"] = str(vault / "Unsorted")
    app.save_config(cfg)
    topics.write_taxonomy([{"label": "AI", **topics.template_definition("AI", "AI"), "destination": str(vault / "AI")}],
                          data / "topics.yaml")
    monkeypatch.setattr(app, "load_secrets", lambda: None)
    monkeypatch.setattr(notes, "haiku", lambda *a, **k: ({
        "title": "Same title", "summary": "Summary", "key_points": ["Point"], "tags": ["ai"], "language": "en",
        "post_in_english": None, "pull_quotes": [], "howto": None}, {}))
    return data, vault, cfg


def bm(i):
    return dict(B, id=str(i), text=f"AI bookmark {i}", tweetUrl=f"https://x.com/someone/status/{i}")


def write_batch(data, bookmarks):
    pending, routes = data / "pending.json", data / "routes.json"
    pending.write_text(json.dumps({"bookmarks": bookmarks}))
    routes.write_text(json.dumps([{"item_id": b["id"], "status": "route", "primary": "ai", "topics": {"ai": .9}}
                                  for b in bookmarks]))
    return notes.cmd_write(argparse.Namespace(pending=str(pending), routes=str(routes), taxonomy=str(data / "topics.yaml"),
                                              out=str(data / "preview"), ids=None, to_vault=True))


def ids_in(folder):
    return sorted(notes.existing_ids(folder))


def test_same_title_from_one_author_gives_each_bookmark_its_own_note(world):
    data, vault, cfg = world
    result = write_batch(data, [bm(1), bm(2), bm(3)])
    assert len(result["written"]) == 3 and len(list((vault / "AI").glob("*.md"))) == 3
    assert ids_in(vault / "AI") == ["1", "2", "3"]


def test_personal_notes_at_every_candidate_name_survive(world):
    data, vault, cfg = world
    folder = vault / "AI"
    folder.mkdir()
    mine = {name: f"Personal note {n}" for n, name in enumerate(
        ["Same title.md", "Same title (@someone).md", "Same title (@someone, 1).md"])}
    for name, text in mine.items():
        (folder / name).write_text(text)
    write_batch(data, [bm(1)])
    assert all((folder / name).read_text() == text for name, text in mine.items())
    assert ids_in(folder) == ["1"]


def test_a_rerun_writes_no_second_note(world):
    data, vault, cfg = world
    write_batch(data, [bm(1), bm(2)])
    again = write_batch(data, [bm(1), bm(2), bm(3)])
    assert sorted(again["present"]) == ["1", "2"] and len(again["written"]) == 1
    assert len(list((vault / "AI").glob("*.md"))) == 3


def test_light_notes_never_replace_a_personal_note(world):
    data, vault, cfg = world
    tax = Taxonomy.load(data / "topics.yaml")
    where = vault / "Unsorted"
    first = unsorted.write([(bm(1), {"ai": .1})], where, tax)[0]
    title = first.stem
    first.unlink()
    (where / f"{title}.md").write_text("Personal one")
    (where / f"{title} (1).md").write_text("Personal two")
    made = unsorted.write([(bm(1), {"ai": .1})], where, tax)
    assert (where / f"{title}.md").read_text() == "Personal one" and (where / f"{title} (1).md").read_text() == "Personal two"
    assert len(made) == 1 and "tweet_id" in made[0].read_text(encoding="utf-8")


def test_the_status_note_moves_aside_for_a_personal_note(world):
    data, vault, cfg = world
    mine = vault / "scatha.md"
    mine.write_text("My own dragon research")
    first = report.write_note(cfg)
    assert mine.read_text() == "My own dragon research"
    assert first == vault / "scatha status.md" and app.is_status_note(first)
    assert app.load_config()["status_note"] == str(first)
    assert report.write_note(app.load_config()) == first  # and stays there


def test_the_status_note_still_rewrites_itself(world):
    data, vault, cfg = world
    first = report.write_note(cfg)
    assert first == vault / "scatha.md" and report.write_note(cfg) == first
    assert "status_note" not in app.load_config() or app.load_config()["status_note"] in ("", None)
