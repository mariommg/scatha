"""The `scatha` command.

    scatha                 what happened in the last runs, and what you can do about it
    scatha review          the sorting desk (sort Unsorted, check Jev's close calls)
    scatha discover        find topics your bookmarks need that you haven't named yet, and add them
                           (free: --pool lists those bookmarks; --last shows the last run again)
    scatha run             import a batch now (same as the schedule; --preview, --batch N)
    scatha setup [step]    the setup wizard (x-login, topics, schedule, status, secrets, unschedule)
    scatha uninstall       list what Scatha put on this machine, and remove only what you pick

Install once with `uv tool install --editable <repo>/router` (setup offers to); it then
works from any folder and follows this checkout.
"""

from __future__ import annotations

import sys

from . import app, report

HELP = __doc__.split("\n\n")[1]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "status"
    if cmd in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if cmd not in ("status", "review", "discover", "run", "setup", "uninstall"):
        print(f"Unknown command '{cmd}'.\n\n{HELP}")
        return 2

    if cmd == "uninstall":  # works on a half-finished install too
        from . import uninstall
        return uninstall.main(argv)
    if not app.config_path().exists() and cmd != "setup":
        print(f"{app.DISPLAY_NAME} isn't set up yet. To start, type `{app.command('setup')}`.")
        return 1
    if cmd in ("run", "setup"):
        line = report.banner()
        if line:
            print(line + "\n")

    if cmd == "status":
        from . import Taxonomy, unsorted
        cfg = app.load_config()
        ask = (lambda q: input(f"{q} [y/N] ").strip().lower() in ("y", "yes")) if sys.stdin.isatty() else None
        if unsorted.check(cfg, Taxonomy.load(app.taxonomy_path(cfg)), ask=ask)["recorded"]:
            print()
        print(report.text(cfg))
        report.write_note(cfg)
        report.mark_seen()
        return 0
    if cmd == "review":
        from . import onboard, review
        args = review_args(argv)
        report.mark_seen()
        return review.main(app.load_config(), args["rebuild"], args["import_file"], args["browser"], onboard.say)
    if cmd == "discover":
        from . import discover, onboard
        cfg = app.load_config()
        result = (discover.last(cfg, onboard.say) if "--last" in argv
                  else discover.run(cfg, onboard.say, pool_only="--pool" in argv))
        if result and sys.stdin.isatty():
            discover.decide_each(cfg, result, onboard)
        return 0
    if cmd == "run":
        from . import run
        return run.main(argv)
    from . import onboard
    return onboard.main(argv)


def review_args(argv: list[str]) -> dict:
    import argparse
    ap = argparse.ArgumentParser(prog=f"{app.APP_NAME} review")
    ap.add_argument("--rebuild", action="store_true", help="re-score every bookmark with the current topics")
    ap.add_argument("--import", dest="import_file", metavar="FILE", help="carry over answers from an older review file")
    ap.add_argument("--no-browser", action="store_true", help="print the link instead of opening a browser")
    a = ap.parse_args(argv)
    return {"rebuild": a.rebuild, "import_file": a.import_file, "browser": not a.no_browser}


if __name__ == "__main__":
    sys.exit(main())
