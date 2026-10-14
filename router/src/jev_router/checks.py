"""Is everything a run needs actually working? Shared by setup (its recap, `status`) and by
every scheduled run, which checks before it touches X.

These make real calls: X's whoami through Smaug (a couple of seconds), one tiny Jev
question (a fraction of a cent), and a one-word reply from Haiku.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import app, topics


def found(tool: str) -> str | None:
    """A recorded tool path that still exists, or the command the PATH finds under that name."""
    if tool and Path(tool).is_file():
        return tool
    return shutil.which(tool) if tool else None


def verify_x(cfg: dict) -> str | None:
    """Returns the @handle X sees, or None."""
    from .run import smaug_env
    app.load_secrets()
    try:
        p = subprocess.run([cfg["tools"]["node"], "src/cli.js", "whoami"], cwd=app.REPO / "smaug", env=smaug_env(cfg),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"user:\s*(@\S+)", p.stdout)
    return m.group(1) if m else None


def verify_typesafe() -> str | None:
    """Returns None if the key works, else the error."""
    try:
        from typesafe_sdk import Noul, TypeSafeClient
        r = TypeSafeClient(model=topics.MODEL).system_one("The sky is blue.", {"q": Noul(instructions="Is this about color?")})
        float(r.answers["q"].noul)
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def verify_notes(cfg: dict) -> str | None:
    """Returns None if Haiku answers through the configured backend, else what went wrong."""
    from . import notes
    if cfg["notes_backend"] == "claude-code" and not found(cfg["tools"]["claude"]):
        return f"Claude Code isn't at {cfg['tools']['claude']} any more (reinstalled or moved?)"
    os.environ["NOTES_BACKEND"] = cfg["notes_backend"]
    os.environ["CLAUDE_BIN"] = cfg["tools"]["claude"]
    try:
        answer, _ = notes.haiku([{"type": "text", "text": "Reply with the single word: ready"}])
        return None if "ready" in str(answer).lower() else f"unexpected reply: {answer!r}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


_LOGGED_OUT = re.compile(r"authenticat|oauth|/login|not logged in|invalid api key|\b401\b", re.I)


def notes_error_in_words(err: str, backend: str) -> str:
    """verify_notes' error, with the common one (a login that expired) said plainly."""
    if _LOGGED_OUT.search(err):
        if backend == "claude-code":
            return ("Claude Code isn't logged in, or its login expired. In a terminal, run 'claude', "
                    "type /login, and sign in")
        return "the Anthropic API key was rejected"
    return err[:200]


def preflight(cfg: dict) -> list[str]:
    """What a run needs, in plain words: one line per thing that isn't working. Empty means go."""
    problems = []
    if not found(cfg["tools"]["node"]):
        problems.append(f"Node.js isn't at {cfg['tools']['node']} any more")
    elif not verify_x(cfg):
        problems.append("your X login has expired (logged out of X, or the cookies aged out)")
    if err := verify_typesafe():
        problems.append(f"Jev didn't answer, so the TypeSafe key may be revoked or out of credit ({err[:100]})")
    if err := verify_notes(cfg):
        writer = "Claude Code" if cfg["notes_backend"] == "claude-code" else "the Anthropic API"
        problems.append(f"Haiku didn't answer through {writer}: {notes_error_in_words(err, cfg['notes_backend'])}")
    return problems
