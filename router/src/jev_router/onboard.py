"""Setup wizard: from a fresh checkout to scheduled bookmark imports.

    uv run --project router python -m jev_router.onboard              # the whole thing
    uv run --project router python -m jev_router.onboard --answers my-setup.json
    uv run --project router python -m jev_router.onboard x-login      # fresh X cookies only
    uv run --project router python -m jev_router.onboard topics       # redo topics and folders
    uv run --project router python -m jev_router.onboard schedule     # change when it runs
    uv run --project router python -m jev_router.onboard status       # what's set up, what isn't
    uv run --project router python -m jev_router.onboard unschedule
    uv run --project router python -m jev_router.onboard review       # sorting desk: check Jev's calls (--rebuild)

(setup.ps1 / setup.sh run the same thing; extra words pass through.)

Secrets are typed into hidden prompts, checked against the real service, and only
then stored (Windows: your user environment variables; macOS/Linux: a file in the
data folder only you can read). They never go into config.json, the answers file,
or a log.

Prompt conventions: the question comes before its options; Enter takes the answer
in [brackets]; '-' answers "none" wherever none is allowed.
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

from . import app, checks, scheduler, topics, vaults
from .checks import verify_notes, verify_typesafe, verify_x

# ---------- output ----------

COLOR = False


def enable_color() -> bool:
    """ANSI colors only for a real terminal, and never with NO_COLOR set (https://no-color.org)."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":  # old consoles need VT processing switched on
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h, mode = k.GetStdHandle(-11), ctypes.c_uint32()
            return bool(k.GetConsoleMode(h, ctypes.byref(mode)) and k.SetConsoleMode(h, mode.value | 0x0004))
        except Exception:
            return False
    return True


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def bold(t: str) -> str: return paint(t, "1")
def dim(t: str) -> str: return paint(t, "2")
def green(t: str) -> str: return paint(t, "32")
def red(t: str) -> str: return paint(t, "31")
def yellow(t: str) -> str: return paint(t, "33")
def cyan(t: str) -> str: return paint(t, "1;36")


def say(text: str = "") -> None:
    print(text, flush=True)


def ok(text: str) -> None:
    say(f"  {green('✓')} {text}")


def bad(text: str) -> None:
    say(f"  {red('✗')} {text}")


def hint(text: str) -> None:
    say(dim(f"  {text}"))


def heading(n: int, total: int, title: str) -> None:
    say()
    say(cyan(f"Step {n} of {total}  {title}"))
    say(dim("─" * 64))


def question(text: str) -> None:
    say()
    say(f"  {bold(text)}")


setup_cmd = app.setup_command


def table(header: list[str], rows: list[list[str]], marks: list[str] | None = None, indent: int = 4) -> None:
    """Aligned columns; color goes on after padding so it can't throw the widths off."""
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    line = lambda r: " " * indent + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    say(dim(line(header)))
    for i, r in enumerate(rows):
        mark = (marks or [""] * len(rows))[i]
        say(line(r) + (f"  {yellow(mark)}" if mark else ""))


# ---------- prompts ----------

def ask(prompt: str, default: str | None = None, clearable: bool = False) -> str:
    """Enter takes the default. With clearable, '-' means none and comes back as ''."""
    shown = f" {dim('[' + default + ']')}" if default else ""
    while True:
        v = input(f"  {prompt}{shown}: ").strip()
        v = v or (default or "")
        if clearable and v == "-":
            return ""
        if v or default is not None or clearable:  # an optional question: Enter with nothing means none
            return v
        say(dim("  (I need an answer here.)"))


def yes(prompt: str, default: bool = True) -> bool:
    v = input(f"  {prompt} {dim('[Y/n]' if default else '[y/N]')}: ").strip().lower()
    return default if not v else v.startswith("y")


def choose(prompt: str, options: list[str], default: int = 1) -> int:
    question(prompt)
    for i, o in enumerate(options, 1):
        say(f"    {i}  {o}")
    while True:
        v = input(f"  Number {dim(f'[{default}]')}: ").strip() or str(default)
        if v.isdigit() and 1 <= int(v) <= len(options):
            return int(v)
        say(dim(f"  (Pick a number from 1 to {len(options)}.)"))


def read_hidden(prompt: str, getch, write) -> str:
    """Hidden input that still shows a paste landed: a running character count on the prompt
    line, so a paste that didn't take (or took twice) is visible before Enter."""
    chars: list[str] = []

    def redraw():
        n = len(chars)
        write(f"\r{prompt}{'*' * min(n, 12)}{f' ({n} characters)' if n else ''}   ")

    redraw()
    while True:
        c = getch()
        if c in ("\r", "\n", ""):
            break
        if c == "\x03":
            raise KeyboardInterrupt
        if c == "\x1b":  # an escape sequence (arrow keys, bracketed-paste markers): skip it whole
            c = getch()
            if c == "[":
                while not (c := getch()).isalpha() and c != "~":
                    pass
            continue
        if c in ("\x00", "\xe0"):  # Windows: arrow and function keys arrive as two characters
            getch()
            continue
        if c in ("\x7f", "\b"):
            if chars:
                chars.pop()
        elif c.isprintable():
            chars.append(c)
        redraw()
    write("\r\n")
    return "".join(chars)


def hidden_input(prompt: str) -> str:
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)
    out = lambda s: (sys.stdout.write(s), sys.stdout.flush())
    if sys.platform == "win32":
        import msvcrt
        return read_hidden(prompt, msvcrt.getwch, out)
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)  # keystrokes one at a time, nothing echoed; Ctrl+C arrives as \x03
        return read_hidden(prompt, lambda: sys.stdin.read(1), out)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def secret(prompt: str) -> str:
    while True:
        v = hidden_input(f"  {prompt} (hidden; paste, then Enter): ").strip()
        half = len(v) // 2
        if v and len(v) % 2 == 0 and v[:half] == v[half:]:
            say(dim(f"  (That looks like the same thing pasted twice; using it once: {half} characters.)"))
            v = v[:half]
        if v:
            return v
        say(dim("  (Nothing came through. Paste it and press Enter.)"))


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


