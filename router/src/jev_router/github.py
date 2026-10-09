"""Vital signs for GitHub repos a note links to, so a note about a tool ages honestly.

A repo's stars, last push, archived flag, and last release, as of the day they were
checked. Two anonymous API calls per repo (60 an hour without a token; set
GITHUB_TOKEN for 5,000). Any failure, a rate limit included, gives None: the note
goes out without vital signs rather than with wrong ones.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

API = "https://api.github.com/repos/"
NOT_OWNERS = {"orgs", "features", "topics", "collections", "sponsors", "marketplace", "settings", "about",
              "pricing", "enterprise", "login", "search", "explore", "trending", "apps", "site", "readme"}


class RateLimited(Exception):
    pass


def repo_of(url: str) -> str | None:
    """'owner/repo' for a link into a repo (any depth), else None."""
    m = re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", url or "")
    if not m or m.group(1).lower() in NOT_OWNERS:
        return None
    repo = re.sub(r"\.git$", "", m.group(2))
    return f"{m.group(1)}/{repo}" if repo else None


def _get(path: str) -> dict | None:
    """The API's JSON, None on 404, RateLimited when GitHub says so."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "scatha"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, headers=headers), timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429 or (e.code == 403 and e.headers.get("x-ratelimit-remaining") == "0"):
            raise RateLimited(path) from e
        if e.code == 404:
            return None
        raise


def vitals(full_name: str) -> dict | None:
    """{'repo', 'stars', 'pushed', 'archived', 'last_release' (or None), 'checked'}, or None if unavailable.
    Raises RateLimited so a caller working through many repos can stop and say so."""
    try:
        r = _get(full_name)
        if not r or "stargazers_count" not in r:
            return None
        release = _get(full_name + "/releases/latest")
    except RateLimited:
        raise
    except Exception:  # network trouble, a 5xx: no vital signs this time
        return None
    day = lambda s: s[:10] if s else None
    return {
        "repo": r.get("full_name") or full_name,
        "stars": int(r["stargazers_count"]),
        "pushed": day(r.get("pushed_at")),
        "archived": bool(r.get("archived")),
        "last_release": day((release or {}).get("published_at")),
        "checked": dt.date.today().isoformat(),
    }


def front_matter(v: dict) -> list[str]:
    lines = [f"repo: {v['repo']}", f"repo_stars: {v['stars']}"]
    if v.get("pushed"):
        lines.append(f"repo_pushed: {v['pushed']}")
    lines.append(f"repo_archived: {'true' if v['archived'] else 'false'}")
    if v.get("last_release"):
        lines.append(f"repo_last_release: {v['last_release']}")
    lines.append(f"repo_checked: {v['checked']}")
    return lines


def inline(v: dict) -> str:
    """' · 949 stars · pushed 2026-02-03 · archived', for a repo's line under Links."""
    parts = [f"{v['stars']:,} stars"]
    if v.get("pushed"):
        parts.append(f"pushed {v['pushed']}")
    if v["archived"]:
        parts.append("archived")
    return "".join(" · " + p for p in parts)
