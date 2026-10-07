"""One ingest run: new X bookmarks -> Obsidian notes. This is what the schedule calls.

    uv run --project router python -m jev_router.run [--batch N] [--preview] [--force]

Steps: check the X login, fetch the newest unprocessed bookmarks (Smaug), read
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
import urllib.request

from . import Taxonomy, app, backup, notes
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


def append_ledger(lines: list[str]) -> None:
    ledger = app.work() / "ingested.md"
    if not ledger.exists():
        ledger.write_text(f"# {app.APP_NAME} ledger: one line per processed bookmark (dedupe reads these URLs)\n\n",
                          encoding="utf-8")
    with ledger.open("a", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))


def summary_text(written: list, skipped: int, failed: int, remaining: int | None, labels: dict) -> str:
    counts = collections.Counter(b for _, b, _ in written)
    parts = ", ".join(f"{n} {labels.get(b, b)}" for b, n in counts.most_common())
    text = f"Bookmark import: {len(written)} new notes ({parts or 'none'})."
    if skipped:
        text += f" {skipped} didn't fit your topics."
    if failed:
        text += f" {failed} failed and will retry next run."
    if remaining:
        text += f" {remaining} older bookmarks still queued."
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
    run_dir = w / "runs" / dt.datetime.now().strftime("%Y-%m-%d_%H%M")

    min_days = MIN_DAYS.get(cfg["schedule"].get("cadence", "weekly"), 0)
    if min_days and not args.force and last_ok.exists():
        since = dt.datetime.now() - dt.datetime.fromtimestamp(last_ok.stat().st_mtime)
        if since < dt.timedelta(days=min_days):
            log(f"last run was {since.days} days ago; cadence wants {min_days}+. Skipping.")
            return 0

    if lock.exists() and dt.datetime.now() - dt.datetime.fromtimestamp(lock.stat().st_mtime) < dt.timedelta(hours=6):
        log("another run holds the lock; exiting")
        return 0
    lock.write_text(f"{os.getpid()} {dt.datetime.now().isoformat()}", encoding="utf-8")
    try:
        tax_path = app.taxonomy_path(cfg)
        tax = Taxonomy.load(tax_path)

        # 1. X login still good?
        code, out = sh([cfg["tools"]["node"], "src/cli.js", "whoami"], app.REPO / "smaug", env=smaug_env(cfg))
        if code != 0 or "user:" not in out:
            ping(cfg, "Bookmark import stopped: your X login has expired. Run the setup's 'x-login' step "
                      "with fresh cookies and it will pick up next run.")
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
                return 3
            m = re.search(r"Limiting to \d+ of (\d+) unprocessed", out)
            remaining = int(m.group(1)) - batch if m else 0
        if not pending.exists() or not json.loads(pending.read_text(encoding="utf-8")).get("bookmarks"):
            ping(cfg, "Bookmark import: no new bookmarks this time.")
            last_ok.touch()
            return 0
        bookmarks = {str(b["id"]): b for b in json.loads(pending.read_text(encoding="utf-8"))["bookmarks"]}
        log(f"{len(bookmarks)} bookmarks to process")

        # 3. Images -> text, 4. route, 5. notes
        notes.cmd_describe(argparse.Namespace(pending=str(pending)))
        results = asyncio.run(route_file(pending, tax_path, run_dir))
        summary = notes.cmd_write(argparse.Namespace(
            pending=str(pending), routes=str(run_dir / "routes.json"), taxonomy=str(tax_path),
            out=str(w / "notes-preview"), ids=None, to_vault=not args.preview))

        # 6. Ledger: everything handled. Failed notes and routing errors stay out and come back next run.
        if not args.preview:
            done = {i for i, _, _ in summary["written"]} | set(summary["present"])
            today = dt.date.today().isoformat()
            append_ledger([f"- {bookmarks[r.item_id]['tweetUrl']} | {today} | {r.status} | {r.primary}"
                           for r in results
                           if r.status == "skip" or (r.status == "route" and r.item_id in done)])
            last_ok.touch()
        shutil.move(str(pending), run_dir / "pending-bookmarks.json")

        # 7. Summary
        labels = {t.key: t.label or t.key.replace("_", " ") for t in tax.topics}
        failed = len(summary["failed"]) + sum(r.status == "error" for r in results)
        ping(cfg, summary_text(summary["written"], sum(r.status == "skip" for r in results), failed, remaining, labels))
        log(f"note-writer tokens this run: {summary['tokens']:,}")
        return 1 if failed else 0
    finally:
        lock.unlink(missing_ok=True)
        if not args.preview:
            try:
                zipped = backup.snapshot(cfg)
                if zipped:
                    log(f"backed up the data folder to {zipped}")
            except Exception as e:  # a backup problem must not turn a good run into a failed one
                log(f"WARNING: backup failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
