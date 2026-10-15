"""Nothing disappears silently: bookmarks that fit no topic become light notes, and every
surface says what happened and what the user can do about it.

What these pin: a bookmark below the line is kept as a light note (no model call) unless
the user turned that off; the run records what it did, so a report written at midnight
reads the same in the morning; the banner shows once and goes quiet after the user looks;
levers appear only when they apply; sorting a bookmark out of Unsorted replaces its light
note with a full one and fixes the ledger, and nothing happens without a yes.
"""

import argparse
import json
from types import SimpleNamespace

import pytest
import yaml

from jev_router import Taxonomy, app, cli, desk, notes, report, review, run, topics, unsorted

from test_run import bookmark, fake, ledger  # noqa: F401  (fixture)


def tax_at(tmp_path):
    t = [{"label": n, "destination": str(tmp_path / "vault" / n), **{k: topics.PRESETS[n][k] for k in ("question", "yes_means", "no_means")}}
         for n in ("AI", "Journalism")]
    topics.write_taxonomy(t, tmp_path / "topics.yaml")
    return Taxonomy.load(tmp_path / "topics.yaml")


B = {"id": "77", "text": "Did you know that 1000 pico de gallos is a nano de gallo?", "author": "pun",
     "tweetUrl": "https://x.com/pun/status/77", "links": [], "createdAt": "Sat Jun 04 10:00:00 +0000 2022"}


# ---------- light notes ----------

def test_light_note_is_valid_and_says_what_to_do(tmp_path):
    tax = tax_at(tmp_path)
    text = unsorted.render(B, {"ai": 0.05, "journalism": 0.13}, {"ai": "AI", "journalism": "Journalism"}, 0.3)
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["bucket"] == "unsorted" and fm["topics"] == [] and fm["tweet_id"] == "77"
    assert fm["topic_scores"] == ["journalism 0.13", "ai 0.05"]
    assert "Best guess: Journalism (0.13)" in text and "review`" in text and "pico de gallos" in text


def test_light_notes_are_written_once(tmp_path):
    tax = tax_at(tmp_path)
    where = tmp_path / "vault" / "Unsorted"
    assert len(unsorted.write([(B, {"ai": 0.1})], where, tax)) == 1
    assert unsorted.write([(B, {"ai": 0.1})], where, tax) == []
    twin = dict(B, id="78", tweetUrl="https://x.com/pun/status/78")  # same text, so the same title
    assert len(unsorted.write([(twin, {"ai": 0.1})], where, tax)) == 1
    assert set(unsorted.notes_by_id(where)) == {"77", "78"}


def test_unsorted_folder_configured_or_beside_the_topics(tmp_path):
    tax = tax_at(tmp_path)
    assert unsorted.folder({}, tax) == tmp_path / "vault" / "Unsorted"
    assert unsorted.folder({"unsorted": {"folder": str(tmp_path / "x")}}, tax) == tmp_path / "x"


# ---------- the run ----------

def skipping_route(monkeypatch, skip_ids):
    async def route(pending, tax, run_dir):
        run_dir.mkdir(parents=True, exist_ok=True)
        ids = [b["id"] for b in json.loads(pending.read_text(encoding="utf-8"))["bookmarks"]]
        return [SimpleNamespace(item_id=i, status="skip" if i in skip_ids else "route", primary="ai",
                                topics={"ai": 0.2 if i in skip_ids else 0.9}) for i in ids]
    monkeypatch.setattr(run, "route_file", route)


def test_run_keeps_skipped_bookmarks_as_light_notes_and_reports(fake, monkeypatch):
    fake.prepared = [bookmark(i) for i in range(1, 5)]
    skipping_route(monkeypatch, {"3", "4"})
    assert run.main([]) == 0
    where = fake.home / "Unsorted"  # beside the one topic folder (home/vault)
    assert set(unsorted.notes_by_id(where)) == {"3", "4"}
    assert "| skip |" in ledger(fake)  # the ledger still says what Jev decided
    last = report.history()[-1]
    assert last["unsorted"] == 2 and "ai" in last["written"]  # (the fake note writer writes every bookmark)
    status = (fake.home / "scatha.md").read_text(encoding="utf-8")
    assert "## Unsorted: 2" in status and "review`" in status
    assert "2 didn't fit your topics; they're in Unsorted" in fake.pings[-1] and "for the full report" in fake.pings[-1]


def test_run_with_unsorted_off_keeps_no_notes(fake, monkeypatch):
    cfg = app.load_config()
    cfg["unsorted"] = {"mode": "off", "folder": ""}
    app.save_config(cfg)
    fake.prepared = [bookmark(1), bookmark(2)]
    skipping_route(monkeypatch, {"2"})
    assert run.main([]) == 0
    assert not (fake.home / "Unsorted").exists()
    assert "1 didn't fit your topics; it wasn't kept" in fake.pings[-1]