# ---------- steps ----------

def step_prereqs(cfg: dict) -> bool:
    ok_ = True
    node = which("node")
    if not node or node_major(node) < 22:
        bad("Node.js 22 or newer is needed (it runs Smaug). Get it at https://nodejs.org, then run setup again.")
        ok_ = False
    else:
        cfg["tools"]["node"] = node
        ok(f"Node.js {node_major(node)}")
    smaug = app.REPO / "smaug"
    if ok_ and not (smaug / "node_modules").is_dir():
        hint("… installing Smaug's packages (one time)")
        subprocess.run([which("npm") or "npm", "install", "--silent"], cwd=smaug, check=False)
    bird = which("bird")
    if not bird and ok_:
        say()
        say("  Optional: bird, a command-line X tool, as a backup. Smaug reads X with its own copy of")
        say("  bird's code; if that ever fails, an installed bird is tried instead. bird 0.8.0 is the")
        say("  last release (its author stopped maintaining it), so it's a backup, not a fix.")
        if yes("Install bird 0.8.0 with npm as a backup?", default=False):
            subprocess.run([which("npm") or "npm", "install", "-g", "@steipete/bird@0.8.0"], check=False)
            bird = which("bird")
    if bird:
        cfg["tools"]["bird"] = bird
        ok("bird (backup)")
    return ok_


def step_x(cfg: dict, verify: bool = True) -> bool:
    say("  Your bookmarks come from X through your browser session. Two cookies do it:")
    say()
    say("    1. In your browser, open x.com (logged in).")
    say("    2. Open developer tools (F12) → Application (Storage in Firefox) → Cookies → https://x.com")
    say("    3. Copy the values of auth_token and ct0.")
    say()
    hint("They're the keys to your X session, so treat them like a password. They stop working")
    hint(f"when you log out of X; when that happens, run:  {setup_cmd('x-login')}")
    handle = verify_x(cfg) if verify and app.has_secret("AUTH_TOKEN") else None
    if handle:
        say()
        if not yes(f"Saved cookies still work (X sees {handle}). Replace them?", default=False):
            ok("Keeping the saved cookies.")
            return True
    for attempt in range(3):
        say()
        token = secret("auth_token")
        ct0 = secret("ct0")
        os.environ["AUTH_TOKEN"], os.environ["CT0"] = token, ct0
        handle = verify_x(cfg) if verify else "(not checked)"
        if handle:
            where = app.save_secret(app.SECRETS["x_auth_token"], token)
            app.save_secret(app.SECRETS["x_ct0"], ct0)
            ok(f"X sees you as {handle}. Saved to {where}.")
            return True
        bad("X didn't accept those. Double-check you copied the values, not the names, and try again.")
    return False


