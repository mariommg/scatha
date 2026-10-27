"""Uninstall: show everything scatha put on this machine, then remove only what the user picks.

Nothing is removed by default. Each item says what it is, why it's there, and what else
removing it touches. Notes are the user's: they're listed so nothing is a surprise, and never
offered. The data folder is only offered when it looks like scatha's and nothing else (its
config, no Obsidian settings, nothing at the top that scatha doesn't write), so a
SCATHA_HOME pointed at the wrong folder can't take that folder with it. The program itself
(this checkout and its Python environment) can't delete itself while it runs: the last lines
say how. So do the things no local step can undo: an X login, a TypeSafe key.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import app, scheduler

# What scatha writes at the top of its data folder. Anything else there means it isn't only ours.
OURS = {"config.json", "topics.yaml", "secrets.env", "run.ps1", "run.sh", "work", "tuning", ".run.lock"}

TOOL = "jev-router"  # the uv tool that provides the `scatha` command (the package name)

# Windows user environment variables setup may have saved, and what else reads each one.
USER_VARS = {
    "TWITTER_AUTH_TOKEN": "Your X login. bird reads it too, if you use bird on its own.",
    "TWITTER_CT0": "Your X login (second half). bird reads it too.",
    "AUTH_TOKEN": "Your X login, under the plain name older setups used. bird and other X tools read this name.",
    "CT0": "Your X login (second half), plain name. bird and other X tools read this name.",
    "TYPESAFE_API_KEY": "Your TypeSafe key. Any other program of yours that uses Jev reads it.",
    "ANTHROPIC_API_KEY": "Your Anthropic key. Many programs read this name (Claude tools, SDK scripts).",
    app.HOME_ENV: "Points scatha at a data folder other than the usual one. Only scatha reads it.",
}


@dataclass
class Item:
    key: str            # the name for --remove
    what: str
    why: str
    splash: str         # what else removing it touches
    remove: Callable[[], str] | None = None  # None: listed only
    note: str = ""      # for listed-only items: why it isn't offered, or how to do it by hand


def data_folder_problem(path: Path) -> str | None:
    """Why this folder must not be deleted as scatha's data folder, or None."""
    if not path.is_dir():
        return "it isn't there"
    p = path.resolve()
    if p == Path.home().resolve() or p.parent == p:
        return "it's your home folder or a drive root"
    if (p / ".obsidian").exists():
        return "it holds Obsidian settings (a vault)"
    if not (p / "config.json").is_file():
        return "it has no scatha config.json"
    extra = sorted(c.name for c in p.iterdir() if c.name not in OURS)
    if extra:
        return "it holds things scatha didn't put there: " + ", ".join(extra[:5]) + (" ..." if len(extra) > 5 else "")
    return None


def _taxonomy(cfg: dict):
    from . import Taxonomy
    try:
        return Taxonomy.load(app.taxonomy_path(cfg))
    except Exception:  # half-finished setup: no topics yet
        return None


def _scatha_note(path: Path) -> bool:
    """A status note scatha wrote (it tags its own), not a note the user named the same."""
    try:
        return path.is_file() and "tags: [scatha]" in path.read_text(encoding="utf-8")[:200]
    except OSError:
        return False


def _remove_tool() -> str:
    p = subprocess.run(["uv", "tool", "uninstall", TOOL], capture_output=True, text=True)
    if p.returncode == 0:
        return f"Removed the `{app.APP_NAME}` command."
    return f"Couldn't remove the `{app.APP_NAME}` command while it's running. Afterwards, type: uv tool uninstall {TOOL}"


def _tool_installed() -> bool:
    if not shutil.which("uv"):
        return False
    p = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True)
    return any(line.split(" ")[0] == TOOL for line in p.stdout.splitlines())


def _rmtree(path: Path, done: str) -> Callable[[], str]:
    def go() -> str:
        shutil.rmtree(path)
        return done
    return go


def _unlink(path: Path, done: str) -> Callable[[], str]:
    def go() -> str:
        path.unlink()
        return done
    return go


def _unset(var: str) -> Callable[[], str]:
    def go() -> str:
        app.delete_user_var(var)
        return f"Removed the user environment variable {var}."
    return go


def _forget(var: str) -> Callable[[], str]:
    def go() -> str:
        app.keychain_delete(var)
        return f"Removed {var} from {app.KEYCHAIN}."
    return go


