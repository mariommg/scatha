"""Uninstall surfaces; the user selects.

What these pin: nothing is removed unless picked; notes are never removable; the data folder
is only removed when it's scatha's and nothing else, so a SCATHA_HOME pointed at a vault (or
home) can't take that folder with it; what's picked goes and the rest stays; the copy kept
before deleting the data folder holds topics and answers but no secrets or bookmark text; one
stuck item doesn't stop the others. Nothing here touches the real schedule, registry, or
checkout: those are faked.
"""

import re
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


# 2026-10-27 pre-release review, finding 3: a vault or a personal note inside work/ went with the data folder.

def test_a_vault_or_note_inside_the_data_folder_stops_its_deletion(world):
    nested = world["data"] / "work" / "PersonalVault"
    (nested / ".obsidian").mkdir(parents=True)
    (nested / "Thesis.md").write_text("my thesis")
    assert "PersonalVault" in uninstall.data_folder_problem(world["data"], app.load_config())
    uninstall.main(["--remove", "data", "--yes", "--no-copy"])  # the review's CLI run: it deleted the vault
    assert (nested / "Thesis.md").read_text() == "my thesis"
    data = next(it for it in uninstall.inventory(app.load_config()) if it.key == "data")
    assert data.remove is None and "Not offered" in data.note


@pytest.mark.parametrize("name", ["Thesis.md", "draft.txt", "photo.png"])
def test_a_personal_file_anywhere_below_stops_it(world, name):
    f = world["data"] / "tuning" / "mine" / name
    f.parent.mkdir()
    f.write_text("mine")
    assert name in uninstall.data_folder_problem(world["data"], app.load_config())


def test_notes_written_inside_the_data_folder_stop_it(world):
    cfg = app.load_config()
    cfg["status_note"] = str(world["data"] / "work" / "scatha.md")
    assert "your notes are written inside it" in uninstall.data_folder_problem(world["data"], cfg)


SCATHAS_OWN = [  # one of everything scatha writes below work/ and tuning/
    "work/ingested.md", "work/pending-bookmarks.json", "work/pending-bookmarks.json.full",
    "work/bookmarks-state.json", "work/runs.jsonl", "work/last-stop.json", "work/unsorted-seen.json",
    "work/.seen", "work/.last-success", "work/.run.lock",
    "work/logs/2026-10-24_0001.log", "work/logs/2026-10-27_123410.log", "work/logs/launchd.err",
    "work/runs/2026-10-24_0001/pending-bookmarks.json", "work/runs/2026-10-24_0001/routes.json",
    "work/runs/2026-10-24_0001/routes.md", "work/runs/2026-10-24_0001-2/routes.json",
    "tuning/verdicts.json", "tuning/not-for-me.json", "tuning/desk/deck.json", "tuning/desk/translations.json",
    "tuning/desk/sorted/pending.json", "tuning/desk/sorted/routes.json",
    "tuning/discovery/2026-10-26_1330.json", "tuning/topics_before-r_programming_2026-10-26_1332.yaml",
    "work/x-query-ids.json", "work/x-features.json",
]


# 2026-10-27 retest 2: the X client's own cache blocked removal. The list of what scatha writes is kept
# by hand, so these tie it to the writers: every cache x.js names, and the real cache writer's output.

def test_every_cache_the_x_client_names_is_owned():
    x_js = (Path(uninstall.__file__).parents[3] / "smaug" / "src" / "x.js").read_text(encoding="utf-8")
    names = re.findall(r"path\.join\(dir, '([^']+)'\)", x_js)
    assert names and all(any(o.fullmatch(f"work/{n}") for o in uninstall.OWNED) for n in names), names


NODE_WRITER = r"""
import {pathToFileURL} from 'node:url';
import path from 'node:path';
const {createRuntimeQueryIdStore} = await import(pathToFileURL(path.resolve('smaug/vendor/bird/runtime-query-ids.js')).href);
const fetchImpl = async url => new Response(url.startsWith('https://abs.twimg.com/')
  ? 'e.exports={queryId:"offlineFixtureQueryId",operationName:"Bookmarks"}'
  : '<script src="https://abs.twimg.com/responsive-web/client-web/main.fixture.js"></script>');
await createRuntimeQueryIdStore({cachePath: process.argv[1], fetchImpl}).refresh(['Bookmarks'], {force: true});
"""


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
def test_the_cache_the_x_client_really_writes_does_not_block_removal(world):
    import subprocess
    cache = world["data"] / "work" / "x-query-ids.json"  # x.js puts it beside bookmarks-state.json
    repo = Path(uninstall.__file__).parents[3]
    p = subprocess.run(["node", "--input-type=module", "-e", NODE_WRITER, str(cache)], cwd=repo,
                       capture_output=True, text=True, timeout=60)  # a fake transport: nothing leaves the machine
    assert p.returncode == 0 and cache.is_file(), p.stderr
    assert uninstall.data_folder_problem(world["data"], app.load_config()) is None
    assert uninstall.main(["--remove", "data", "--yes", "--no-copy"]) == 0 and not world["data"].exists()


def test_what_scatha_writes_there_is_still_deletable(world):
    for rel in SCATHAS_OWN:
        (world["data"] / rel).parent.mkdir(parents=True, exist_ok=True)
        (world["data"] / rel).write_text("x")
    preview = world["data"] / "work" / "notes-preview" / "AI" / "a.md"
    preview.parent.mkdir(parents=True)
    preview.write_text('---\ntweet_id: "1"\n---\n')
    assert uninstall.data_folder_problem(world["data"], app.load_config()) is None


# 2026-10-27 retest, finding A: a familiar file type is not ownership.

@pytest.mark.parametrize("rel", ["work/PersonalExports/personal.json", "work/PersonalExports/personal.yaml",
                                 "work/PersonalExports/personal.jsonl", "work/PersonalExports/personal.log",
                                 "work/PersonalExports/runs.jsonl", "tuning/my-topics.yaml", "work/logs/notes.log",
                                 "work/notes-preview/AI/My own note.md"])
def test_a_file_at_a_path_scatha_never_writes_stops_it(world, rel):
    mine = world["data"] / rel
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text("Personal data, not generated by scatha")
    data = next(it for it in uninstall.inventory(app.load_config()) if it.key == "data")
    assert data.remove is None and rel in data.note
    assert uninstall.main(["--remove", "data", "--yes", "--no-copy"]) == 2  # the review's CLI run: it returned 0
    assert mine.read_text() == "Personal data, not generated by scatha"


def test_deleting_checks_again_at_the_last_moment(world):
    go = next(it for it in uninstall.inventory(app.load_config()) if it.key == "data").remove
    late = world["data"] / "work" / "Late.md"
    late.write_text("written after the list was shown")
    assert "Didn't delete" in go() and late.exists()
