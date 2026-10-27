"""Every bookmark a run takes ends somewhere the user can see: a note, Unsorted, or a retry.

From the 2026-10-27 pre-release review, whose probes found runs that reported success while
a bookmark went nowhere. What these pin: a bookmark between the two thresholds (when someone
sets them apart, as the RUNBOOK suggests) goes to Unsorted and the ledger like any skip, so
the desk and discovery can reach it. Routing, Haiku, X and ntfy are faked; files are real.
"""

import json
from types import SimpleNamespace

import pytest
import yaml

from jev_router import Taxonomy, app, checks, decide, notes, report, review, run, topics

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
    monkeypatch.setattr(checks, "preflight", lambda cfg: [])
    monkeypatch.setattr(run, "ping", lambda *a: None)
    app.work().mkdir()
    return data, vault, cfg


def bm(i, **extra):
    return dict(B, id=str(i), text=f"AI bookmark {i}", tweetUrl=f"https://x.com/someone/status/{i}", **extra)


def routed_as(monkeypatch, status, primary, scores):
    async def route(pending, tax, out):
        out.mkdir(parents=True, exist_ok=True)
        ids = [b["id"] for b in json.loads(pending.read_text())["bookmarks"]]
        rows = [{"item_id": i, "status": status, "primary": primary, "topics": scores} for i in ids]
        (out / "routes.json").write_text(json.dumps(rows))
        return [SimpleNamespace(**r) for r in rows]
    monkeypatch.setattr(run, "route_file", route)


def test_a_bookmark_between_the_thresholds_goes_to_unsorted(world, monkeypatch):
    data, vault, cfg = world
    path = data / "topics.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["thresholds"]["route"] = .6  # the RUNBOOK's "move the keep line", review left at 0.3
    path.write_text(yaml.safe_dump(raw))
    status, primary = decide(Taxonomy.load(path), {"ai": .45})
    assert status == "review"
    (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(1)]}))
    routed_as(monkeypatch, status, primary, {"ai": .45})
    assert run.main([]) == 0
    assert "status/1 | " in (app.work() / "ingested.md").read_text() and "| skip |" in (app.work() / "ingested.md").read_text()
    assert len(list((vault / "Unsorted").glob("*.md"))) == 1
    assert set(review.collect_bookmarks(app.work())) == {"1"}
    assert report.history()[-1]["unsorted"] == 1


def test_two_runs_in_one_minute_keep_both_saved_batches(world, monkeypatch):
    class Fixed(run.dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 27, 12, 34, 10, tzinfo=tz)
    monkeypatch.setattr(run.dt, "datetime", Fixed)
    routed_as(monkeypatch, "skip", "ai", {"ai": .1})
    for i in (1, 2):
        (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(i)]}))
        assert run.main([]) == 0
    assert set(review.ledger(app.work())) == {"1", "2"}
    assert set(review.collect_bookmarks(app.work())) == {"1", "2"}
    assert sorted(d.name for d in (app.work() / "runs").iterdir()) == ["2026-10-27_1234", "2026-10-27_1234-2"]


PHOTO = [{"type": "photo", "url": "https://example.invalid/image.png"}]


def test_a_failed_image_read_holds_the_bookmark_for_the_next_run(world, monkeypatch):
    data, vault, cfg = world
    led = app.work() / "ingested.md"
    outage = [True]

    def images(b):
        if outage[0]:
            raise RuntimeError("temporary image service outage")
        return "The text in the screenshot", {}
    monkeypatch.setattr(notes, "describe_images", images)
    routed_as(monkeypatch, "skip", "ai", {"ai": .1})
    (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(1, media=PHOTO), bm(2)]}))
    assert run.main([]) == 1  # something failed, and says so
    assert "status/1 " not in led.read_text() and "status/2 " in led.read_text()
    assert report.history()[-1]["failed"] == 1 and len(list((vault / "Unsorted").glob("*.md"))) == 1

    outage[0] = False  # next week: the fetch brings it back (it isn't in the ledger), and the images read
    (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(1, media=PHOTO)]}))
    assert run.main([]) == 0
    assert "status/1 " in led.read_text() and len(list((vault / "Unsorted").glob("*.md"))) == 2
    assert any("The text in the screenshot" in p.read_text(encoding="utf-8") for p in (vault / "Unsorted").glob("*.md"))


def test_when_every_image_read_fails_nothing_is_ledgered(world, monkeypatch):
    monkeypatch.setattr(notes, "describe_images", lambda b: (_ for _ in ()).throw(RuntimeError("outage")))
    routed_as(monkeypatch, "skip", "ai", {"ai": .1})
    (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(1, media=PHOTO)]}))
    assert run.main([]) == 1
    led = app.work() / "ingested.md"
    assert not led.exists() or "status/1" not in led.read_text()
    assert not (app.work() / "pending-bookmarks.json").exists()  # saved with the run, not resumed as if new