def step_typesafe(verify: bool = True) -> bool:
    say("  Jev (from TypeSafe) sorts each bookmark into your topics. It's cheap: a few cents covers")
    say("  hundreds of bookmarks. Get an API key at https://typesafe.ai (dashboard → API keys).")
    say()
    if app.has_secret(app.SECRETS["typesafe"]) and verify and not verify_typesafe():
        if not yes("A working TypeSafe key is already saved. Replace it?", default=False):
            ok("Keeping the saved key.")
            return True
    for _ in range(3):
        os.environ[app.SECRETS["typesafe"]] = key = secret("TypeSafe API key")
        err = verify_typesafe() if verify else None
        if not err:
            ok(f"Jev answered. Saved to {app.save_secret(app.SECRETS['typesafe'], key)}.")
            return True
        bad(f"That key didn't work ({err[:120]}). Try again?")
    return False


def step_notes(cfg: dict, preset: str | None = None, verify: bool = True) -> bool:
    say("  Claude Haiku writes each note: summary, key points, quotes, translation, reading images.")
    claude = which("claude")
    options = ["Claude Code, headless " + dim("(uses your Claude plan; no API key needed)"),
               "Anthropic API key " + dim("(pay as you go, roughly a cent per note)")]
    if preset:
        pick = 1 if preset == "claude-code" else 2
    else:
        pick = choose("How should Haiku be reached?", options, default=1 if claude else 2)
    if pick == 1:
        if not claude:
            bad("I can't find the 'claude' command. Install Claude Code (https://claude.com/claude-code),")
            say("    log in once by running 'claude', then run setup again. Or pick the API option.")
            return False
        cfg["notes_backend"], cfg["tools"]["claude"] = "claude-code", claude
    else:
        cfg["notes_backend"] = "anthropic-api"
        if not app.has_secret(app.SECRETS["anthropic"]):
            hint("Get a key at https://console.anthropic.com (Settings → API keys).")
            os.environ[app.SECRETS["anthropic"]] = secret("Anthropic API key")
    while err := verify_notes(cfg) if verify else None:
        bad(f"Haiku didn't answer: {checks.notes_error_in_words(err, cfg['notes_backend'])}")
        # Usually fixable without leaving: log in again in another terminal, then retry here
        if cfg["notes_backend"] != "claude-code" or not yes("Fixed it in another terminal? Try again", default=True):
            return False
    if cfg["notes_backend"] == "anthropic-api":
        app.save_secret(app.SECRETS["anthropic"], os.environ[app.SECRETS["anthropic"]])
    ok("Haiku answered.")
    return True


# ----- topics and folders -----

def pick_topics() -> list[dict]:
    names = list(topics.PRESETS)
    question("Which topics do you want?")
    hint("A bookmark goes to the topic it fits best; one that fits none of them is skipped.")
    hint("Start from these (the first five are tuned); you can add your own next.")
    say()
    for i, n in enumerate(names, 1):
        say(f"    {i:>2}  {n}")
    picks = ask("Numbers, comma-separated (- for none)", default="1,2,3,4,5", clearable=True)
    chosen = [names[int(x) - 1] for x in re.findall(r"\d+", picks) if 0 < int(x) <= len(names)]
    out = [{"label": n, "description": topics.PRESETS[n].get("description") or topics.PRESETS[n]["yes_means"],
            "preset": True} for n in chosen]
    say()
    while yes("Add a topic of your own?", default=not out):
        label = ask("Name (short, like 'Sports')")
        desc = ask("In one line, what belongs here?")
        out.append({"label": label, "description": desc})
        say()
    return out


def vault_of(dest: Path, found: list[Path]) -> Path | None:
    return next((v for v in found if dest == v or dest.is_relative_to(v)), None)


