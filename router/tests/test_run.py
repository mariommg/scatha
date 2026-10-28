"""The runner end to end with every outside service faked: X (Smaug), Jev, the note writer.

What these pin: a bookmark that fails anywhere stays out of the ledger (so the next
fetch brings it back) and shows up in the run's summary and exit code; a run where
nothing could be read is a failure, not "no new bookmarks".
"""

import collections
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
    f = SimpleNamespace(prepared=[], failed_prep=[], note_fails=set(), pings=[], home=tmp_path,
                        vault=collections.Counter(), fetches=0)  # vault: note id -> times written

    def smaug(cmd, cwd, env=None):  # what `node src/cli.js fetch` leaves behind
        f.fetches += 1
        done = ledger(f)  # like Smaug, skip anything the ledger already lists
        out = {"bookmarks": [b for b in f.prepared if b["tweetUrl"] + " " not in done],
               "prepareFailed": [{"id": i, "error": "boom"} for i in f.failed_prep]}
        (app.work() / "pending-bookmarks.json").write_text(json.dumps(out), encoding="utf-8")
        return 0, ""

    async def route(pending, tax, run_dir):
        run_dir.mkdir(parents=True, exist_ok=True)
        ids = [b["id"] for b in json.loads(pending.read_text(encoding="utf-8"))["bookmarks"]]
        return [SimpleNamespace(item_id=i, status="route", primary="ai") for i in ids]

    def write(args):  # like notes.cmd_write: a note already in the vault is "present", not written again
        ids = [b["id"] for b in json.loads(open(args.pending, encoding="utf-8").read())["bookmarks"]]
        present = [i for i in ids if f.vault[i]]
        new = [i for i in ids if not f.vault[i] and i not in f.note_fails]
        f.vault.update(new)
        return {"written": [(i, "ai", f"{i}.md") for i in new], "present": present,
                "failed": [i for i in ids if i in f.note_fails], "tokens": 0}

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


# ----- the run lock: one run at a time -----

RACER = """
import pathlib, sys, time
from jev_router.run import take_lock
lock, start, me = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
(start.parent / f"ready-{me}").touch()
while not start.exists():
    pass
print("won" if take_lock(lock) else "lost", flush=True)
time.sleep(1)  # hold it while the others try
"""


def test_only_one_of_many_simultaneous_runs_gets_the_lock(tmp_path):
    import subprocess, sys, time
    lock, start, n = tmp_path / ".run.lock", tmp_path / "start", 12
    procs = [subprocess.Popen([sys.executable, "-c", RACER, str(lock), str(start), str(i)],
                              stdout=subprocess.PIPE, text=True) for i in range(n)]
    deadline = time.time() + 60
    while len(list(tmp_path.glob("ready-*"))) < n and time.time() < deadline:
        time.sleep(0.05)
    start.touch()  # everyone goes at once
    results = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert results.count("won") == 1, results


def test_a_run_never_removes_another_runs_lock(tmp_path):
    lock = tmp_path / ".run.lock"
    mine = run.take_lock(lock)
    run.release_lock(lock, "someone else's token")
    assert lock.exists() and run.take_lock(lock) is None
    run.release_lock(lock, mine)
    assert not lock.exists()


def test_a_stale_lock_is_taken_over(tmp_path):
    import os, time
    lock = tmp_path / ".run.lock"
    lock.write_text("12345 long gone", encoding="utf-8")
    old = time.time() - 7 * 3600
    os.utime(lock, (old, old))
    assert run.take_lock(lock) is not None


def test_a_locked_run_exits_without_fetching(fake):
    (fake.home / "work").mkdir(exist_ok=True)
    held = run.take_lock(fake.home / "work" / ".run.lock")
    fake.prepared = [bookmark(1)]
    assert run.main([]) == 0
    assert ledger(fake) == "" and not fake.pings  # nothing fetched, nothing reported
    assert (fake.home / "work" / ".run.lock").read_text(encoding="utf-8") == held  # and the holder's lock survives


# ----- a run that dies halfway: the next one finishes the job, no duplicates, nothing lost -----

def crash_once(monkeypatch, target, name):
    real, state = getattr(target, name), {"crashed": False}

    def once(*a, **k):
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("power cut")
        return real(*a, **k)
    monkeypatch.setattr(target, name, once)


def test_a_run_that_dies_after_fetching_is_finished_by_the_next(fake, monkeypatch):
    fake.prepared = [bookmark(1), bookmark(2)]
    crash_once(monkeypatch, run, "route_file")
    with pytest.raises(RuntimeError):
        run.main([])
    assert not (fake.home / "work" / ".run.lock").exists()  # the lock doesn't outlive the crash
    fake.prepared = []  # X has nothing newer; the batch must come from the saved pending file
    assert run.main([]) == 0
    assert fake.fetches == 1  # resumed, not refetched
    assert ledger(fake).count("status/1 ") == 1 and ledger(fake).count("status/2 ") == 1


def test_a_run_that_dies_after_writing_notes_does_not_write_them_twice(fake, monkeypatch):
    fake.prepared = [bookmark(1), bookmark(2)]
    crash_once(monkeypatch, run, "append_ledger")  # notes are in the vault; the ledger never heard
    with pytest.raises(RuntimeError):
        run.main([])
    assert ledger(fake) == ""
    assert run.main([]) == 0
    assert dict(fake.vault) == {"1": 1, "2": 1}  # each note written once
    assert ledger(fake).count("status/1 ") == 1 and ledger(fake).count("status/2 ") == 1
    assert run.main([]) == 0 and fake.fetches == 2  # a third run fetches fresh and finds nothing new
    assert ledger(fake).count("status/1 ") == 1
