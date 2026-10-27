"""One ingest run: new X bookmarks -> Obsidian notes. This is what the schedule calls.

    uv run --project router python -m jev_router.run [--batch N] [--preview] [--force]

Steps: check that X, Jev, and the note writer all answer, fetch the newest unprocessed bookmarks (Smaug), read
image posts, route with Jev, write notes, record every processed bookmark in the
ledger, ping a summary. A bookmark whose note failed stays out of the ledger, so
the next run picks it up again. All paths come from the data folder (see app.py).
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import urllib.request

from . import Taxonomy, app, checks, notes, report, unsorted
from .smaug import route_file

# Minimum days between runs per cadence. Schedulers that can't express "every two
# weeks" fire weekly and this guard skips the off weeks.
MIN_DAYS = {"daily": 0, "weekly": 0, "biweekly": 13}


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def ping(cfg: dict, text: str) -> None:
    log(f"summary: {text}")
    if not cfg.get("ntfy_topic"):
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{cfg['ntfy_topic']}", data=text[:280].encode("utf-8"),
                                     headers={"Title": "Bookmarks"})
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        log(f"ntfy failed: {e}")


def sh(cmd: list[str], cwd, env=None) -> tuple[int, str]:
    log("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    print(out, flush=True)
    return p.returncode, out


def smaug_env(cfg: dict) -> dict:
    w = app.work()
    return os.environ | {
        "ARCHIVE_FILE": str(w / "ingested.md"),
        "PENDING_FILE": str(w / "pending-bookmarks.json"),
        "STATE_FILE": str(w / "bookmarks-state.json"),
        "BIRD_PATH": cfg["tools"]["bird"],
        "INCLUDE_MEDIA": "true",
        "AUTO_INVOKE_CLAUDE": "false",
        "AUTO_INVOKE_OPENCODE": "false",
    }


def new_run_dir(w: Path, stamp: str) -> Path:
    """A run folder no other run has: two runs in the same minute must not share (and overwrite) one."""
    (w / "runs").mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        d = w / "runs" / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise FileExistsError(f"no free run folder for {stamp}")


LOCK_STALE = dt.timedelta(hours=6)
UNSORTED = ("skip", "review")  # routing statuses that end in Unsorted rather than a topic folder


def take_lock(lock) -> str | None:
    """This run's token if it may go ahead, None if another run holds the lock.

    Creating the file is the test: the OS lets exactly one O_EXCL create succeed, so runs that
    start in the same instant can't both get in. A lock older than LOCK_STALE belongs to a run
    that died; it is removed and the create tried once more."""
    token = f"{os.getpid()} {dt.datetime.now().isoformat()}"
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = dt.datetime.now() - dt.datetime.fromtimestamp(lock.stat().st_mtime)
            except FileNotFoundError:
                continue  # its holder just finished
            if age < LOCK_STALE:
                return None
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        return token
    return None


def release_lock(lock, token: str) -> None:
    """Remove the lock only if it is still this run's."""
    try:
        if lock.read_text(encoding="utf-8") == token:
            lock.unlink()
    except FileNotFoundError:
        pass


def append_ledger(lines: list[str]) -> None:
    ledger = app.work() / "ingested.md"
    if not ledger.exists():
        ledger.write_text(f"# {app.APP_NAME} ledger: one line per processed bookmark (dedupe reads these URLs)\n\n",
                          encoding="utf-8")
    with ledger.open("a", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))