def split_destination(dest: Path, found: list[Path]) -> tuple[str, str]:
    """(vault or folder, subfolder) for display: the vault's name when it's a known vault."""
    v = vault_of(dest, found)
    if v is None:
        return str(dest.parent), dest.name
    rel = dest.relative_to(v)
    return v.name, "" if str(rel) == "." else str(rel)


def vault_options(found: list[Path]) -> list[str]:
    width = max((len(v.name) for v in found), default=0)
    return [f"{v.name.ljust(width)}  {dim(str(v))}" for v in found] + ["Another folder (type a path)"]


def pick_base(found: list[Path], prompt: str, current: Path | None = None) -> Path:
    """A vault from the list, or any folder."""
    if not found:
        return Path(ask("Folder path", default=str(current) if current else None)).expanduser()
    cur = vault_of(current, found) if current else None
    pick = choose(prompt, vault_options(found), default=found.index(cur) + 1 if cur else 1)
    if pick <= len(found):
        return found[pick - 1]
    return Path(ask("Folder path", default=str(current.parent) if current and not cur else None)).expanduser()


def folder_name(label: str) -> str:
    """A topic's default folder: its words run together, each capitalized ("General knowledge" ->
    "GeneralKnowledge"), so paths need no quoting. Letters in any script survive."""
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[^\W_]+", label)) or "Notes"


def pick_subfolder(base: Path, label: str, current: Path | None = None) -> Path:
    """Enter keeps the suggestion (the current subfolder, else the topic's folder name); '-' means none."""
    suggestion = folder_name(label)
    if current is not None and current.is_relative_to(base):
        rel = str(current.relative_to(base))
        suggestion = "-" if rel == "." else rel
    sub = ask(f"Subfolder inside {base.name or base} (- for none)", default=suggestion, clearable=True)
    return base / sub if sub else base


def review_destinations(ts: list[dict], found: list[Path]) -> None:
    """Show every topic's folder and let the user fix any row before anything is saved."""
    while True:
        question("Here's where each topic's notes will go:")
        rows, marks = [], []
        for i, t in enumerate(ts, 1):
            dest = Path(t["destination"])
            where, sub = split_destination(dest, found)
            rows.append([str(i), t["label"], where, sub or "(none)"])
            marks.append("" if dest.is_dir() else "(new)")
        table(["#", "Topic", "Vault or folder", "Subfolder"], rows, marks)
        if any(marks):
            hint("(new) folders don't exist yet; they're made when the first note lands. Check the spelling.")
        v = input(f"  Enter to save, or a number to change one: ").strip()
        if not v:
            return
        if not (v.isdigit() and 1 <= int(v) <= len(ts)):
            say(dim(f"  (Press Enter, or pick a number from 1 to {len(ts)}.)"))
            continue
        t = ts[int(v) - 1]
        current = Path(t["destination"])
        base = pick_base(found, f"Where should '{t['label']}' notes go?", current)
        t["destination"] = str(pick_subfolder(base, t["label"], current))


def step_topics(cfg: dict, preset_topics: list[dict] | None = None, draft: bool | None = None) -> bool:
    if preset_topics is None:
        preset_topics = pick_topics()
        if not preset_topics:
            bad("You need at least one topic.")
            return False

    needs_definition = [t for t in preset_topics if "question" not in topics.PRESETS.get(t["label"], {})]
    if draft is None:
        draft = bool(needs_definition) and yes(
            "Have Haiku turn your one-liners into definitions Jev reads well? (recommended)")

    # Destinations: one place for all, each topic in its own subfolder, then a review where any row can change.
    found = vaults.find_vaults()
    for t in preset_topics:
        if t.get("destination"):
            t["destination"] = str(Path(t["destination"]).expanduser())
    if any(not t.get("destination") for t in preset_topics):
        if not found:
            question("Where should your notes go?")
            hint("No Obsidian vaults found; notes are plain Markdown, so any folder works.")
            base = pick_base(found, "")
        else:
            base = pick_base(found, "Which Obsidian vault should your notes go to?")
        hint("Each topic gets a subfolder named after it. You can change any of them next.")
        for t in preset_topics:
            if not t.get("destination"):
                t["destination"] = str(base / folder_name(t["label"]))
    review_destinations(preset_topics, found)

    # Definitions
    built = []
    labels = [t["label"] for t in preset_topics]
    say()
    for t in preset_topics:
        p = topics.PRESETS.get(t["label"], {})
        if t.get("preset") and "question" in p:
            d = {k: p[k] for k in ("question", "yes_means", "no_means")}
        elif draft:
            hint(f"… drafting '{t['label']}'")
            d = topics.draft_definition(t["label"], t["description"], [x for x in labels if x != t["label"]])
        else:
            d = topics.template_definition(t["label"], t["description"])
        built.append({"label": t["label"], **d, "destination": t["destination"]})
    path = topics.write_taxonomy(built, app.taxonomy_path(cfg))
    ok(f"{len(built)} topics saved to {path}")
    hint("Plain YAML. Open it any time to adjust wording or folders.")
    return True


