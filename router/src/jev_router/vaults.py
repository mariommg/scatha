"""Find the Obsidian vaults this computer knows about.

Obsidian keeps its vault list in obsidian.json (one per OS location below). Vaults
are returned most recently opened first, and only if the folder still exists.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def registry_files() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        return [Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "obsidian/obsidian.json"]
    if sys.platform == "darwin":
        return [home / "Library/Application Support/obsidian/obsidian.json"]
    return [home / ".config/obsidian/obsidian.json",
            home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json"]  # Flatpak


def find_vaults() -> list[Path]:
    seen: dict[Path, int] = {}
    for reg in registry_files():
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for v in (data.get("vaults") or {}).values():
            p = Path(v.get("path", ""))
            if p.is_dir():
                seen[p] = max(seen.get(p, 0), int(v.get("ts") or 0))
    return sorted(seen, key=lambda p: -seen[p])


def is_vault(path: Path) -> bool:
    return (path / ".obsidian").is_dir()
