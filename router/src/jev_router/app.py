"""Where one user's install keeps its settings, secrets, and records.

Everything user-specific lives in one data folder, outside the code:

    ~/.scatha/               (override with SCATHA_HOME)
      config.json            batch size, note writer, schedule, tool paths
      topics.yaml            the buckets: what belongs where, and which folder
      secrets.env            macOS/Linux only (mode 600); Windows uses user environment variables
      work/                  ledger, pending batch, per-run outputs, logs

The project name appears here and nowhere else in the code, so a rename is one edit.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

APP_NAME = "scatha"
HOME_DIRNAME = ".scatha"
HOME_ENV = "SCATHA_HOME"
TASK_NAME = "Scatha-Ingest"  # Windows Task Scheduler / launchd / systemd unit stem
REPO = Path(__file__).resolve().parents[3]  # the checkout: router/, smaug/, docs/

# Secrets, by the variable name the tools read. X cookies are stored under bird's
# namespaced fallbacks (TWITTER_*) so they don't collide with other tools; the
# runner also accepts plain AUTH_TOKEN / CT0 from older setups.
SECRETS = {
    "x_auth_token": "TWITTER_AUTH_TOKEN",
    "x_ct0": "TWITTER_CT0",
    "typesafe": "TYPESAFE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

DEFAULT_CONFIG = {
    "version": 1,
    "batch_size": 100,
    "notes_backend": "claude-code",  # or "anthropic-api"
    "taxonomy": "topics.yaml",
    "ntfy_topic": "",
    "reader_profile": "",  # a sentence about you; Haiku tailors how-to steps to it
    "schedule": {"cadence": "weekly", "day": "Saturday", "time": "00:01"},
    "tools": {"node": "node", "bird": "bird", "claude": "claude"},
    # Bookmarks that fit no topic: "light" notes (no model cost) in this folder, or "off" (ledger only).
    # An empty folder means "Unsorted" beside most of the topic folders.
    "unsorted": {"mode": "light", "folder": ""},
    "status_note": "",  # the vault note each run rewrites; empty: "scatha.md" beside the Unsorted folder
}


def home() -> Path:
    return Path(os.environ.get(HOME_ENV) or Path.home() / HOME_DIRNAME).expanduser()


def task_name() -> str:
    """The schedule's name. A non-default data folder gets its own, so a test install can't replace the real one."""
    default = Path.home() / HOME_DIRNAME
    if home().resolve() == default.resolve():
        return TASK_NAME
    return f"{TASK_NAME}-" + "".join(c for c in home().name if c.isalnum() or c in "-_").strip("-_")


def setup_command(sub: str = "") -> str:
    """How the user runs setup on this OS, from the project folder (there is no command on PATH)."""
    return ((r".\setup.ps1" if sys.platform == "win32" else "./setup.sh") + f" {sub}").rstrip()


def command(sub: str = "") -> str:
    """What to tell the user to type: `scatha <sub>` once the command is installed, else the long form that always works."""
    exe = APP_NAME + (".exe" if sys.platform == "win32" else "")
    if shutil.which(APP_NAME) or (Path.home() / ".local" / "bin" / exe).exists():
        return f"{APP_NAME} {sub}".strip()
    return f'uv run --project "{REPO / "router"}" {APP_NAME} {sub}'.strip()


def work() -> Path:
    return home() / "work"


def config_path() -> Path:
    return home() / "config.json"


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if config_path().exists():
        user = json.loads(config_path().read_text(encoding="utf-8"))
        for k, v in user.items():
            cfg[k] = cfg[k] | v if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    return cfg


def save_config(cfg: dict) -> Path:
    home().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return config_path()


def taxonomy_path(cfg: dict) -> Path:
    p = Path(cfg["taxonomy"]).expanduser()
    return p if p.is_absolute() else home() / p


# ---------- secrets ----------

def _secrets_file() -> Path:
    return home() / "secrets.env"


def save_secret(var: str, value: str) -> str:
    """Store a secret where the runner (and scheduled runs) can read it. Returns where it went."""
    os.environ[var] = value
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, var, 0, winreg.REG_SZ, value)
        return "your Windows user environment variables"
    path = _secrets_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if not l.startswith(var + "=")]
    lines.append(f"{var}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return str(path) + " (readable only by you)"


def load_secrets() -> None:
    """Fill os.environ from wherever save_secret put things, without overriding the live environment."""
    names = list(SECRETS.values()) + ["AUTH_TOKEN", "CT0"]
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for n in names:
                if not os.environ.get(n):
                    try:
                        os.environ[n] = winreg.QueryValueEx(key, n)[0]
                    except FileNotFoundError:
                        pass
    elif _secrets_file().exists():
        for line in _secrets_file().read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    # bird and Smaug read AUTH_TOKEN / CT0; bring the namespaced copies across
    os.environ.setdefault("AUTH_TOKEN", os.environ.get("TWITTER_AUTH_TOKEN", ""))
    os.environ.setdefault("CT0", os.environ.get("TWITTER_CT0", ""))
    if not os.environ["AUTH_TOKEN"]:
        os.environ.pop("AUTH_TOKEN")
    if not os.environ["CT0"]:
        os.environ.pop("CT0")


def has_secret(var: str) -> bool:
    load_secrets()
    return bool(os.environ.get(var))