def step_schedule(cfg: dict, preset: dict | None = None, batch: int | None = None) -> bool:
    if preset:
        cfg["schedule"] = preset
    else:
        pick = choose("How often should it run?", ["Weekly " + dim("(recommended)"), "Every other week", "Daily"])
        cadence = {1: "weekly", 2: "biweekly", 3: "daily"}[pick]
        day = "Saturday"
        if cadence != "daily":
            day = scheduler.DAYS[choose("Which day?", scheduler.DAYS, default=7) - 1]
        say()
        time = ask("What time (24-hour HH:MM)?", default="00:01")
        cfg["schedule"] = {"cadence": cadence, "day": day, "time": time}
    try:
        scheduler.validate(cfg["schedule"])
    except ValueError as e:
        bad(str(e))
        return False
    if batch is None:
        question("How many bookmarks per run?")
        hint("Each run takes up to this many unprocessed bookmarks, newest first. A big backlog clears a batch")
        hint("per run. (For one Claude Pro user, 100 notes used about a quarter of a 5-hour window.)")
        batch = int(ask("Batch size", default=str(cfg["batch_size"])))
    cfg["batch_size"] = batch
    app.save_config(cfg)
    say()
    try:
        ok(scheduler.install(cfg))
    except Exception as e:
        bad(f"Couldn't register the schedule: {e}")
        return False
    if sys.platform == "win32":
        hint("It runs while you're logged in (a locked screen is fine) and wakes the PC if it's asleep.")
    elif sys.platform != "darwin" and scheduler.lingering() is False:
        hint("It only runs while you're logged in. On a server, keep it running after you log out with:")
        hint("  loginctl enable-linger $USER")
    return True


def step_ntfy(cfg: dict, preset: str | None = None) -> None:
    if preset is not None:
        cfg["ntfy_topic"] = preset
    else:
        question("Want a phone ping after each run? (optional)")
        hint("Like \"23 new notes, 4 skipped\", through ntfy.sh. Install the ntfy app, subscribe to a topic")
        hint("name nobody would guess, and enter it here.")
        current = cfg.get("ntfy_topic") or None
        cfg["ntfy_topic"] = ask("ntfy topic (- for none)" if current else "ntfy topic (Enter to skip)",
                                default=current, clearable=True)
    app.save_config(cfg)


def step_reader(cfg: dict, preset: str | None = None) -> None:
    if preset is not None:
        cfg["reader_profile"] = preset
    else:
        question("Tell Haiku about you? (optional)")
        hint("One sentence, so how-to steps fit your setup.")
        hint("(Like: 'I work in Claude Code on Windows 11 and keep notes in Obsidian.')")
        current = cfg.get("reader_profile") or None
        cfg["reader_profile"] = ask("About you (- for none)" if current else "About you (Enter to skip)",
                                    default=current, clearable=True)
    app.save_config(cfg)