def summary_text(written: list, skipped: int, failed: int, remaining: int | None, labels: dict,
                 mode: str = "light", new_unsorted: int = 0) -> str:
    counts = collections.Counter(b for _, b, _ in written)
    parts = ", ".join(f"{n} {labels.get(b, b)}" for b, n in counts.most_common())
    text = f"Bookmark import: {len(written)} new notes ({parts or 'none'})."
    if skipped:
        if mode != "off":
            where = "they're in Unsorted" if skipped > 1 else "it's in Unsorted"
        else:
            where = "they weren't kept" if skipped > 1 else "it wasn't kept"
        text += f" {skipped} didn't fit your topics; {where}."
    if failed:
        text += f" {failed} failed and will retry next run."
    if remaining:
        text += f" {remaining} older bookmarks still queued."
    if new_unsorted >= report.DISCOVER_AT:
        text += (f" {new_unsorted} bookmarks have gone to Unsorted since your topics were last checked; "
                 f"`{app.command('discover')}` looks for a topic they share.")
    if skipped or failed:
        text += f" Type `{app.command()}` for the full report and what you can do."
    return text


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, help="override batch_size for this run")
    ap.add_argument("--preview", action="store_true",
                    help="write notes to <data>/work/notes-preview instead of your folders; nothing is recorded")
    ap.add_argument("--force", action="store_true", help="ignore the minimum-days guard")
    args = ap.parse_args(argv)

    cfg = app.load_config()
    app.load_secrets()
    os.environ["CLAUDE_BIN"] = cfg["tools"]["claude"]
    os.environ["NOTES_BACKEND"] = cfg["notes_backend"]
    os.environ["READER_PROFILE"] = cfg.get("reader_profile", "")
    batch = args.batch or cfg["batch_size"]
    w = app.work()
    w.mkdir(parents=True, exist_ok=True)
    pending, lock, last_ok = w / "pending-bookmarks.json", w / ".run.lock", w / ".last-success"
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")

    min_days = MIN_DAYS.get(cfg["schedule"].get("cadence", "weekly"), 0)
    if min_days and not args.force and last_ok.exists():
        since = dt.datetime.now() - dt.datetime.fromtimestamp(last_ok.stat().st_mtime)
        if since < dt.timedelta(days=min_days):
            log(f"last run was {since.days} days ago; cadence wants {min_days}+. Skipping.")
            return 0

    def stopped(stage: str, problems: list[str]) -> None:
        """A run that ends before importing anything still reaches the report and the status note."""
        if args.preview:
            return
        report.record_stop(stage, problems)
        try:
            note = report.write_note(cfg)
        except Exception as e:  # a broken topics file, say: `scatha` still shows the stop
            note = None
            log(f"status note: {type(e).__name__}: {e}")
        log(f"status note: {note}" if note else "status note: could not write it")

    token = take_lock(lock)
    if token is None:
        log("another run holds the lock; exiting")
        return 0
    try:
        tax_path = app.taxonomy_path(cfg)
        tax = Taxonomy.load(tax_path)

        # 1. X login, Jev, and the note writer all answering? Stop before touching X if not.
        log("checking X login, Jev, and the note writer")
        problems = checks.preflight(cfg)
        if problems:
            for p in problems:
                log(f"pre-flight: {p}")
            ping(cfg, f"Bookmark import stopped before fetching: {'; '.join(problems)}. To fix it, run "
                       f"{app.setup_command()} in {app.REPO}; setup finds what's broken and asks only about that. "
                       "Nothing was lost: the next run picks up where this one would have.")
            stopped("preflight", problems)
            return 2

        # 2. Fetch, or resume a run that stopped after fetching
        remaining = None
        if pending.exists() and json.loads(pending.read_text(encoding="utf-8")).get("bookmarks"):
            log("resuming: pending bookmarks from an earlier run")
        else:
            code, out = sh([cfg["tools"]["node"], "src/cli.js", "fetch", "1000", "--limit", str(batch)],
                           app.REPO / "smaug", env=smaug_env(cfg))
            if code != 0:
                ping(cfg, "Bookmark import failed while fetching from X. The newest log in the data folder has details.")
                stopped("fetch", ["Fetching your bookmarks from X failed."])
                return 3
            m = re.search(r"Limiting to \d+ of (\d+) unprocessed", out)
            remaining = int(m.group(1)) - batch if m else 0
        batch_file = json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else {}
        # Bookmarks Smaug couldn't read stay out of the ledger, so the next fetch brings them back
        unread = batch_file.get("prepareFailed") or []
        for f in unread:
            log(f"could not read bookmark {f.get('id')}: {f.get('error')}")
        if not batch_file.get("bookmarks"):
            if unread:
                shutil.move(str(pending), new_run_dir(w, stamp) / "pending-bookmarks.json")
                ping(cfg, f"Bookmark import: {len(unread)} new bookmarks couldn't be read and will retry next run. "
                          "The newest log in the data folder has details.")
                stopped("read", [f"None of the {len(unread)} new bookmarks could be read from X."])
                return 1
            ping(cfg, "Bookmark import: no new bookmarks this time.")
            last_ok.touch()
            if not args.preview:
                report.clear_stop()
            return 0
        bookmarks = {str(b["id"]): b for b in batch_file["bookmarks"]}
        log(f"{len(bookmarks)} bookmarks to process" + (f" ({len(unread)} couldn't be read)" if unread else ""))

        # 3. Images -> text, 4. route, 5. notes
        run_dir = new_run_dir(w, stamp)
        unseen = notes.cmd_describe(argparse.Namespace(pending=str(pending))) or []
        if unseen:
            # A post that's mostly images, filed without them, would be filed on almost nothing and never
            # read again. Held back instead: out of this run and out of the ledger, so the next fetch retries it.
            held = {i for i, _ in unseen}
            data = json.loads(pending.read_text(encoding="utf-8"))
            data["bookmarks"] = [b for b in data["bookmarks"] if str(b["id"]) not in held]
            data["imagesFailed"] = [{"id": i, "error": e} for i, e in unseen]
            pending.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            log(f"{len(held)} held back for the next run: reading their images failed")
        # Again from the file: it now holds the text read from the images (light notes show it too)
        bookmarks = {str(b["id"]): b for b in json.loads(pending.read_text(encoding="utf-8"))["bookmarks"]}
        if not bookmarks:  # every one was held back
            shutil.move(str(pending), run_dir / "pending-bookmarks.json")
            ping(cfg, f"Bookmark import: reading the images of {len(unseen)} bookmarks failed. "
                      "They will retry next run. The newest log in the data folder has details.")
            stopped("read", [f"Reading the images of all {len(unseen)} new bookmarks failed."])
            return 1
        results = asyncio.run(route_file(pending, tax_path, run_dir))
        summary = notes.cmd_write(argparse.Namespace(
            pending=str(pending), routes=str(run_dir / "routes.json"), taxonomy=str(tax_path),
            out=str(w / "notes-preview"), ids=None, to_vault=not args.preview))

        # 5b. Bookmarks that fit no topic: light notes in Unsorted (no model cost), unless turned off.
        # "review" (between the two thresholds, when someone sets them apart) has no queue of its own,
        # so it goes to Unsorted too and is ledgered as a skip: the desk and discovery see it there.
        skips = [r for r in results if r.status in UNSORTED]
        if skips and unsorted.mode(cfg) != "off":
            where = unsorted.folder(cfg, tax)
            if args.preview:
                where = w / "notes-preview" / where.name
            made = unsorted.write([(bookmarks[r.item_id], r.topics) for r in skips], where, tax)
            log(f"{len(made)} light notes in {where} for bookmarks that fit no topic")

        # 6. Ledger: everything handled. Failed notes and routing errors stay out and come back next run.
        if not args.preview:
            done = {i for i, _, _ in summary["written"]} | set(summary["present"])
            today = dt.date.today().isoformat()
            append_ledger([f"- {bookmarks[r.item_id]['tweetUrl']} | {today} | {'skip' if r.status in UNSORTED else r.status} | {r.primary}"
                           for r in results
                           if r.status in UNSORTED or (r.status == "route" and r.item_id in done)])
            last_ok.touch()
        shutil.move(str(pending), run_dir / "pending-bookmarks.json")

        # 7. Summary
        labels = {t.key: t.label or t.key.replace("_", " ") for t in tax.topics}
        failed = len(summary["failed"]) + sum(r.status == "error" for r in results) + len(unread) + len(unseen)
        if not args.preview:
            report.record_run(dict(collections.Counter(b for _, b, _ in summary["written"])), len(skips),
                              failed, remaining, unsorted.mode(cfg))
            gone = unsorted.check(cfg, tax, say=log)  # light notes deleted since the last look; no one to ask at night
            if gone["held"]:
                log(f"{gone['held']} Unsorted notes disappeared at once; waiting for a yes at `{app.command()}`")
            note = report.write_note(cfg)
            log(f"status note: {note}" if note else "status note: could not write it")
        ping(cfg, summary_text(summary["written"], len(skips), failed, remaining, labels, unsorted.mode(cfg),
                               0 if args.preview else report.new_unsorted()))
        log(f"note-writer tokens this run: {summary['tokens']:,}")
        return 1 if failed else 0
    except Exception as e:
        try:
            stopped("crash", [f"The run hit an unexpected error: {type(e).__name__}: {str(e)[:200]}"])
        except Exception:
            pass  # the original error matters more
        raise
    finally:
        release_lock(lock, token)


if __name__ == "__main__":
    sys.exit(main())
