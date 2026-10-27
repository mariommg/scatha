"""Uninstall surfaces; the user selects.

What these pin: nothing is removed unless picked; notes are never removable; the data folder
is only removed when it's scatha's and nothing else, so a SCATHA_HOME pointed at a vault (or
home) can't take that folder with it; what's picked goes and the rest stays; the copy kept
before deleting the data folder holds topics and answers but no secrets or bookmark text; one
stuck item doesn't stop the others. Nothing here touches the real schedule, registry, or
checkout: those are faked.
"""

import shutil
from pathlib import Path

import pytest

from jev_router import app, scheduler, topics, uninstall


@pytest.fixture
def world(tmp_path, monkeypatch):
    data, vault = tmp_path / "data", tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "AI").mkdir()
    (vault / "AI" / "a note.md").write_text("mine")
    (vault / "scatha.md").write_text("---\ntags: [scatha]\n---\n# scatha\n")
    monkeypatch.setenv(app.HOME_ENV, str(data))
    ts = [{"label": "AI", **{k: topics.PRESETS["AI"][k] for k in ("question", "yes_means", "no_means")},
           "destination": str(vault / "AI")}]
    data.mkdir()
    topics.write_taxonomy(ts, data / "topics.yaml")
    app.save_config({"taxonomy": "topics.yaml", "status_note": str(vault / "scatha.md")})
    (data / "secrets.env").write_text("TYPESAFE_API_KEY=x\n")
    (data / "work").mkdir()
    (data / "work" / "ingested.md").write_text("# ledger\n")
    (data / "tuning").mkdir()
    (data / "tuning" / "verdicts.json").write_text("{}")

    repo = tmp_path / "repo"
    (repo / "smaug" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(app, "REPO", repo)
    gone = []
    monkeypatch.setattr(scheduler, "installed", lambda: True)
    monkeypatch.setattr(scheduler, "others", lambda: ["Scatha-Ingest-test"])
    monkeypatch.setattr(scheduler, "remove", lambda task=None: gone.append(task or "mine") or "removed")
    env = {"AUTH_TOKEN": "a", "TYPESAFE_API_KEY": "t"}
    monkeypatch.setattr(app, "stored_user_vars", lambda names: [n for n in names if n in env])
    monkeypatch.setattr(app, "delete_user_var", lambda v: env.pop(v))
    monkeypatch.setattr(app, "stored_in_keychain", lambda names: [])
    monkeypatch.setattr(uninstall, "_tool_installed", lambda: False)
    monkeypatch.setattr(uninstall.shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return {"data": data, "vault": vault, "repo": repo, "gone": gone, "env": env, "tmp": tmp_path}


def everything_there(w):
    return (w["data"].is_dir() and (w["vault"] / "AI" / "a note.md").exists() and (w["vault"] / "scatha.md").exists()
            and (w["repo"] / "smaug" / "node_modules").is_dir() and w["env"] == {"AUTH_TOKEN": "a", "TYPESAFE_API_KEY": "t"}
            and w["gone"] == [])


def test_the_list_shows_everything_and_removes_nothing(world, capsys):
    assert uninstall.main([]) == 0
    out = capsys.readouterr().out
    for shown in ("schedule", "Scatha-Ingest-test", str(world["data"]), "AUTH_TOKEN", "TYPESAFE_API_KEY",
                  "status note", "Your notes", "node_modules"):
        assert shown in out
    assert everything_there(world)


def test_notes_are_never_removable(world):
    assert uninstall.main(["--remove", "notes", "--yes"]) == 2
    assert everything_there(world)


@pytest.mark.parametrize("pointed_at", ["vault", "home"])
def test_a_data_folder_pointed_at_a_vault_or_home_is_never_deleted(world, monkeypatch, pointed_at):
    target = world["vault"] if pointed_at == "vault" else world["tmp"]
    shutil.copy2(world["data"] / "config.json", target / "config.json")
    monkeypatch.setenv(app.HOME_ENV, str(target))
    data = next(it for it in uninstall.inventory(app.load_config()) if it.key == "data")
    assert data.remove is None and "Not offered" in data.note
    assert uninstall.main(["--remove", "data", "--yes"]) == 2
    assert (target / "config.json").exists() and (world["vault"] / "AI" / "a note.md").exists()


def test_data_folder_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "me"))
    f = tmp_path / "f"
    f.mkdir()
    assert "no scatha config" in uninstall.data_folder_problem(f)
    (f / "config.json").write_text("{}")
    assert uninstall.data_folder_problem(f) is None
    (f / "thesis.docx").write_text("x")
    assert "thesis.docx" in uninstall.data_folder_problem(f)
    assert "isn't there" in uninstall.data_folder_problem(tmp_path / "nope")


def test_what_is_picked_goes_and_the_rest_stays(world):
    assert uninstall.main(["--remove", "schedule,data,auth_token", "--yes"]) == 0
    assert world["gone"] == ["mine"] and not world["data"].exists() and world["env"] == {"TYPESAFE_API_KEY": "t"}
    assert (world["vault"] / "scatha.md").exists() and (world["repo"] / "smaug" / "node_modules").is_dir()
    kept = next(world["tmp"].glob("scatha-kept-*"))
    assert (kept / "topics.yaml").exists() and (kept / "tuning" / "verdicts.json").exists()
    assert not (kept / "secrets.env").exists() and not (kept / "work").exists()


def test_numbers_work_too_and_no_copy_means_none(world):
    items = uninstall.inventory(app.load_config())
    n = [i for i, it in enumerate(items, 1) if it.key in ("status-note", "node-modules")]
    assert uninstall.main(["--remove", *map(str, n), "--yes", "--no-copy"]) == 0  # as PowerShell passes them
    assert not (world["vault"] / "scatha.md").exists() and not (world["repo"] / "smaug" / "node_modules").exists()
    assert world["data"].is_dir() and not list(world["tmp"].glob("scatha-kept-*"))


def test_a_note_of_the_users_named_like_the_status_note_is_not_offered(world):
    (world["vault"] / "scatha.md").write_text("# my own page about the dragon\n")
    assert "status-note" not in [it.key for it in uninstall.inventory(app.load_config())]


def test_without_a_terminal_removing_needs_yes(world):
    assert uninstall.main(["--remove", "schedule"]) == 2
    assert everything_there(world)


def test_stopping_says_truly_what_was_removed(world, monkeypatch, capsys):
    def interrupted(var):
        raise KeyboardInterrupt
    monkeypatch.setattr(app, "delete_user_var", interrupted)
    assert uninstall.main(["--remove", "schedule,auth_token", "--yes"]) == 130
    assert "Removed before that: The schedule" in capsys.readouterr().out
    monkeypatch.setattr(uninstall.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    assert uninstall.main([]) == 130 and "Nothing removed" in capsys.readouterr().out


def test_one_stuck_item_does_not_stop_the_rest(world, monkeypatch):
    def stuck(task=None):
        raise OSError("access denied")
    monkeypatch.setattr(scheduler, "remove", stuck)
    assert uninstall.main(["--remove", "schedule,typesafe_api_key", "--yes"]) == 1
    assert world["env"] == {"AUTH_TOKEN": "a"}