def step_test(cfg: dict) -> None:
    if not yes("Try it now on your 5 newest bookmarks? Notes go to a preview folder, not your vaults."):
        return
    hint("Running the scheduled script itself, in a fresh shell, the way the scheduler will (a minute or two)…")
    code = scheduler.run_now(["--preview", "--batch", "5", "--force"])
    logs = sorted((app.work() / "logs").glob("*.log"))
    lines = logs[-1].read_text(encoding="utf-8-sig", errors="replace").splitlines() if logs else []
    for line in lines:
        if "summary:" in line or "pre-flight:" in line:
            say(f"    {line.split('] ', 1)[-1]}")
    say()
    if code:
        bad(f"The test run stopped (exit {code}). The log says why: {logs[-1] if logs else app.work() / 'logs'}")
        return
    ok(f"The scheduled script works. Preview notes: {app.work() / 'notes-preview'}")
    hint("Nothing was recorded, so the first real run picks these up again.")


# ---------- where things stand ----------

STEPS = {1: "Tools", 2: "X login", 3: "Jev (sorting)", 4: "Haiku (writing notes)",
         5: "Topics and folders", 6: "Schedule", 7: "Test drive"}


def assess(cfg: dict) -> dict[int, tuple[bool, str]]:
    """Steps 1-6: (working?, what's set). The three logins get a live check, so this takes a few seconds."""
    app.load_secrets()
    s: dict[int, tuple[bool, str]] = {}
    node = checks.found(cfg["tools"]["node"])
    major = node_major(node) if node else 0
    s[1] = ((True, f"Node.js {major}" + (", bird backup" if checks.found(cfg["tools"]["bird"]) else ""))
            if major >= 22 else (False, f"Node.js 22+ not found at {cfg['tools']['node']}"))

    if not app.has_secret("AUTH_TOKEN"):
        s[2] = (False, "not set up yet")
    elif not s[1][0]:
        s[2] = (False, "can't check without Node.js (step 1)")
    else:
        handle = verify_x(cfg)
        s[2] = (True, f"X sees {handle}") if handle else (False, "saved cookies stopped working (logged out of X?)")

    if not app.has_secret(app.SECRETS["typesafe"]):
        s[3] = (False, "not set up yet")
    else:
        err = verify_typesafe()
        s[3] = (True, "key works") if not err else (False, f"key rejected ({err[:60]})")

    writer = "Claude Code" if cfg["notes_backend"] == "claude-code" else "Anthropic API"
    if cfg["notes_backend"] == "anthropic-api" and not app.has_secret(app.SECRETS["anthropic"]):
        s[4] = (False, "not set up yet")
    elif cfg["notes_backend"] == "claude-code" and cfg["tools"]["claude"] == "claude":
        s[4] = (False, "not set up yet")  # step 4 records where claude is; the bare default means it never finished
    else:
        err = verify_notes(cfg)
        extra = "; about you: set" if cfg.get("reader_profile") else ""
        s[4] = (True, f"{writer}, answering{extra}") if not err else (False, f"{writer}: {err[:80]}")

    tax = app.taxonomy_path(cfg)
    if tax.exists():
        from . import Taxonomy
        labels = [t.label or t.key for t in Taxonomy.load(tax).topics]
        s[5] = (True, f"{len(labels)}: {', '.join(labels)}")
    else:
        s[5] = (False, "not set up yet")

    there = scheduler.installed()
    when = f"{scheduler.describe(cfg['schedule'])}, {cfg['batch_size']} per run"
    s[6] = ((True, when) if there else (True, when + " (crontab; can't check)") if there is None
            else (False, "not scheduled"))
    return s


def show_recap(states: dict[int, tuple[bool, str]]) -> None:
    width = max(len(STEPS[n]) for n in states)
    for n, (good, detail) in states.items():
        mark = green("✓") if good else red("✗")
        say(f"    {mark} {n}  {STEPS[n].ljust(width)}  {detail if good else red(detail)}")


def run_step(n: int, cfg: dict, answers: dict, verify: bool) -> bool:
    heading(n, len(STEPS), STEPS[n])
    if n == 1:
        good = step_prereqs(cfg)
        app.save_config(cfg)
        return good
    if n == 2:
        return step_x(cfg, verify)
    if n == 3:
        return step_typesafe(verify)
    if n == 4:
        if not step_notes(cfg, answers.get("notes_backend"), verify):
            return False
        app.save_config(cfg)
        step_reader(cfg, answers.get("reader_profile"))
        return True
    if n == 5:
        return step_topics(cfg, answers.get("topics"), answers.get("draft_definitions"))
    if n == 6:
        step_ntfy(cfg, answers.get("ntfy_topic"))
        return step_schedule(cfg, answers.get("schedule"), answers.get("batch_size"))
    step_test(cfg)
    return True