def test_preview_records_nothing(fake, monkeypatch):
    fake.prepared = [bookmark(1)]
    skipping_route(monkeypatch, {"1"})
    run.main(["--preview"])
    assert report.history() == [] and not (fake.home / "scatha.md").exists()
    assert unsorted.notes_by_id(app.work() / "notes-preview" / "Unsorted")


# ---------- report, banner, levers ----------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path))
    tax_at(tmp_path)
    app.save_config({"taxonomy": "topics.yaml"})
    return tmp_path


def test_banner_shows_until_the_user_looks(home):
    assert report.banner() is None
    report.record_run({"ai": 3}, 2, 1, 50, "light")
    line = report.banner()
    assert "3 new notes, 2 went to Unsorted, 1 failed and will retry" in line
    report.mark_seen()
    assert report.banner() is None


def test_levers_only_when_they_apply(home):
    s = report.state(app.load_config())
    assert report.levers(s) == []  # no runs, nothing unsorted, nothing queued: say nothing
    s.update(unsorted=12, remaining=40, failed=0, runs=[{}])
    keys = [l.key for l in report.LEVERS if l.applies(s)]
    assert keys == ["sort", "topic", "next"]
    assert len(report.levers(s, limit=2)) == 2


def test_status_note_lists_newest_unsorted(home):
    cfg = app.load_config()
    tax = Taxonomy.load(home / "topics.yaml")
    unsorted.write([(dict(B, id=str(i), tweetUrl=f"https://x.com/p/status/{i}", text=f"post number {i}"), {"ai": 0.1})
                    for i in range(12)], unsorted.folder(cfg, tax), tax)
    report.record_run({}, 12, 0, None, "light")
    path = report.write_note(cfg)
    text = path.read_text(encoding="utf-8")
    assert text.count("- [[") == 10 and "and 2 more" in text and "Edits here are replaced" in text


def test_cli_bare_is_the_report_and_counts_as_looking(home, capsys):
    report.record_run({"ai": 1}, 0, 0, None, "light")
    assert cli.main([]) == 0
    assert "Last run" in capsys.readouterr().out and report.banner() is None
    assert cli.main(["nonsense"]) == 2


def test_cli_before_setup_points_at_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path / "nothing"))
    assert cli.main([]) == 1 and "setup" in capsys.readouterr().out


def test_command_falls_back_to_the_long_form(monkeypatch, tmp_path):
    monkeypatch.setattr(app.shutil, "which", lambda name: None)
    monkeypatch.setattr(app.Path, "home", lambda: tmp_path)
    assert app.command("review").startswith("uv run --project") and app.command("review").endswith("scatha review")
    monkeypatch.setattr(app.shutil, "which", lambda name: "/bin/scatha")
    assert app.command("review") == "scatha review" and app.command() == "scatha"


# ---------- sorting out of Unsorted ----------

def test_finish_sorting_writes_full_notes_removes_light_and_fixes_ledger(home, monkeypatch):
    cfg = app.load_config()
    tax = Taxonomy.load(home / "topics.yaml")
    work = app.work()
    (work / "runs" / "r").mkdir(parents=True)
    bms = [dict(B, id=i, tweetUrl=f"https://x.com/p/status/{i}", text=f"post {i}") for i in ("1", "2", "3")]
    (work / "runs" / "r" / "pending-bookmarks.json").write_text(json.dumps({"bookmarks": bms}), encoding="utf-8")
    (work / "ingested.md").write_text("# l\n\n" + "".join(f"- https://x.com/p/status/{i} | 2026-10-10 | skip | ai\n" for i in "123"), encoding="utf-8")
    where = unsorted.folder(cfg, tax)
    unsorted.write([(b, {"ai": 0.1}) for b in bms], where, tax)
    store = desk.Verdicts(review.verdicts_path())
    store.put("1", {"verdict": "journalism"})
    store.put("2", {"verdict": "drop"})
    calls = []

    def write(args):
        routes = json.loads(open(args.routes, encoding="utf-8").read())
        calls.append(routes)
        return {"written": [(r["item_id"], r["primary"], "p") for r in routes], "failed": [], "present": [], "tokens": 0}

    monkeypatch.setattr(notes, "cmd_write", write)
    monkeypatch.setattr(app, "load_secrets", lambda: None)
    cfg["tools"] = {"claude": "claude"}
    cfg["notes_backend"] = "claude-code"

    assert review.finish_sorting(cfg, say=lambda *a: None, ask=lambda q: False) == 0
    assert set(unsorted.notes_by_id(where)) == {"1", "2", "3"} and not calls  # nothing without a yes

    assert review.finish_sorting(cfg, say=lambda *a: None, ask=lambda q: True) == 1
    assert calls[0][0]["primary"] == "journalism" and calls[0][0]["status"] == "route"
    assert set(unsorted.notes_by_id(where)) == {"3"}
    led = (work / "ingested.md").read_text(encoding="utf-8")
    assert "status/1 | 2026-10-10 | route | journalism" in led and "status/2 | 2026-10-10 | skip" in led
    assert review.finish_sorting(cfg, say=lambda *a: None, ask=lambda q: True) == 0  # done is done
