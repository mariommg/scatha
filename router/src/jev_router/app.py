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
#
# On Windows they go in Windows Credential Manager, where only programs that ask can read them;
# user environment variables (handed to every program you start) are the fallback, and where
# earlier installs kept them. Elsewhere: secrets.env in the data folder, readable only by you
# (a Linux timer at 00:01 can't count on an unlocked keyring). Reading order: the live
# environment, then Credential Manager, then the plain store.

KEYCHAIN = "Windows Credential Manager"
LEGACY_X = {"AUTH_TOKEN": "TWITTER_AUTH_TOKEN", "CT0": "TWITTER_CT0"}  # older setups' names -> ours


def keychain_service() -> str:
    """One entry per secret for every install, like the environment variables before it: a test install
    with entries of its own would still delete the shared plain copy the real install reads."""
    return APP_NAME


def _keychain():
    """The keyring backend scatha uses, or None (not Windows, or no usable backend)."""
    if sys.platform != "win32":
        return None
    try:
        import keyring
        kr = keyring.get_keyring()
    except Exception:
        return None
    return kr if getattr(kr, "priority", 0) >= 1 else None


def keychain_get(var: str) -> str | None:
    kc = _keychain()
    try:
        return kc.get_password(keychain_service(), var) if kc else None
    except Exception:
        return None


def keychain_set(var: str, value: str) -> bool:
    """Store, then read back. False when there's no keychain or it didn't keep the value."""
    kc = _keychain()
    if kc is None:
        return False
    try:
        kc.set_password(keychain_service(), var, value)
        return kc.get_password(keychain_service(), var) == value
    except Exception:
        return False


def keychain_delete(var: str) -> None:
    _keychain().delete_password(keychain_service(), var)


def stored_in_keychain(names: list[str]) -> list[str]:
    return [n for n in names if keychain_get(n)]


def _secrets_file() -> Path:
    return home() / "secrets.env"


def _set_user_var(var: str, value: str) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, var, 0, winreg.REG_SZ, value)


def _user_var(var: str) -> str | None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            return winreg.QueryValueEx(key, var)[0]
        except FileNotFoundError:
            return None


def save_secret(var: str, value: str) -> str:
    """Store a secret where the runner (and scheduled runs) can read it. Returns where it went."""
    os.environ[var] = value
    if keychain_set(var, value):
        if stored_user_vars([var]):  # a plain copy reaches every new process first, and may go stale
            delete_user_var(var)
            os.environ[var] = value
        return KEYCHAIN
    if sys.platform == "win32":
        _set_user_var(var, value)
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
    for n in SECRETS.values():
        if not os.environ.get(n) and (v := keychain_get(n)):
            os.environ[n] = v
    names = list(SECRETS.values()) + list(LEGACY_X)
    if sys.platform == "win32":
        for n in names:
            if not os.environ.get(n) and (v := _user_var(n)):
                os.environ[n] = v
    elif _secrets_file().exists():
        for line in _secrets_file().read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    # bird and Smaug read AUTH_TOKEN / CT0. Our namespaced copy wins: a plain one an older setup
    # left behind goes stale the first time the X login is redone.
    for plain, ours in LEGACY_X.items():
        if os.environ.get(ours):
            os.environ[plain] = os.environ[ours]
        elif not os.environ.get(plain):
            os.environ.pop(plain, None)


def secret_home(var: str) -> str | None:
    """Where a secret is kept, in words, or None if it isn't."""
    if keychain_get(var):
        return KEYCHAIN
    if stored_user_vars([var]):
        return "a user environment variable"
    if _secrets_file().exists() and any(l.startswith(var + "=") for l in _secrets_file().read_text().splitlines()):
        return "secrets.env"
    return "this terminal only" if os.environ.get(var) else None


def plain_secrets() -> dict[str, list[str]]:
    """What `setup secrets` can still do, so the status hint and the step agree: our secrets still in
    user environment variables (move), older X names with no Credential Manager copy (copy), and
    older X names left beside a copy (leftover: only the user can say whether other tools need them)."""
    if _keychain() is None:
        return {"move": [], "copy": [], "leftover": []}
    move = stored_user_vars(list(SECRETS.values()))
    legacy = stored_user_vars(list(LEGACY_X))
    copied = lambda p: LEGACY_X[p] in move or keychain_get(LEGACY_X[p])
    return {"move": move, "copy": [p for p in legacy if not copied(p)], "leftover": [p for p in legacy if copied(p)]}


def move_to_keychain(var: str, as_name: str | None = None) -> bool:
    """Copy a user environment variable into Credential Manager (under our name for a legacy X one),
    check it took, then delete the plain copy, except a legacy name's: other X tools read it,
    so that's the user's call (unset_user_var)."""
    value, name = _user_var(var), as_name or var
    if not value or not keychain_set(name, value):
        return False
    if var not in LEGACY_X:
        delete_user_var(var)
    return True


def stored_user_vars(names: list[str]) -> list[str]:
    """Which of these are saved as Windows user environment variables (none elsewhere: secrets.env is in the data folder)."""
    return [n for n in names if _user_var(n) is not None] if sys.platform == "win32" else []


def delete_user_var(var: str) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, var)
    os.environ.pop(var, None)


def haiku_env(cfg: dict) -> None:
    """Point notes.haiku() at this install's note writer (for Haiku calls outside a run)."""
    load_secrets()
    os.environ["CLAUDE_BIN"] = cfg["tools"]["claude"]
    os.environ["NOTES_BACKEND"] = cfg["notes_backend"]
    os.environ["READER_PROFILE"] = cfg.get("reader_profile", "")


def has_secret(var: str) -> bool:
    load_secrets()
    return bool(os.environ.get(var))