# ---------- commands ----------

def status() -> None:
    cfg = app.load_config()
    say(bold(f"{app.APP_NAME} status"))
    say(f"  data folder   {app.home()}")
    if not app.config_path().exists():
        bad(f"Not set up yet. From {app.REPO}, run {setup_cmd()}")
        return
    hint("Checking the logins (a few seconds)…")
    show_recap(assess(cfg))
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
    for bad_key in ("auth_token", "ct0", "typesafe_api_key", "anthropic_api_key"):
        if bad_key in a:
            raise SystemExit("That answers file contains a secret. Delete it and make a new one; "
                             "setup always asks for secrets in hidden prompts.")
    return a


def onboard(answers: dict, verify: bool = True) -> int:
    """First time: every step in order. After that: a recap, then only the steps that are
    missing or broken (a stopped setup resumes; an expired login gets fixed), or, when
    everything works, a menu to change any one step."""
    cfg = app.load_config()
    say()
    say(cyan(f"{app.APP_NAME} setup"))
    say("Your X bookmarks, sorted into your topics and written up as notes, on a schedule.")
    say(f"Everything you set here lives in {app.home()} (secrets excepted; I'll say where each one goes).")
    hint("Enter takes the answer in [brackets]. Ctrl+C stops; run setup again to pick up where you left off.")

    todo = list(range(1, 7))
    if app.config_path().exists():
        question("Already set up")
        hint("Checking what works (a few seconds)…")
        states = assess(cfg)
        show_recap(states)
        todo = [n for n, (good, _) in states.items() if not good]
        if not todo:
            say()
            ok("Everything works.")
            return change_menu(cfg, answers, verify)
        say()
        say(f"  Picking up at step {todo[0]} ({STEPS[todo[0]]}). Steps marked ✓ stay as they are.")
    for n in todo:
        if not run_step(n, cfg, answers, verify):
            return 1
    run_step(7, cfg, answers, verify)
    say()
    say(cyan("All set."))
    say(f"  Handy later, from {app.REPO}:")
    say()
    table(["Command", "What it does"], [
        [setup_cmd(), "check everything; fix what's broken, change anything"],
        [setup_cmd("status"), "check everything, change nothing; newest log"],
        [setup_cmd("x-login"), "fresh X cookies, straight away"],
    ])
    say()
    hint("The runbook (docs/RUNBOOK.md) covers the rest.")
    return 0


def change_menu(cfg: dict, answers: dict, verify: bool) -> int:
    while True:
        say()
        v = input("  Change something? A step number (1-7, 7 is a test drive), or Enter to finish: ").strip()
        if not v:
            return 0
        if v.isdigit() and int(v) in STEPS:
            run_step(int(v), cfg, answers, verify)
        else:
            say(dim("  (A number from 1 to 7, or Enter.)"))


def main(argv: list[str] | None = None) -> int:
    global COLOR
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    COLOR = enable_color()
    ap = argparse.ArgumentParser(prog=setup_cmd())
    ap.add_argument("command", nargs="?", default="all",
                    choices=["all", "x-login", "topics", "schedule", "status", "unschedule", "review"])
    ap.add_argument("--answers", help="answers file from docs/onboarding.html (no secrets)")
    ap.add_argument("--rebuild", action="store_true", help="review: re-score every bookmark with the current topics")
    ap.add_argument("--import", dest="import_file", metavar="FILE", help="review: carry over answers from an older review file")
    ap.add_argument("--no-browser", action="store_true", help="review: print the link instead of opening a browser")
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
        if args.command == "review":
            from . import review
            return review.main(cfg, args.rebuild, args.import_file, not args.no_browser, say)
        if args.command == "unschedule":
            say(scheduler.remove())
            return 0
        status()
        return 0
    except (KeyboardInterrupt, EOFError):
        say(f"\n  Stopped. Steps you finished are saved; run {setup_cmd()} again to finish.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
