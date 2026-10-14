"""The sorting desk: a local page where a person checks Jev's calls on their own items.

Generic: nothing here knows about X or scatha. A consumer builds a deck (its topics
and its scored items) and calls serve(); the person clicks where each item belongs,
and every click is saved straight to a verdicts file. scatha's deck is review.py.

    deck.json      {"title", "topics": [{key, label, question, yes_means, no_means}],
                    "cutoff", "topics_hash", "built", "items": [{id, text, scores, placed, ...}]}
    verdicts.json  {"version": 1, "verdicts": {item_id: {verdict, bucket, note, ...}}}

A verdict is a topic key, "drop" (belongs nowhere), or "unsure". Only what the person
clicks counts: an untouched item is not agreement. The server is the only writer,
one item at a time, so a page reload or a second tab can't overwrite other answers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

DROP, UNSURE = "drop", "unsure"
PAIR_GAP = 0.10  # top two topics closer than this: Jev's ±0.05 drift can flip the placement
CUT_GAP = 0.10  # best topic this close to the keep/drop cutoff


def topics_hash(topics: list[dict]) -> str:
    """Changes when any definition Jev reads changes (labels and folders don't count)."""
    core = [[t["key"], t.get("question"), t.get("yes_means"), t.get("no_means")] for t in topics]
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()[:12]


def closeness(scores: dict[str, float], cutoff: float) -> dict:
    """Why an item is a close call, if it is: two topics nearly tied, or the best one near the cutoff."""
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return {"close": False, "gap": None, "pair": None, "near_cut": False}
    best = ranked[0][1]
    gap = round(best - ranked[1][1], 3) if len(ranked) > 1 else None
    pair = [ranked[0][0], ranked[1][0]] if gap is not None and gap < PAIR_GAP and best >= cutoff else None
    near_cut = abs(best - cutoff) < CUT_GAP
    return {"close": bool(pair) or near_cut, "gap": gap, "pair": pair, "near_cut": near_cut}


def _write_json(path: Path, data) -> None:
    """Atomic: a crash mid-write leaves the old file, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):  # Windows refuses the swap while something else has the file open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1 * (attempt + 1))


class Verdicts:
    """The verdicts file. One lock, read-modify-write per item."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8")).get("verdicts", {})

    def put(self, item_id: str, record: dict | None) -> dict | None:
        """Save one item's verdict; None clears it. Every other item is left as it is on disk."""
        with self.lock:
            all_ = self.load()
            if record is None:
                all_.pop(item_id, None)
            else:
                all_[item_id] = record
            _write_json(self.path, {"version": 1, "verdicts": all_})
            return record


def record_for(item: dict, deck: dict, verdict: str | None, note: str) -> dict | None:
    """What gets saved: the person's answer plus what Jev said at the time, so later scoring can compare."""
    keys = {t["key"] for t in deck["topics"]}
    if verdict is not None and verdict not in keys | {DROP, UNSURE}:
        raise ValueError(f"unknown verdict {verdict!r}")
    note = (note or "").strip()
    if verdict is None and not note:
        return None
    return {
        "verdict": verdict,
        "bucket": verdict if verdict in keys else None,
        "note": note,
        "author": item.get("author"),
        "jev": item.get("placed"),
        "scores": item.get("scores"),
        "topics_hash": deck.get("topics_hash"),
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "desk",
    }


def page() -> str:
    return files("jev_router").joinpath("desk.html").read_text(encoding="utf-8")


def make_server(deck: dict, store: Verdicts, current_hash: str | None = None, port: int = 0):
    """A server bound to this machine only. Every API call must carry the launch token."""
    token = secrets.token_urlsafe(16)
    items = {str(i["id"]): i for i in deck["items"]}
    html = page()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code: int, body, ctype="application/json"):
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost") and secrets.compare_digest(
                self.headers.get("X-Desk-Token", ""), token)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, html, "text/html")
            if path == "/api/deck":
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                stale = bool(current_hash and current_hash != deck.get("topics_hash"))
                return self._send(200, {"deck": deck, "verdicts": store.load(), "stale": stale,
                                        "verdicts_path": str(store.path)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            path = self.path.split("?")[0]
            if path == "/api/done":
                self._send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path != "/api/verdict":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                item = items[str(body["id"])]
                rec = record_for(item, deck, body.get("verdict"), body.get("note", ""))
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            try:
                saved = store.put(str(body["id"]), rec)
            except OSError as e:
                return self._send(500, {"error": f"could not write {store.path}: {e}"})
            self._send(200, {"ok": True, "record": saved})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = token
    server.url = f"http://127.0.0.1:{server.server_address[1]}/#t={token}"
    return server


def serve(deck_path: Path, verdicts_path: Path, current_hash: str | None = None,
          open_browser: bool = True, say=print) -> None:
    deck = json.loads(Path(deck_path).read_text(encoding="utf-8"))
    server = make_server(deck, Verdicts(verdicts_path), current_hash)
    say(f"  The sorting desk is open at {server.url}")
    say(f"  Every click is saved to {verdicts_path}.")
    say("  When you're done, press Done on the page (or Ctrl+C here).")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    say("  Desk closed.")
