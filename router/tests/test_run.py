"""The runner end to end with every outside service faked: X (Smaug), Jev, the note writer.

What these pin: a bookmark that fails anywhere stays out of the ledger (so the next
fetch brings it back) and shows up in the run's summary and exit code; a run where
nothing could be read is a failure, not "no new bookmarks".
"""

import json
from types import SimpleNamespace

import pytest

from jev_router import app, checks, notes, run, topics


def bookmark(i):
    return {"id": str(i), "tweetUrl": f"https://x.com/someone/status/{i}", "text": f"post {i}"}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A data folder plus fakes. Set .prepared / .failed_prep before calling run.main()."""
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path))
    app.save_config({"tools": {"node": "node", "bird": "bird", "claude": "claude"}})
    topics.write_taxonomy([{"label": "AI", **{k: topics.PRESETS["AI"][k] for k in ("question", "yes_means", "no_means")},
                            "destination": str(tmp_path / "vault")}], tmp_path / "topics.yaml")
    f = SimpleNamespace(prepared=[], failed_prep=[], note_fails=set(), pings=[], home=tmp_path)

    def smaug(cmd, cwd, env=None):  # what `node src/cli.js fetch` leaves behind
        out = {"bookmarks": f.prepared, "prepareFailed": [{"id": i, "error": "boom"} for i in f.failed_prep]}
        (app.work() / "pending-bookmarks.json").write_text(json.dumps(out), encoding="utf-8")
        return 0, ""

    async def route(pending, tax, run_dir):
        run_dir.mkdir(parents=True, exist_ok=True)
        ids = [b["id"] for b in json.loads(pending.read_text(encoding="utf-8"))["bookmarks"]]
        return [SimpleNamespace(item_id=i, status="route", primary="ai") for i in ids]

    def write(args):
        ids = [b["id"] for b in json.loads(open(args.pending, encoding="utf-8").read())["bookmarks"]]
        return {"written": [(i, "ai", f"{i}.md") for i in ids if i not in f.note_fails],
                "present": [], "failed": [i for i in ids if i in f.note_fails], "tokens": 0}

    monkeypatch.setattr(app, "load_secrets", lambda: None)
    monkeypatch.setattr(checks, "preflight", lambda cfg: [])
    monkeypatch.setattr(run, "sh", smaug)
    monkeypatch.setattr(run, "route_file", route)
    monkeypatch.setattr(notes, "cmd_describe", lambda args: None)
    monkeypatch.setattr(notes, "cmd_write", write)
    monkeypatch.setattr(run, "ping", lambda cfg, text: f.pings.append(text))
    return f


def ledger(f):
    p = f.home / "work" / "ingested.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_clean_run_records_everything(fake):
    fake.prepared = [bookmark(1), bookmark(2)]
    assert run.main([]) == 0
    assert "status/1 " in ledger(fake) and "status/2 " in ledger(fake)
    assert (fake.home / "work" / ".last-success").exists()
    assert "failed" not in fake.pings[-1]


def test_nothing_new_is_a_clean_no_op(fake):
    assert run.main([]) == 0
    assert "no new bookmarks" in fake.pings[-1]
    assert (fake.home / "work" / ".last-success").exists()


def test_bookmarks_that_could_not_be_read_are_reported_and_retried(fake):
    fake.prepared, fake.failed_prep = [bookmark(1)], ["2", "3"]
    assert run.main([]) == 1
    assert "status/1 " in ledger(fake)
    assert "status/2" not in ledger(fake) and "status/3" not in ledger(fake)  # so the next fetch brings them back
    assert "2 failed" in fake.pings[-1]


def test_a_run_where_nothing_could_be_read_is_a_failure(fake):
    fake.failed_prep = ["2", "3"]
    assert run.main([]) == 1
    assert "no new bookmarks" not in fake.pings[-1]
    assert "2" in fake.pings[-1]
    assert not (fake.home / "work" / ".last-success").exists()  # a biweekly cadence must not skip the retry
    assert ledger(fake) == ""
    assert not (fake.home / "work" / "pending-bookmarks.json").exists()  # next run fetches afresh


def test_failed_notes_stay_out_of_the_ledger(fake):
    fake.prepared, fake.note_fails = [bookmark(1), bookmark(2)], {"2"}
    assert run.main([]) == 1
    assert "status/1 " in ledger(fake) and "status/2" not in ledger(fake)
    assert "1 failed" in fake.pings[-1]
