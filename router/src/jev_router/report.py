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
    return entry


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


def banner() -> str | None:
    u = unseen()
    if not u["runs"] or not (u["notes"] or u["unsorted"] or u["failed"]):
        return None
    bits = [f"{u['notes']} new notes"]
    if u["unsorted"]:
        bits.append(f"{u['unsorted']} went to Unsorted")
    if u["failed"]:
        bits.append(f"{u['failed']} failed and will retry")
    return f"Since you last looked: {', '.join(bits)}. Type `{app.command()}` to see what happened."


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
        "held": unsorted.held_count(), "not_kept": not_kept(), "new_unsorted": new_unsorted(runs),
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
             f"{n['desk']} marked on the desk" if n["desk"] else ""]
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
          lambda s: f"`{app.command()}`: {s['held']} Unsorted notes disappeared at once. "
                    "Say whether they count as not worth keeping (nothing is recorded until you do)."),
    Lever("sort", lambda s: s["unsorted"] > 0,
          lambda s: f"`{app.command('review')}`: sort the {s['unsorted']} unsorted bookmarks. "
                    "Picking a topic writes the full note; Don't keep removes it."),
    Lever("discover", lambda s: s.get("new_unsorted", 0) >= DISCOVER_AT,
          lambda s: f"`{app.command('discover')}`: {s['new_unsorted']} bookmarks have gone to Unsorted since your "
                    "topics were last checked. See whether they share a topic you haven't named (a few cents)."),
    Lever("tune", lambda s: s["unsorted"] == 0 and bool(s["runs"]),
          lambda s: f"`{app.command('review')}`: check Jev's closest calls, so you know where your topics blur."),
    Lever("next", lambda s: (s["remaining"] or 0) > 0,
          lambda s: f"`{app.command('run')}`: import the next batch now ({s['remaining']} still queued) "
                    "instead of waiting for the schedule."),
    Lever("retry", lambda s: s["failed"] > 0,
          lambda s: (f"Nothing to do for the {s['failed']} that failed: they retry next run. " if s["failed"] > 1
                     else "Nothing to do for the one that failed: it retries next run. ") +
                    f"If it keeps happening, `{app.command('setup')}` finds what's broken."),
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
        return "No runs recorded yet."
    by = ", ".join(f"{n} {s['labels'].get(k, k)}" for k, n in sorted(last["written"].items(), key=lambda kv: -kv[1]))
    total = sum(last["written"].values())
    line = f"Last run {_when(last['at'])}: {total} new notes" + (f" ({by})" if by else "") + "."
    if last["unsorted"]:
        line += f" {last['unsorted']} didn't fit your topics" + (
            " and went to Unsorted." if last.get("unsorted_mode", "light") != "off" else " and weren't kept.")
    if last["failed"]:
        line += f" {last['failed']} failed and will retry next run."
    return line


def text(cfg: dict) -> str:
    """The full report, for the terminal."""
    s = state(cfg)
    lines = [f"{app.APP_NAME}", "", last_run_line(s)]
    if s["mode"] != "off":
        lines.append(f"Unsorted now: {s['unsorted']} bookmarks in {s['unsorted_dir']}" if s["unsorted"]
                     else "Unsorted is empty: every bookmark found a topic.")
    if not_kept_line(s):
        lines.append(not_kept_line(s))
    if s["remaining"]:
        lines.append(f"Still queued: {s['remaining']} older bookmarks.")
    acts = levers(s)
    if acts:
        lines += ["", "What you can do:"] + [f"  - {a}" for a in acts]
    if len(s["runs"]) > 1:
        lines += ["", "Recent runs:"]
        for r in s["runs"][-5:][::-1]:
            lines.append(f"  {_when(r['at'])}  {sum(r['written'].values()):>3} notes  {r['unsorted']:>3} unsorted  {r['failed']:>2} failed")
    lines += ["", f"Status note in your vault: {note_path(cfg, s['tax'])}"]
    return "\n".join(lines)


def note_path(cfg: dict, tax: Taxonomy) -> Path:
    if cfg.get("status_note"):
        return Path(cfg["status_note"]).expanduser()
    return unsorted.folder(cfg, tax).parent / f"{app.APP_NAME}.md"


def note(cfg: dict) -> str:
    """The status note: the same facts where the user already looks, rewritten every run."""
    s = state(cfg)
    out = ["---", "tags: [scatha]", f"updated: {dt.datetime.now().isoformat(timespec='minutes')}", "---", "",
           f"# {app.APP_NAME}", "", "*Rewritten after every run. Edits here are replaced.*", "", last_run_line(s)]
    if s["mode"] != "off":
        if s["unsorted"]:
            out += ["", f"## Unsorted: {s['unsorted']}", "",
                    "These fit none of your topics well enough to be filed. They're light notes (no summary yet); "
                    "sorting one writes its full note."]
            newest = unsorted.newest(cfg, s["tax"])
            out += [""] + [f"- [[{p.stem}]]" for p in newest]
            if s["unsorted"] > len(newest):
                out.append(f"- …and {s['unsorted'] - len(newest)} more in `{s['unsorted_dir'].name}/`")
        else:
            out += ["", "Unsorted is empty: every bookmark found a topic."]
        out += ["", "Delete an unsorted note you don't want, and scatha counts it as not worth keeping."]
    if not_kept_line(s):
        out += ["", not_kept_line(s)]
    acts = levers(s, limit=2)
    if acts:
        out += ["", "## What you can do", ""] + [f"- {a}" for a in acts]
    if s["remaining"]:
        out += ["", f"{s['remaining']} older bookmarks are still queued."]
    return "\n".join(out) + "\n"


def write_note(cfg: dict) -> Path | None:
    try:
        tax = Taxonomy.load(app.taxonomy_path(cfg))
        path = note_path(cfg, tax)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note(cfg), encoding="utf-8")
        return path
    except OSError:
        return None
