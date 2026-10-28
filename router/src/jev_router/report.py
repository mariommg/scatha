"""What scatha did, said everywhere the user looks, always with what they can do about it.

A run that happens at midnight reports nothing anyone sees, so the same facts go to
several places: a status note in the vault (rewritten every run), a one-line banner
on any `scatha` command ("since you last looked..."), the full report (bare `scatha`),
and the run summary. Each carries the levers that apply right now, from one list,
and says nothing extra when there's nothing to do.

    work/runs.jsonl   one line per real run: when, notes by topic, unsorted, failed, queued
    work/.seen        when the user last looked (bare `scatha` or `scatha review`)
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import Taxonomy, app, unsorted


# ---------- run history ----------

def history_path() -> Path:
    return app.work() / "runs.jsonl"


def record_run(written: dict[str, int], n_unsorted: int, failed: int, remaining: int | None, mode: str) -> dict:
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "written": written, "unsorted": n_unsorted,
             "failed": failed, "remaining": remaining, "unsorted_mode": mode}
    history_path().parent.mkdir(parents=True, exist_ok=True)
    with history_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    clear_stop()
    return entry


# ---------- a run that stopped before it imported anything ----------
# Kept apart from the history: it has no counts to show, and the last run that did import stays
# the one the report counts from. The next run that gets through clears it.

def _stop_path() -> Path:
    return app.work() / "last-stop.json"


def record_stop(stage: str, problems: list[str]) -> None:
    """stage: "preflight", "fetch", "read" (nothing in the batch could be read), or "crash"."""
    _stop_path().parent.mkdir(parents=True, exist_ok=True)
    _stop_path().write_text(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"),
                                        "stage": stage, "problems": problems}), encoding="utf-8")


def clear_stop() -> None:
    _stop_path().unlink(missing_ok=True)


def last_stop() -> dict | None:
    try:
        return json.loads(_stop_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stop_steps(stop: dict) -> list[str]:
    """What to try, two or three steps, most likely fix first."""
    logs = f"Read the newest log in {app.work() / 'logs'}. It shows each step the run took."
    retry = f"`{app.command('run')}`: try the import again now. (Or wait: the schedule will try again.)"
    setup = f"`{app.command('setup')}`: check every part. It will ask only about what's broken."
    if stop["stage"] == "preflight":
        x = any("X login" in p for p in stop["problems"])
        first = f"`{app.command('setup x-login')}`: log in to X again." if x else setup
        return [first, retry, logs]
    if stop["stage"] == "fetch":
        return [f"`{app.command('run')}`: try again. (X sometimes refuses requests for a while. Waiting an hour can help.)",
                setup, logs]
    if stop["stage"] == "read":
        return [f"You don't need to do anything now: {app.DISPLAY_NAME} will try those bookmarks again next run.",
                f"If it happens again, `{app.command('setup')}` will check every part.", logs]
    return [retry, logs, setup]


def stop_lines(stop: dict) -> list[str]:
    head = f"The last run stopped ({_when(stop['at'])}) before it imported anything:"
    return [head] + [f"  - {p[:1].upper() + p[1:]}" for p in stop["problems"]] + ["", "What to try:"] + [
        f"  {n}. {s}" for n, s in enumerate(stop_steps(stop), 1)]


def history() -> list[dict]:
    if not history_path().exists():
        return []
    out = []
    for line in history_path().read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


# ---------- "since you last looked" ----------

def _seen_path() -> Path:
    return app.work() / ".seen"


def last_seen() -> str:
    return _seen_path().read_text(encoding="utf-8").strip() if _seen_path().exists() else ""


def mark_seen() -> None:
    _seen_path().parent.mkdir(parents=True, exist_ok=True)
    _seen_path().write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def unseen(runs: list[dict] | None = None) -> dict:
    since = last_seen()
    new = [r for r in (history() if runs is None else runs) if r["at"] > since]
    return {"runs": len(new), "notes": sum(sum(r["written"].values()) for r in new),
            "unsorted": sum(r["unsorted"] for r in new), "failed": new[-1]["failed"] if new else 0}


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _retry(n: int) -> str:
    return f"{n} failed ({'it' if n == 1 else 'they'} will try again next run)"


def terminal(text: str) -> str:
    """Markdown bold (button names) as terminal bold, or plain when color is off."""
    import re
    from .onboard import enable_color
    return re.sub(r"\*\*(.+?)\*\*", r"\033[1m\1\033[0m" if enable_color() else r"\1", text)


def banner() -> str | None:
    stop = last_stop()
    if stop and stop["at"] >= last_seen():  # same second: better shown twice than missed
        return f"The last run stopped before it imported anything. Type `{app.command()}` for what to try."
    u = unseen()
    if not u["runs"] or not (u["notes"] or u["unsorted"] or u["failed"]):
        return None
    bits = [f"{_n(u['notes'], 'new note')}."]
    if u["unsorted"]:
        bits.append(f"{u['unsorted']} went to Unsorted.")
    if u["failed"]:
        bits.append(_retry(u["failed"]) + ".")
    return f"Since you last looked: {' '.join(bits)} Type `{app.command()}` for the details."


# ---------- state and levers ----------

def state(cfg: dict) -> dict:
    tax = Taxonomy.load(app.taxonomy_path(cfg))
    runs = history()
    last = runs[-1] if runs else None
    return {
        "tax": tax, "labels": {t.key: t.label or t.key for t in tax.topics}, "last": last, "runs": runs,
        "unsorted": unsorted.count(cfg, tax) if unsorted.mode(cfg) != "off" else 0,
        "unsorted_dir": unsorted.folder(cfg, tax), "mode": unsorted.mode(cfg),
        "failed": last["failed"] if last else 0, "remaining": last["remaining"] if last else None,
        "held": unsorted.held_count(), "not_kept": not_kept(), "new_unsorted": new_unsorted(runs), "stop": last_stop(),
    }


DISCOVER_AT = 15  # new Unsorted bookmarks since the last discovery run before discovery is suggested


def new_unsorted(runs: list[dict] | None = None) -> int:
    """Bookmarks sent to Unsorted since the last discovery run (all of them, if discovery never ran)."""
    from . import discover
    since = ""
    if discover.runs_dir().is_dir():
        for f in discover.runs_dir().glob("*.json"):
            try:
                since = max(since, json.loads(f.read_text(encoding="utf-8")).get("at") or "")
            except (OSError, ValueError):
                continue
    return sum(r.get("unsorted", 0) for r in (history() if runs is None else runs) if r.get("at", "") > since)


def not_kept() -> dict[str, int]:
    """Bookmarks judged not worth keeping, by where the user said so: {"vault": n, "desk": m}."""
    from . import desk, review
    out = {"vault": 0, "desk": 0}
    for v in desk.Verdicts(review.verdicts_path()).load().values():
        if v.get("verdict") == desk.DROP:
            out["vault" if v.get("source") == "vault" else "desk"] += 1
    return out


def not_kept_line(s: dict) -> str | None:
    n = s["not_kept"]
    if not (n["vault"] or n["desk"]):
        return None
    parts = [f"{n['vault']} deleted from Unsorted" if n["vault"] else "",
             f"{n['desk']} marked **Don't Keep** on the desk" if n["desk"] else ""]
    return (f"Not worth keeping: {n['vault'] + n['desk']} ({', '.join(p for p in parts if p)}). "
            f"Changed your mind about one? Give it a topic in `{app.command('review')}`.")


@dataclass(frozen=True)
class Lever:
    key: str
    applies: Callable[[dict], bool]
    says: Callable[[dict], str]


# Every lever scatha offers, most useful first. Surfaces show only those that apply now.
LEVERS = [
    Lever("gone", lambda s: s["held"] > 0,
          lambda s: f"`{app.command()}`: {s['held']} Unsorted notes disappeared at the same time. "
                    f"Did you delete them on purpose? Tell {app.DISPLAY_NAME}, and it will count them as not worth "
                    "keeping. (Until then, it won't record anything.)"),
    Lever("sort", lambda s: s["unsorted"] > 0,
          lambda s: f"`{app.command('review')}`: sort your {_n(s['unsorted'], 'Unsorted bookmark')}. "
                    f"Pick a topic, and {app.DISPLAY_NAME} will write the full note. "
                    "Pick **Don't Keep**, and the light note will go away."),
    Lever("discover", lambda s: s.get("new_unsorted", 0) >= DISCOVER_AT,
          lambda s: f"`{app.command('discover')}`: {s['new_unsorted']} bookmarks went to Unsorted since you last "
                    "checked your topics. Some of them may share a topic you don't have yet. "
                    "(Checking costs a few cents.)"),
    Lever("tune", lambda s: s["unsorted"] == 0 and bool(s["runs"]),
          lambda s: f"`{app.command('review')}`: look at Jev's closest calls. They show you where your topics overlap."),
    Lever("next", lambda s: (s["remaining"] or 0) > 0,
          lambda s: f"`{app.command('run')}`: get the next batch now, without waiting for the schedule. "
                    f"({_n(s['remaining'], 'bookmark')} still in the queue.)"),
    Lever("retry", lambda s: s["failed"] > 0,
          lambda s: (f"{_n(s['failed'], 'bookmark')} failed. You don't need to do anything: {app.DISPLAY_NAME} will "
                     f"try {'it' if s['failed'] == 1 else 'them'} again next run. If {'it keeps' if s['failed'] == 1 else 'they keep'} "
                     f"failing, type `{app.command('setup')}` to find the problem.")),
]


def levers(s: dict, limit: int | None = None) -> list[str]:
    out = [l.says(s) for l in LEVERS if l.applies(s)]
    return out[:limit] if limit else out


# ---------- the report ----------

def _when(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).strftime("%a %b %d, %H:%M")


def last_run_line(s: dict) -> str:
    last = s["last"]
    if not last:
        return "No runs yet."
    by = ", ".join(f"{n} {s['labels'].get(k, k)}" for k, n in sorted(last["written"].items(), key=lambda kv: -kv[1]))
    total = sum(last["written"].values())
    run = "Last successful run" if s.get("stop") else "Last run"
    line = f"{run} {_when(last['at'])}: {_n(total, 'new note')}" + (f" ({by})" if by else "") + "."
    if last["unsorted"]:
        one = last["unsorted"] == 1
        line += f" {last['unsorted']} didn't fit your topics" + (
            f", so {'it' if one else 'they'} went to Unsorted." if last.get("unsorted_mode", "light") != "off"
            else f", and {app.DISPLAY_NAME} didn't keep {'it' if one else 'them'}.")
    if last["failed"]:
        line += f" {_retry(last['failed'])}."
    return line


def text(cfg: dict) -> str:
    """The full report, for the terminal."""
    s = state(cfg)
    lines = [app.DISPLAY_NAME, ""] + (stop_lines(s["stop"]) + [""] if s["stop"] else []) + [last_run_line(s)]
    if s["mode"] != "off":
        lines.append(f"Unsorted now: {_n(s['unsorted'], 'bookmark')}, in {s['unsorted_dir']}" if s["unsorted"]
                     else "Unsorted is empty.")
    if not_kept_line(s):
        lines.append(not_kept_line(s))
    if s["remaining"]:
        lines.append(f"Still in the queue: {_n(s['remaining'], 'older bookmark')}.")
    acts = levers(s)
    if acts:
        lines += ["", "What you can do:"] + [f"  - {a}" for a in acts]
    if len(s["runs"]) > 1:
        lines += ["", "Recent runs:"]
        for r in s["runs"][-5:][::-1]:
            lines.append(f"  {_when(r['at'])}  {sum(r['written'].values()):>3} notes  {r['unsorted']:>3} unsorted  {r['failed']:>2} failed")
    lines += ["", f"Status note in your vault: {note_path(cfg, s['tax'])}"]
    return terminal("\n".join(lines))


def note_path(cfg: dict, tax: Taxonomy) -> Path:
    if cfg.get("status_note"):
        return Path(cfg["status_note"]).expanduser()
    return unsorted.folder(cfg, tax).parent / f"{app.APP_NAME}.md"


def note(cfg: dict) -> str:
    """The status note: the same facts where the user already looks, rewritten every run."""
    s = state(cfg)
    out = ["---", app.STATUS_TAG, f"updated: {dt.datetime.now().isoformat(timespec='minutes')}", "---", "",
           f"# {app.DISPLAY_NAME}", "",
           f"*{app.DISPLAY_NAME} rewrites this note after every run, so any edits here will be lost.*", ""]
    if s["stop"]:
        out += ["## The last run stopped", ""] + [l.removeprefix("  ") for l in stop_lines(s["stop"])] + [""]
    out.append(last_run_line(s))
    if s["mode"] != "off":
        if s["unsorted"]:
            out += ["", f"## Unsorted: {s['unsorted']}", "",
                    "These bookmarks don't fit your topics well enough to file. They're light notes: the post and "
                    f"its links, no summary yet. Sort one, and {app.DISPLAY_NAME} will write its full note."]
            newest = unsorted.newest(cfg, s["tax"])
            out += [""] + [f"- [[{p.stem}]]" for p in newest]
            if s["unsorted"] > len(newest):
                out.append(f"- …and {s['unsorted'] - len(newest)} more in `{s['unsorted_dir'].name}/`")
        else:
            out += ["", "Unsorted is empty."]
        out += ["", f"Delete an Unsorted note you don't want, and {app.DISPLAY_NAME} will count it as not worth keeping."]
    if not_kept_line(s):
        out += ["", not_kept_line(s)]
    acts = levers(s, limit=2)
    if acts:
        out += ["", "## What you can do", ""] + [f"- {a}" for a in acts]
    if s["remaining"]:
        out += ["", f"{_n(s['remaining'], 'older bookmark')} still in the queue."]
    return "\n".join(out) + "\n"


def write_note(cfg: dict) -> Path | None:
    """Rewrite the status note. A note of the user's at that path is never replaced: the status
    note moves to the first free "scatha status" name beside it, and the config remembers it."""
    try:
        tax = Taxonomy.load(app.taxonomy_path(cfg))
        path = note_path(cfg, tax)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = note(cfg)
        names = [path.name] + [f"{app.APP_NAME} status{'' if n == 1 else f' {n}'}.md" for n in range(1, 100)]
        for name in names:
            candidate = path.with_name(name)
            if app.is_status_note(candidate):
                candidate.write_text(text, encoding="utf-8")
            else:
                try:
                    with candidate.open("x", encoding="utf-8") as f:
                        f.write(text)
                except FileExistsError:
                    continue  # the user's
            if candidate != path:
                saved = app.load_config()
                saved["status_note"] = cfg["status_note"] = str(candidate)
                app.save_config(saved)
            return candidate
        return None
    except OSError:
        return None
