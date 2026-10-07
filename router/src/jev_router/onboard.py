"""Setup wizard: from a fresh checkout to scheduled bookmark imports.

    uv run --project router python -m jev_router.onboard              # the whole thing
    uv run --project router python -m jev_router.onboard --answers my-setup.json
    uv run --project router python -m jev_router.onboard x-login      # fresh X cookies only
    uv run --project router python -m jev_router.onboard topics       # redo topics and folders
    uv run --project router python -m jev_router.onboard schedule     # change when it runs
    uv run --project router python -m jev_router.onboard status       # what's set up, what isn't
    uv run --project router python -m jev_router.onboard unschedule

Secrets are typed into hidden prompts, checked against the real service, and only
then stored (Windows: your user environment variables; macOS/Linux: a file in the
data folder only you can read). They never go into config.json, the answers file,
or a log.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import app, scheduler, topics, vaults

# ---------- prompt helpers ----------

def say(text: str = "") -> None:
    print(text, flush=True)


def heading(n: int, total: int, title: str) -> None:
    say(f"\n── Step {n} of {total}: {title} " + "─" * max(0, 50 - len(title)))


def ask(prompt: str, default: str | None = None) -> str:
    hint = f" [{default}]" if default else ""
    while True:
        v = input(f"  {prompt}{hint}: ").strip()
        if v or default is not None:
            return v or default
        say("  (I need an answer here.)")


def yes(prompt: str, default: bool = True) -> bool:
    v = input(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not v else v.startswith("y")


def choose(prompt: str, options: list[str], default: int = 1) -> int:
    for i, o in enumerate(options, 1):
        say(f"    {i}. {o}")
    while True:
        v = input(f"  {prompt} [{default}]: ").strip() or str(default)
        if v.isdigit() and 1 <= int(v) <= len(options):
            return int(v)
        say(f"  (Pick a number from 1 to {len(options)}.)")


def secret(prompt: str) -> str:
    while True:
        v = getpass.getpass(f"  {prompt} (typing is hidden): ").strip()
        if v:
            return v
        say("  (Nothing came through. Paste it and press Enter.)")


# ---------- checks ----------

def which(name: str) -> str | None:
    if sys.platform == "win32" and name in ("bird", "npm"):
        return shutil.which(name + ".cmd") or shutil.which(name)
    return shutil.which(name)


def node_major(node: str) -> int:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
        return int(re.match(r"v(\d+)", out).group(1))
    except Exception:
        return 0


def verify_x(cfg: dict) -> str | None:
    """Returns the @handle X sees, or None."""
    from .run import smaug_env
    app.load_secrets()
    p = subprocess.run([cfg["tools"]["node"], "src/cli.js", "whoami"], cwd=app.REPO / "smaug", env=smaug_env(cfg),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
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
    from . import notes
    os.environ["NOTES_BACKEND"] = cfg["notes_backend"]
    os.environ["CLAUDE_BIN"] = cfg["tools"]["claude"]
    try:
        answer, _ = notes.haiku([{"type": "text", "text": "Reply with the single word: ready"}])
        return None if "ready" in str(answer).lower() else f"unexpected reply: {answer!r}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


# ---------- steps ----------

def step_prereqs(cfg: dict) -> bool:
    ok = True
    node = which("node")
    if not node or node_major(node) < 22:
        say("  ✗ Node.js 22 or newer is needed (it runs Smaug). Get it at https://nodejs.org, then run setup again.")
        ok = False
    else:
        cfg["tools"]["node"] = node
        say(f"  ✓ Node.js {node_major(node)}")
    smaug = app.REPO / "smaug"
    if ok and not (smaug / "node_modules").is_dir():
        say("  … installing Smaug's packages (one time)")
        subprocess.run([which("npm") or "npm", "install", "--silent"], cwd=smaug, check=False)
    bird = which("bird")
    if not bird and ok:
        say("  Optional: bird, a command-line X tool, as a backup. Smaug reads X with its own copy of")
        say("  bird's code; if that ever fails, an installed bird is tried instead. bird 0.8.0 is the")
        say("  last release (its author stopped maintaining it), so it's a backup, not a fix.")
        if yes("Install bird 0.8.0 with npm as a backup?", default=False):
            subprocess.run([which("npm") or "npm", "install", "-g", "@steipete/bird@0.8.0"], check=False)
            bird = which("bird")
    if bird:
        cfg["tools"]["bird"] = bird
        say("  ✓ bird (backup)")
    return ok


def step_x(cfg: dict, verify: bool = True) -> bool:
    say("  Your bookmarks come from X through your browser session. Two cookies do it:")
    say("    1. In your browser, open x.com (logged in).")
    say("    2. Open developer tools (F12) → Application (Storage in Firefox) → Cookies → https://x.com")
    say("    3. Copy the values of auth_token and ct0.")
    say("  They're the keys to your X session, so treat them like a password. They stop working")
    say("  when you log out of X; when that happens, run:  onboard x-login")
    handle = verify_x(cfg) if verify and app.has_secret("AUTH_TOKEN") else None
    if handle and not yes(f"Saved cookies still work (X sees {handle}). Replace them?", default=False):
        say("  ✓ Keeping the saved cookies.")
        return True
    for attempt in range(3):
        token = secret("auth_token")
        ct0 = secret("ct0")
        os.environ["AUTH_TOKEN"], os.environ["CT0"] = token, ct0
        handle = verify_x(cfg) if verify else "(not checked)"
        if handle:
            where = app.save_secret(app.SECRETS["x_auth_token"], token)
            app.save_secret(app.SECRETS["x_ct0"], ct0)
            say(f"  ✓ X sees you as {handle}. Saved to {where}.")
            return True
        say("  ✗ X didn't accept those. Double-check you copied the values, not the names, and try again.")
    return False


def step_typesafe(verify: bool = True) -> bool:
    say("  Jev (from TypeSafe) sorts each bookmark into your topics. It's cheap: a few cents covers")
    say("  hundreds of bookmarks. Get an API key at https://typesafe.ai (dashboard → API keys).")
    if app.has_secret(app.SECRETS["typesafe"]) and verify and not verify_typesafe():
        if not yes("A working TypeSafe key is already saved. Replace it?", default=False):
            say("  ✓ Keeping the saved key.")
            return True
    for _ in range(3):
        os.environ[app.SECRETS["typesafe"]] = key = secret("TypeSafe API key")
        err = verify_typesafe() if verify else None
        if not err:
            say(f"  ✓ Jev answered. Saved to {app.save_secret(app.SECRETS['typesafe'], key)}.")
            return True
        say(f"  ✗ That key didn't work ({err[:120]}). Try again?")
    return False


def step_notes(cfg: dict, preset: str | None = None, verify: bool = True) -> bool:
    say("  Claude Haiku writes each note: summary, key points, quotes, translation, reading images.")
    claude = which("claude")
    options = ["Claude Code, headless (uses your Claude plan; no API key needed)",
               "Anthropic API key (pay as you go, roughly a cent per note)"]
    if preset:
        pick = 1 if preset == "claude-code" else 2
    else:
        pick = choose("Which one?", options, default=1 if claude else 2)
    if pick == 1:
        if not claude:
            say("  ✗ I can't find the 'claude' command. Install Claude Code (https://claude.com/claude-code),")
            say("    log in once by running 'claude', then run setup again. Or pick the API option.")
            return False
        cfg["notes_backend"], cfg["tools"]["claude"] = "claude-code", claude
    else:
        cfg["notes_backend"] = "anthropic-api"
        if not app.has_secret(app.SECRETS["anthropic"]):
            say("  Get a key at https://console.anthropic.com (Settings → API keys).")
            os.environ[app.SECRETS["anthropic"]] = secret("Anthropic API key")
    err = verify_notes(cfg) if verify else None
    if err:
        say(f"  ✗ Haiku didn't answer: {err[:200]}")
        return False
    if cfg["notes_backend"] == "anthropic-api":
        app.save_secret(app.SECRETS["anthropic"], os.environ[app.SECRETS["anthropic"]])
    say("  ✓ Haiku answered.")
    return True


def pick_folder(label: str, found: list[Path], default_parent: Path | None) -> Path:
    options = [f"Obsidian vault: {v}" for v in found] + ["Another folder (type a path)"]
    pick = choose(f"Where should '{label}' notes go?", options,
                  default=(found.index(default_parent) + 1) if default_parent in found else 1)
    base = found[pick - 1] if pick <= len(found) else Path(ask("Folder path")).expanduser()
    sub = ask("Subfolder inside it (blank for none)", default=label)
    return base / sub if sub else base


def step_topics(cfg: dict, preset_topics: list[dict] | None = None, draft: bool | None = None) -> bool:
    if preset_topics is None:
        say("  Topics decide what gets kept and where it goes. A bookmark goes to the topic it fits best;")
        say("  one that fits none of them is skipped. Start from these, or write your own:")
        names = list(topics.PRESETS)
        for i, n in enumerate(names, 1):
            say(f"    {i:2}. {n}")
        picks = ask("Numbers to start from, comma-separated (blank for none)", default="1,2,3,4,5")
        chosen = [names[int(x) - 1] for x in re.findall(r"\d+", picks) if 0 < int(x) <= len(names)]
        preset_topics = []
        for n in chosen:
            p = topics.PRESETS[n]
            preset_topics.append({"label": n, "description": p.get("description") or p["yes_means"], "preset": True})
        while yes("Add a topic of your own?", default=not preset_topics):
            label = ask("Name (short, like 'Woodworking')")
            desc = ask("In one line, what belongs here?")
            preset_topics.append({"label": label, "description": desc})
        if not preset_topics:
            say("  ✗ You need at least one topic.")
            return False

    needs_definition = [t for t in preset_topics if "question" not in topics.PRESETS.get(t["label"], {})]
    if draft is None:
        draft = bool(needs_definition) and yes(
            "Have Haiku turn your one-liners into definitions Jev reads well? (recommended)")

    # Destinations
    found = vaults.find_vaults()
    if found and any(not t.get("destination") for t in preset_topics):
        say("  Obsidian vaults on this computer (most recent first):")
    elif not found:
        say("  No Obsidian vaults found; notes are plain Markdown, so any folder works.")
    last = None
    for t in preset_topics:
        if t.get("destination"):
            t["destination"] = str(Path(t["destination"]).expanduser())
        else:
            dest = pick_folder(t["label"], found, last)
            last = next((v for v in found if dest.is_relative_to(v)), None)
            t["destination"] = str(dest)

    # Definitions
    built = []
    labels = [t["label"] for t in preset_topics]
    for t in preset_topics:
        p = topics.PRESETS.get(t["label"], {})
        if t.get("preset") and "question" in p:
            d = {k: p[k] for k in ("question", "yes_means", "no_means")}
        elif draft:
            say(f"  … drafting '{t['label']}'")
            d = topics.draft_definition(t["label"], t["description"], [x for x in labels if x != t["label"]])
        else:
            d = topics.template_definition(t["label"], t["description"])
        built.append({"label": t["label"], **d, "destination": t["destination"]})
    path = topics.write_taxonomy(built, app.taxonomy_path(cfg))
    say(f"  ✓ {len(built)} topics saved to {path}")
    for b in built:
        say(f"     {b['label']:<22} → {b['destination']}")
    say("    (Plain YAML. Open it any time to adjust wording or folders.)")
    return True


def step_schedule(cfg: dict, preset: dict | None = None, batch: int | None = None) -> bool:
    if preset:
        cfg["schedule"] = preset
    else:
        pick = choose("How often should it run?", ["Weekly (recommended)", "Every other week", "Daily"])
        cadence = {1: "weekly", 2: "biweekly", 3: "daily"}[pick]
        day = "Saturday"
        if cadence != "daily":
            day = scheduler.DAYS[choose("Which day?", scheduler.DAYS, default=7) - 1]
        time = ask("What time (24-hour HH:MM)?", default="00:01")
        cfg["schedule"] = {"cadence": cadence, "day": day, "time": time}
    try:
        scheduler.validate(cfg["schedule"])
    except ValueError as e:
        say(f"  ✗ {e}")
        return False
    if batch is None:
        say("  Each run takes up to this many unprocessed bookmarks, newest first. A big backlog clears a batch")
        say("  per run. (For one Claude Pro user, 100 notes used about a quarter of a 5-hour window.)")
        batch = int(ask("Batch size", default=str(cfg["batch_size"])))
    cfg["batch_size"] = batch
    app.save_config(cfg)
    try:
        say("  ✓ " + scheduler.install(cfg))
    except Exception as e:
        say(f"  ✗ Couldn't register the schedule: {e}")
        return False
    if sys.platform == "win32":
        say("    It runs while you're logged in (a locked screen is fine) and wakes the PC if it's asleep.")
    return True


def step_ntfy(cfg: dict, preset: str | None = None) -> None:
    if preset is not None:
        cfg["ntfy_topic"] = preset
    else:
        say("  Optional: a phone ping after each run (\"23 new notes, 4 skipped\") through ntfy.sh.")
        say("  Install the ntfy app, subscribe to a topic name nobody would guess, and enter it here.")
        cfg["ntfy_topic"] = ask("ntfy topic (blank to skip)", default="")
    app.save_config(cfg)


def step_reader(cfg: dict, preset: str | None = None) -> None:
    if preset is not None:
        cfg["reader_profile"] = preset
    else:
        say("  Optional: one sentence about you, so how-to steps fit your setup.")
        say("  (Like: 'I work in Claude Code on Windows 11 and keep notes in Obsidian.')")
        cfg["reader_profile"] = ask("About you (blank to skip)", default=cfg.get("reader_profile", ""))
    app.save_config(cfg)


def step_test(cfg: dict) -> None:
    if not yes("Try it now on your 5 newest bookmarks? Notes go to a preview folder, not your vaults."):
        return
    from . import run
    run.main(["--preview", "--batch", "5", "--force"])
    say(f"  Preview notes: {app.work() / 'notes-preview'}")
    say("  (Nothing was recorded, so the first real run picks these up again.)")


# ---------- commands ----------

def status() -> None:
    cfg = app.load_config()
    app.load_secrets()
    say(f"{app.APP_NAME} status")
    say(f"  data folder   {app.home()}")
    say(f"  config        {'✓' if app.config_path().exists() else '✗ missing (run onboard)'}")
    tax = app.taxonomy_path(cfg)
    if tax.exists():
        from . import Taxonomy
        t = Taxonomy.load(tax)
        say(f"  topics        {', '.join((x.label or x.key) for x in t.topics)}")
    else:
        say("  topics        ✗ missing (run onboard topics)")
    for name, var in app.SECRETS.items():
        if name == "anthropic" and cfg["notes_backend"] != "anthropic-api":
            continue
        say(f"  {name:<13} {'✓ saved' if os.environ.get(var) or (name.startswith('x_') and os.environ.get(var.replace('TWITTER_', ''))) else '✗ missing'}")
    say(f"  note writer   {cfg['notes_backend']}")
    say(f"  schedule      {scheduler.describe(cfg['schedule'])}, {cfg['batch_size']} per run")
    if cfg.get("backup_dir"):
        zips = sorted(Path(cfg["backup_dir"]).expanduser().glob(f"{app.APP_NAME}-data_*.zip"))
        say(f"  backup        {cfg['backup_dir']} ({len(zips)} kept" + (f", newest {zips[-1].name})" if zips else ")"))
    else:
        say("  backup        off (set backup_dir in config.json)")
    ledger = app.work() / "ingested.md"
    if ledger.exists():
        say(f"  processed     {sum(1 for l in ledger.read_text(encoding='utf-8').splitlines() if 'x.com/' in l)} bookmarks")
    logs = sorted((app.work() / "logs").glob("*.log")) if (app.work() / "logs").exists() else []
    if logs:
        say(f"  last log      {logs[-1]}")


def load_answers(path: str | None) -> dict:
    if not path:
        return {}
    a = json.loads(Path(path).read_text(encoding="utf-8"))
    for bad in ("auth_token", "ct0", "typesafe_api_key", "anthropic_api_key"):
        if bad in a:
            raise SystemExit("That answers file contains a secret. Delete it and make a new one; "
                             "setup always asks for secrets in hidden prompts.")
    return a


def onboard(answers: dict, verify: bool = True) -> int:
    cfg = app.load_config()
    total = 7
    say(f"\n{app.APP_NAME} setup")
    say("Your X bookmarks, sorted into your topics and written up as notes, on a schedule.")
    say(f"Everything you set here lives in {app.home()} (secrets excepted; I'll say where each one goes).")

    heading(1, total, "Tools")
    if not step_prereqs(cfg):
        return 1
    app.save_config(cfg)
    heading(2, total, "X login")
    if not step_x(cfg, verify):
        return 1
    heading(3, total, "Jev (sorting)")
    if not step_typesafe(verify):
        return 1
    heading(4, total, "Haiku (writing notes)")
    if not step_notes(cfg, answers.get("notes_backend"), verify):
        return 1
    app.save_config(cfg)
    step_reader(cfg, answers.get("reader_profile"))
    heading(5, total, "Topics and folders")
    if not step_topics(cfg, answers.get("topics"), answers.get("draft_definitions")):
        return 1
    heading(6, total, "Schedule")
    step_ntfy(cfg, answers.get("ntfy_topic"))
    if not step_schedule(cfg, answers.get("schedule"), answers.get("batch_size")):
        return 1
    heading(7, total, "Test drive")
    step_test(cfg)
    say("\nAll set. Handy later:")
    say("  onboard status      what's set up")
    say("  onboard x-login     when X logs you out")
    say("  onboard topics      change topics or folders")
    say("  onboard schedule    change when it runs")
    say(f"  The runbook (docs/RUNBOOK.md) covers the rest.")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="onboard")
    ap.add_argument("command", nargs="?", default="all",
                    choices=["all", "x-login", "topics", "schedule", "status", "unschedule"])
    ap.add_argument("--answers", help="answers file from docs/onboarding.html (no secrets)")
    ap.add_argument("--skip-verify", action="store_true", help=argparse.SUPPRESS)  # tests only
    args = ap.parse_args(argv)
    answers = load_answers(args.answers)
    cfg = app.load_config()
    verify = not args.skip_verify
    try:
        if args.command == "all":
            return onboard(answers, verify)
        if args.command == "x-login":
            return 0 if step_x(cfg, verify) else 1
        if args.command == "topics":
            return 0 if step_topics(cfg, answers.get("topics"), answers.get("draft_definitions")) else 1
        if args.command == "schedule":
            return 0 if step_schedule(cfg, answers.get("schedule"), answers.get("batch_size")) else 1
        if args.command == "unschedule":
            say(scheduler.remove())
            return 0
        status()
        return 0
    except (KeyboardInterrupt, EOFError):
        say("\n  Stopped. Steps you finished are saved; run the same command again to finish.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
