"""A copy of the data folder somewhere safer, after each run.

Config (config.json):
    backup_dir     where the zips go, ideally a synced or remote-backed folder ("" = off)
    backup_keep    how many dated zips to keep (default 8: two months of weekly runs)
    backup_extra   other files to copy alongside as-is, e.g. your own notes about the install

The zip holds everything in the data folder (settings, topics, ledger, runs, logs)
except secrets.env and the preview notes. Secrets never go into a backup.

    uv run --project router python -m jev_router.backup     # back up now
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from . import app

SKIP_NAMES = {"secrets.env", ".run.lock"}
SKIP_DIRS = {"notes-preview"}


def _skipped(rel: Path) -> bool:
    return rel.name in SKIP_NAMES or any(part in SKIP_DIRS for part in rel.parts)


def snapshot(cfg: dict) -> Path | None:
    """Writes a dated zip to backup_dir and prunes old ones. Returns the zip, or None when backups are off."""
    if not cfg.get("backup_dir"):
        return None
    dest = Path(cfg["backup_dir"]).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    home = app.home()
    name = f"{app.APP_NAME}-data_{dt.datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"
    # Build it elsewhere and move it in whole, so a sync client never uploads half a zip
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp) / name
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(home.rglob("*")):
                rel = f.relative_to(home)
                if f.is_file() and not _skipped(rel):
                    z.write(f, rel.as_posix())
        shutil.move(str(part), dest / name)
    for extra in cfg.get("backup_extra") or []:
        p = Path(extra).expanduser()
        if p.is_file():
            shutil.copy2(p, dest / p.name)
    keep = max(1, int(cfg.get("backup_keep", 8)))
    for old in sorted(dest.glob(f"{app.APP_NAME}-data_*.zip"))[:-keep]:
        old.unlink()
    return dest / name


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = app.load_config()
    path = snapshot(cfg)
    print(f"backed up to {path}" if path else "backups are off (set backup_dir in config.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