def inventory(cfg: dict) -> list[Item]:
    """Everything scatha put on this machine that's still here, in the order it's shown."""
    items: list[Item] = []
    home = app.home()

    if scheduler.installed():
        items.append(Item("schedule", f"The schedule ({app.task_name()})",
                          "Runs the import on its own, " + scheduler.describe(cfg["schedule"]) + ".",
                          "Nothing else. Imports stop until you run setup's schedule step again.",
                          scheduler.remove))
    for task in scheduler.others():
        items.append(Item(f"schedule:{task}", f"Another install's schedule ({task})",
                          "Left by a second data folder, usually a test install.",
                          "That install stops importing. Its data folder stays (delete it by hand if you're done with it).",
                          lambda t=task: scheduler.remove(t)))

    problem = data_folder_problem(home)
    if home.exists():
        what = f"The data folder: {home}"
        why = ("Your settings and topics, the record of what's been imported, your answers on the desk, "
               "logs, and copies of the bookmarks' text" + (", and secrets.env (your X login and keys)" if (home / "secrets.env").exists() else "") + ".")
        splash = "Only scatha uses it. Without the import record, a new install would import everything again."
        if problem:
            items.append(Item("data", what, why, splash, note=f"Not offered: {problem}. Delete it by hand if you're sure."))
        else:
            items.append(Item("data", what, why, splash, _rmtree(home, f"Deleted {home}.")))

    for var in app.stored_in_keychain(list(app.SECRETS.values())):
        items.append(Item(f"keychain-{var.lower()}", f"{app.KEYCHAIN} entry {var}",
                          "Saved by setup so scheduled runs can read it.", "Nothing else: only scatha reads this entry.",
                          _forget(var)))
    for var in app.stored_user_vars(list(USER_VARS)):
        items.append(Item(var.lower(), f"User environment variable {var}", "Saved by setup so scheduled runs can read it.",
                          USER_VARS[var], _unset(var)))

    tax = _taxonomy(cfg)
    if tax is not None:
        from . import report, unsorted
        note = report.note_path(cfg, tax)
        if _scatha_note(note):
            items.append(Item("status-note", f"The status note: {note}", "Rewritten after every run with what happened.",
                              "Nothing else. It's scatha's page, not one of your notes.",
                              _unlink(note, f"Deleted {note}.")))
        folders = list(dict.fromkeys(Path(t.destination) for t in tax.topics if t.destination))
        where = unsorted.folder(cfg, tax)
        if unsorted.mode(cfg) != "off" and where.is_dir():
            folders.append(where)
        if folders:
            items.append(Item("notes", "Your notes", "Written into: " + "; ".join(str(f) for f in folders),
                              "They're yours.", note="Never offered. They're plain Markdown and work without scatha."))

    modules = app.REPO / "smaug" / "node_modules"
    if modules.is_dir():
        items.append(Item("node-modules", f"Smaug's packages: {modules}", "Installed by setup for the X fetcher.",
                          "Only scatha uses them. Setup puts them back if you reinstall.",
                          _rmtree(modules, f"Deleted {modules}.")))
    if _tool_installed():
        items.append(Item("command", f"The `{app.APP_NAME}` command", "Installed by setup (uv tool) so it works from any folder.",
                          "Nothing else.", _remove_tool))
    if shutil.which("bird"):
        items.append(Item("bird", "bird (the X command-line tool)", "scatha tries it when its built-in X client fails.",
                          "Other programs of yours may use it.",
                          note="Not offered: setup may not have installed it. To remove it: npm uninstall -g @steipete/bird"))
    return items


def afterwards(cfg: dict) -> list[str]:
    """What no step here can do."""
    lines = [
        "Your X login stays active until you log out of X in your browser (or end the session in X's settings). "
        "Deleting it here only removes the copy on this machine.",
        "Your TypeSafe key still works until you revoke it at typesafe.ai.",
    ]
    if cfg.get("notes_backend") == "anthropic-api":
        lines.append("Your Anthropic API key still works until you revoke it at console.anthropic.com.")
    if cfg.get("ntfy_topic"):
        lines.append(f"Your phone is still subscribed to the ntfy topic '{cfg['ntfy_topic']}'; remove it in the ntfy app.")
    lines.append(f"The program itself: delete {app.REPO} (its Python environment is inside it).")
    return lines


