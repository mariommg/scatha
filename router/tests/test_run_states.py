"""Every bookmark a run takes ends somewhere the user can see: a note, Unsorted, or a retry.

Probes on 2026-10-27 found runs that reported success while
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
    assert not (app.work() / "pending-bookmarks.json").exists()


# A run that stopped early used to leave the report and status note showing the last success.

def test_a_stopped_run_shows_in_the_report_with_steps_to_try(world, monkeypatch):
    data, vault, cfg = world
    report.record_run({"ai": 5}, 0, 0, 0, "light")
    report.mark_seen()
    monkeypatch.setattr(checks, "preflight", lambda cfg: ["your X login has expired (logged out of X?)"])
    assert run.main([]) == 2
    text = report.text(app.load_config())
    assert "The last run stopped" in text and "Your X login has expired" in text
    steps = text.split("What to try:")[1].split("\n\n")[0].strip().splitlines()
    assert 2 <= len(steps) <= 3 and "setup x-login" in steps[0]
    assert "Last successful run" in text and "5 new notes" in text  # the counts aren't replaced or invented
    page = (vault / "scatha.md").read_text(encoding="utf-8")
    assert "## The last run stopped" in page and "setup x-login" in page
    assert "stopped before it imported anything" in report.banner()
    assert len(report.history()) == 1


@pytest.mark.parametrize("stage", ["preflight", "fetch", "read", "crash"])
def test_every_kind_of_stop_offers_two_or_three_steps(world, stage):
    assert 2 <= len(report.stop_steps({"stage": stage, "problems": ["something"]})) <= 3


def test_the_next_run_that_gets_through_clears_the_stop(world, monkeypatch):
    data, vault, cfg = world
    report.record_stop("fetch", ["Fetching your bookmarks from X failed."])
    routed_as(monkeypatch, "skip", "ai", {"ai": .1})
    (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(1)]}))
    assert run.main([]) == 0
    assert report.last_stop() is None and "The last run stopped" not in report.text(app.load_config())


def test_a_run_with_nothing_new_clears_the_stop_in_the_vault_too(world, monkeypatch):
    # the terminal cleared, the status note kept "The last run stopped"
    data, vault, cfg = world
    report.record_run({"ai": 5}, 0, 0, 0, "light")
    report.record_stop("fetch", ["Fetching your bookmarks from X failed."])
    page = report.write_note(cfg)
    assert "The last run stopped" in page.read_text(encoding="utf-8")
    monkeypatch.setattr(run, "sh", lambda *a, **k: (0, ""))  # the fetch works and finds nothing new
    assert run.main([]) == 0
    assert report.last_stop() is None and "The last run stopped" not in report.text(app.load_config())
    assert "The last run stopped" not in page.read_text(encoding="utf-8") and "5 new notes" in page.read_text(encoding="utf-8")


def test_a_crash_is_recorded_and_still_raised(world, monkeypatch):
    (app.work() / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": [bm(1)]}))
    monkeypatch.setattr(notes, "cmd_describe", lambda a: (_ for _ in ()).throw(KeyError("boom")))
    with pytest.raises(KeyError):
        run.main([])
    assert report.last_stop()["stage"] == "crash" and "KeyError" in report.last_stop()["problems"][0]  # saved with the run, not resumed as if new