def keep_copy(home: Path, dest: Path) -> Path:
    """Topics, settings, and desk answers (no secrets, no bookmark text), for a reinstall."""
    dest.mkdir(parents=True)
    for name in ("topics.yaml", "config.json"):
        if (home / name).is_file():
            shutil.copy2(home / name, dest / name)
    tuning = home / "tuning"
    if tuning.is_dir():
        shutil.copytree(tuning, dest / "tuning", ignore=shutil.ignore_patterns("desk"))
    return dest


def copy_dest() -> Path:
    dest = Path.home() / f"{app.APP_NAME}-kept-{dt.date.today().isoformat()}"
    return dest if not dest.exists() else dest.with_name(dest.name + dt.datetime.now().strftime("-%H%M%S"))


def show(items: list[Item], ui) -> None:
    for i, it in enumerate(items, 1):
        ui.say()
        ui.say(f"  {i:>2}  {ui.bold(it.what)}  {ui.dim('[' + it.key + ']')}")
        ui.say(f"      {it.why}")
        ui.say(ui.dim(f"      If removed: {it.splash}"))
        if not it.remove:
            ui.say(ui.dim(f"      {it.note}"))


def pick(items: list[Item], answer: str) -> tuple[list[Item], list[str]]:
    """Numbers or keys, separated by spaces or commas. Returns (chosen, what couldn't be used)."""
    chosen, bad = [], []
    by_key = {it.key: it for it in items}
    for tok in answer.replace(",", " ").split():
        it = items[int(tok) - 1] if tok.isdigit() and 1 <= int(tok) <= len(items) else by_key.get(tok)
        if it is None or it.remove is None:
            bad.append(tok)
        elif it not in chosen:
            chosen.append(it)
    return chosen, bad


def main(argv: list[str] | None = None) -> int:
    from . import onboard as ui
    done: list[str] = []
    try:
        return _main(argv, ui, done)
    except (KeyboardInterrupt, EOFError):
        ui.say("\n  Stopped. " + ("Removed before that: " + "; ".join(done) if done else "Nothing removed."))
        return 130


def _main(argv: list[str] | None, ui, done: list[str]) -> int:
    import argparse
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ui.COLOR = ui.enable_color()
    ap = argparse.ArgumentParser(prog=f"{app.APP_NAME} uninstall",
                                 description="Lists what scatha put on this machine. Removes only what you pick.")
    # Any number of words: PowerShell splits "schedule,data" into two before it gets here
    ap.add_argument("--remove", nargs="*", default=[], help="numbers or names from the list, e.g. schedule data")
    ap.add_argument("--no-copy", action="store_true", help="don't keep a copy of topics and desk answers")
    ap.add_argument("--yes", action="store_true", help="don't ask to confirm (needed without a terminal)")
    args = ap.parse_args(argv)
    tty = sys.stdin.isatty()

    cfg = app.load_config()
    items = inventory(cfg)
    ui.say(ui.cyan(f"What {app.APP_NAME} put on this machine"))
    ui.say(ui.dim("Nothing is removed unless you pick it."))
    show(items, ui)
    ui.say()
    answer = " ".join(args.remove)
    if not answer:
        if not tty:
            ui.hint(f"To remove something: {app.APP_NAME} uninstall --remove <numbers or names> --yes")
            return 0
        answer = ui.ask("Numbers or names to remove, separated by spaces (Enter for none)", default="")
    chosen, bad = pick(items, answer)
    for tok in bad:
        ui.bad(f"'{tok}' isn't on the list, or isn't offered.")
    if bad and not tty:
        return 2
    if not chosen:
        ui.say("  Nothing removed.")
        return 0

    ui.question("About to remove:")
    for it in chosen:
        ui.say(f"    - {it.what}")
    if not args.yes:
        if not tty:
            ui.bad("Add --yes to remove without a terminal.")
            return 2
        if not ui.yes("Go ahead?", default=False):
            ui.say("  Nothing removed.")
            return 0

    if any(it.key == "data" for it in chosen) and not args.no_copy and (not tty or ui.yes(
            "Keep a copy of your topics, settings, and desk answers first (no secrets, no bookmark text)?")):
        ui.ok(f"Copy kept in {keep_copy(app.home(), copy_dest())}")
    failed = 0
    for it in chosen:
        try:
            ui.ok(it.remove())
            done.append(it.what)
        except Exception as e:  # keep going: one stuck item shouldn't leave the rest half done
            failed += 1
            ui.bad(f"{it.what}: {type(e).__name__}: {e}")

    ui.question("What this can't do for you:")
    for line in afterwards(cfg):
        ui.say(f"    - {line}")
    return 1 if failed else 0
