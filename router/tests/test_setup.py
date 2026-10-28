"""Setup pieces that need no network: config, topics, schedules, answers files."""

import json
import os
import sys

import pytest

from jev_router import Taxonomy, app, onboard, scheduler, topics


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path))
    return tmp_path


def test_config_merges_over_defaults(home):
    app.save_config({"batch_size": 25, "schedule": {"time": "03:30"}})
    cfg = app.load_config()
    assert cfg["batch_size"] == 25
    assert cfg["schedule"] == {"cadence": "weekly", "day": "Saturday", "time": "03:30"}  # nested merge keeps the rest
    assert cfg["notes_backend"] == "claude-code"


def test_topics_round_trip_through_the_router(home):
    built = [
        {"label": "AI", **{k: topics.PRESETS["AI"][k] for k in ("question", "yes_means", "no_means")}, "destination": "/v/AI"},
        {"label": "Wood working!", **topics.template_definition("Wood working!", "Joinery, tools, finishes"), "destination": "/v/Wood"},
        {"label": "Wood-working", **topics.template_definition("Wood-working", "dupe slug"), "destination": "/v/W2"},
    ]
    path = topics.write_taxonomy(built, home / "topics.yaml")
    tax = Taxonomy.load(path)
    assert [t.key for t in tax.topics] == ["ai", "wood_working", "wood_working_2"]
    assert tax.topics[1].label == "Wood working!" and tax.topics[1].destination == "/v/Wood"
    assert tax.route_at == tax.review_at == 0.3


@pytest.mark.parametrize("sched,ok", [
    ({"cadence": "weekly", "day": "Saturday", "time": "00:01"}, True),
    ({"cadence": "daily", "time": "23:59"}, True),
    ({"cadence": "monthly", "day": "Saturday", "time": "00:01"}, False),
    ({"cadence": "weekly", "day": "Caturday", "time": "00:01"}, False),
    ({"cadence": "weekly", "day": "Saturday", "time": "25:00"}, False),
])
def test_schedule_validation(sched, ok):
    if ok:
        scheduler.validate(sched)
    else:
        with pytest.raises(ValueError):
            scheduler.validate(sched)


def test_every_scheduler_format_encodes_the_same_moment(tmp_path):
    sched = {"cadence": "biweekly", "day": "Saturday", "time": "00:01"}
    w = tmp_path / "run.sh"
    ps = scheduler.windows_ps(sched, w)
    assert "-WeeksInterval 2" in ps and "-DaysOfWeek Saturday" in ps and "-At '00:01'" in ps
    assert "-WakeToRun" in ps and "-StartWhenAvailable" in ps
    plist = scheduler.launchd_plist(sched, w, "com.test")
    assert "<key>Weekday</key><integer>6</integer>" in plist and "<key>Minute</key><integer>1</integer>" in plist
    _, timer = scheduler.systemd_units(sched, w)
    assert "OnCalendar=Sat *-*-* 00:01:00" in timer and "Persistent=true" in timer
    assert scheduler.cron_line(sched, w).startswith("1 0 * * 6 ")
    daily = scheduler.systemd_units({"cadence": "daily", "time": "07:30"}, w)[1]
    assert "OnCalendar=*-*-* 07:30:00" in daily


def test_wrapper_bakes_in_paths(home):
    path = scheduler.write_wrapper()
    text = path.read_text(encoding="utf-8-sig")
    assert str(home) in text and "jev_router.run" in text
    if sys.platform == "win32":
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM: Task Scheduler's PowerShell reads ANSI otherwise


def test_answers_file_may_not_carry_secrets(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"topics": [], "ct0": "abc"}))
    with pytest.raises(SystemExit):
        onboard.load_answers(str(f))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows stores secrets in the registry")
def test_secrets_file_is_private(home):
    app.save_secret("TYPESAFE_API_KEY", "k1")
    app.save_secret("TYPESAFE_API_KEY", "k2")
    f = home / "secrets.env"
    assert f.read_text().count("TYPESAFE_API_KEY=") == 1 and "k2" in f.read_text()
    assert oct(f.stat().st_mode)[-3:] == "600"


def test_scratch_data_folder_gets_its_own_task(home):
    assert app.task_name() != app.TASK_NAME and app.task_name().startswith(app.TASK_NAME + "-")
    assert f"-TaskName '{app.task_name()}'" in scheduler.windows_ps({"cadence": "daily", "time": "01:00"}, home / "run.ps1")


def answers(monkeypatch, *typed):
    """Feed the wizard's input() prompts in order; fail loudly if it asks for more."""
    it = iter(typed)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


@pytest.mark.parametrize("typed,expected", [("", "Science"), ("TIL", "TIL"), ("-", None)])
def test_subfolder_enter_takes_the_suggestion_and_dash_means_none(tmp_path, monkeypatch, typed, expected):
    answers(monkeypatch, typed)
    got = onboard.pick_subfolder(tmp_path, "Science")
    assert got == (tmp_path / expected if expected else tmp_path)


def test_choose_asks_before_listing_options(monkeypatch, capsys):
    answers(monkeypatch, "2")
    assert onboard.choose("Which day?", ["Friday", "Saturday"]) == 2
    out = capsys.readouterr().out
    assert out.index("Which day?") < out.index("Friday")


def test_topic_folders_are_reviewed_before_saving(home, tmp_path, monkeypatch, capsys):
    vault = tmp_path / "Commonplace Book"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "AI").mkdir()
    monkeypatch.setattr(onboard.vaults, "find_vaults", lambda: [vault])
    picked = [{"label": "AI", "preset": True, "destination": str(vault / "AI")},
              {"label": "General knowledge", "preset": True, "destination": str(vault / "TLM")}]
    answers(monkeypatch, "2", "1", "TIL", "", "")  # change row 2: same vault, fix the typo; then save; Unsorted as suggested
    assert onboard.step_topics(app.load_config(), picked, draft=False)
    out = capsys.readouterr().out
    assert "TLM" in out and "(new)" in out  # the typo was on screen, flagged as a folder that doesn't exist
    tax = Taxonomy.load(home / "topics.yaml")
    assert [t.destination for t in tax.topics] == [str(vault / "AI"), str(vault / "TIL")]


def test_new_topics_default_to_one_vault_with_a_subfolder_each(home, tmp_path, monkeypatch):
    vault = tmp_path / "Notes"
    vault.mkdir()
    monkeypatch.setattr(onboard.vaults, "find_vaults", lambda: [vault])
    picked = [{"label": "AI", "preset": True}, {"label": "Journalism", "preset": True}]
    answers(monkeypatch, "1", "", "")  # the vault, then save the review as shown, then Unsorted as suggested
    assert onboard.step_topics(app.load_config(), picked, draft=False)
    assert [t.destination for t in Taxonomy.load(home / "topics.yaml").topics] == [str(vault / "AI"), str(vault / "Journalism")]
    assert app.load_config()["unsorted"] == {"mode": "light", "folder": str(vault / "Unsorted")}


def test_unsorted_can_go_anywhere_or_nowhere(home, tmp_path, monkeypatch):
    elsewhere = tmp_path / "Other vault" / "Inbox"
    answers(monkeypatch, str(elsewhere))
    onboard.step_unsorted(app.load_config(), [tmp_path / "Notes" / "AI"])
    assert app.load_config()["unsorted"] == {"mode": "light", "folder": str(elsewhere)}
    answers(monkeypatch, "-")
    onboard.step_unsorted(app.load_config(), [tmp_path / "Notes" / "AI"])
    assert app.load_config()["unsorted"]["mode"] == "off"


def test_wizard_points_at_the_setup_script_not_a_bare_command(monkeypatch):
    # cold read 2026-10-27: a bare .\setup.ps1 fails under Windows' default execution policy
    for platform, script in [("win32", "powershell -ExecutionPolicy Bypass -File setup.ps1 x-login"),
                             ("linux", "./setup.sh x-login")]:
        monkeypatch.setattr(onboard.sys, "platform", platform)
        assert onboard.setup_cmd("x-login") == script


def test_note_cost_speaks_the_chosen_backend():
    # cold read 2026-10-27: "2,000 Claude tokens each" matched neither the plan nor the API wording
    assert "Claude plan" in app.note_cost({"notes_backend": "claude-code"})
    assert "cent" in app.note_cost({"notes_backend": "anthropic-api"})


def states(**broken):
    """A recap where every step works except the ones named, e.g. states(s2="cookies dead")."""
    return {n: (f"s{n}" not in broken, broken.get(f"s{n}", "fine")) for n in range(1, 7)}


def test_rerun_fixes_only_whats_broken(home, monkeypatch, capsys):
    app.save_config({})
    monkeypatch.setattr(onboard, "assess", lambda cfg: states(s2="saved cookies stopped working"))
    ran = []
    monkeypatch.setattr(onboard, "run_step", lambda n, *a: ran.append(n) or True)
    assert onboard.onboard({}) == 0
    assert ran == [2, 7]  # the broken login, then the offer of a test drive
    assert "Picking up at step 2 (X login)" in capsys.readouterr().out


def test_stopped_setup_resumes_at_the_first_unfinished_step(home, monkeypatch):
    app.save_config({})
    monkeypatch.setattr(onboard, "assess", lambda cfg: states(s5="not set up yet", s6="not scheduled"))
    ran = []
    monkeypatch.setattr(onboard, "run_step", lambda n, *a: ran.append(n) or True)
    onboard.onboard({})
    assert ran == [5, 6, 7]


def test_working_install_offers_changes_instead_of_rerunning(home, monkeypatch):
    app.save_config({})
    monkeypatch.setattr(onboard, "assess", lambda cfg: states())
    ran = []
    monkeypatch.setattr(onboard, "run_step", lambda n, *a: ran.append(n) or True)
    answers(monkeypatch, "5", "")  # change topics, then done
    assert onboard.onboard({}) == 0
    assert ran == [5]


def test_resumed_setup_still_offers_the_command(home, monkeypatch):
    # cold read 2026-10-27: stopped after step 6, rerun, Enter: no `scatha` command and no offer of one
    app.save_config({})
    monkeypatch.setattr(onboard, "assess", lambda cfg: states())
    offered = []
    monkeypatch.setattr(onboard, "install_command", lambda: offered.append(1) or False)
    answers(monkeypatch, "")
    assert onboard.onboard({}) == 0
    assert offered == [1]


def test_a_failed_test_drive_is_not_all_set(home, monkeypatch, capsys):
    # cold read 2026-10-27: "The test run stopped", then "All set." and exit 0
    monkeypatch.setattr(onboard, "run_step", lambda n, *a: n != 7)
    monkeypatch.setattr(onboard, "install_command", lambda: True)
    assert onboard.onboard({}) == 1
    out = capsys.readouterr().out
    assert "test drive didn't work" in out and "All set." not in out
    assert "scatha setup`, then type 7" in out


def test_a_skipped_test_drive_is_still_all_set(home, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "run_step", lambda n, *a: None if n == 7 else True)
    monkeypatch.setattr(onboard, "install_command", lambda: True)
    assert onboard.onboard({}) == 0
    assert "All set." in capsys.readouterr().out


def test_step_test_reports_a_stopped_run(home, monkeypatch):
    monkeypatch.setattr(onboard, "yes", lambda *a, **k: True)
    monkeypatch.setattr(onboard.scheduler, "run_now", lambda args: 2)
    assert onboard.step_test(app.load_config()) is False
    monkeypatch.setattr(onboard.scheduler, "run_now", lambda args: 0)
    assert onboard.step_test(app.load_config()) is True
    monkeypatch.setattr(onboard, "yes", lambda *a, **k: False)
    assert onboard.step_test(app.load_config()) is None


def test_command_installed_in_another_terminal_is_not_offered_again(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard.shutil, "which", lambda name: None)
    monkeypatch.setattr(onboard, "installed_source", lambda: tmp_path / "router")
    monkeypatch.setattr(onboard, "yes", lambda *a, **k: pytest.fail("asked again"))
    assert onboard.install_command()


def test_first_run_goes_through_every_step(home, monkeypatch):
    ran = []
    monkeypatch.setattr(onboard, "run_step", lambda n, *a: ran.append(n) or True)
    onboard.onboard({})
    assert ran == [1, 2, 3, 4, 5, 6, 7]


def test_preflight_names_a_moved_claude_without_calling_it(monkeypatch, tmp_path):
    from jev_router import checks
    monkeypatch.setattr(checks, "verify_x", lambda cfg: "@me")
    monkeypatch.setattr(checks, "verify_typesafe", lambda: None)
    node = tmp_path / "node.exe"
    node.write_text("")
    gone = str(tmp_path / "old" / "claude.exe")
    cfg = app.load_config() | {"tools": {"node": str(node), "bird": "bird", "claude": gone}}
    problems = checks.preflight(cfg)
    assert len(problems) == 1 and "Claude Code" in problems[0] and gone in problems[0]


def test_failed_preflight_says_why_and_stops_before_fetching(home, monkeypatch):
    from jev_router import checks, run
    (home / "topics.yaml").write_text("topics: []\n")
    monkeypatch.setattr(run, "Taxonomy", type("T", (), {"load": staticmethod(lambda p: None)}))
    monkeypatch.setattr(checks, "preflight", lambda cfg: ["your X login has expired"])
    alerts = []
    monkeypatch.setattr(run, "ping", lambda cfg, text: alerts.append(text))
    monkeypatch.setattr(run, "sh", lambda *a, **k: pytest.fail("fetched despite a failed pre-flight"))
    assert run.main(["--force"]) == 2
    assert "X login has expired" in alerts[0] and app.setup_command() in alerts[0]


def test_scheduler_env_drops_what_setup_loaded(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/x/venv")
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("CLAUDE_BIN", "/c")
    env = scheduler.scheduler_env()
    assert not {"VIRTUAL_ENV", "TYPESAFE_API_KEY", "CLAUDE_BIN"} & set(env)
    assert "/x/venv" not in env["PATH"]


class FakeLinux:
    """systemctl on PATH; 'bus' says whether this session reaches the user manager."""

    def __init__(self, monkeypatch, tmp_path, bus):
        self.calls, self.bus = [], bus
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(scheduler.subprocess, "run", self.run)

    def run(self, cmd, **kw):
        self.calls.append((cmd, kw))
        down = cmd[:3] == ["systemctl", "--user", "show-environment"] and not self.bus
        return type("Done", (), {"returncode": 1 if down else 0, "stdout": "", "stderr": ""})()


SCHED = {"schedule": {"cadence": "weekly", "day": "Saturday", "time": "00:01"}}


def test_unreachable_systemd_prints_a_crontab_line_instead_of_failing(home, monkeypatch, tmp_path):
    fake = FakeLinux(monkeypatch, tmp_path, bus=False)
    said = scheduler.install(SCHED)
    assert "crontab -e" in said and "enable-linger" in said
    assert not (tmp_path / ".config/systemd/user").exists()
    assert scheduler.installed() is None  # a crontab line can't be checked, so no false ✗ either
    assert not any(c[0][:2] == ["systemctl", "--user"] and "enable" in c[0] for c in fake.calls)


def test_unschedule_removes_the_systemd_units(home, monkeypatch, tmp_path):
    FakeLinux(monkeypatch, tmp_path, bus=True)
    scheduler.install(SCHED)
    units = list((tmp_path / ".config/systemd/user").iterdir())
    assert len(units) == 2
    scheduler.remove()
    assert not any(u.exists() for u in units)


def test_linux_test_drive_runs_inside_systemd(home, monkeypatch, tmp_path):
    fake = FakeLinux(monkeypatch, tmp_path, bus=True)
    scheduler.run_now(["--preview"])
    cmd = fake.calls[-1][0]
    assert cmd[:3] == ["systemd-run", "--user", "--wait"] and cmd[-1] == "--preview"


def test_test_drive_without_systemd_gets_the_bare_scheduler_path(home, monkeypatch, tmp_path):
    fake = FakeLinux(monkeypatch, tmp_path, bus=False)
    monkeypatch.setenv("PATH", "/home/me/.nvm/versions/node/v22/bin:/usr/bin:/bin")
    scheduler.run_now([])
    cmd, kw = fake.calls[-1]
    assert cmd[0] == "/bin/bash" and kw["env"]["PATH"].split(os.pathsep) == ["/usr/bin", "/bin"]


# ----- first Linux install, 2026-10-14 -----

@pytest.mark.parametrize("label,folder", [("General knowledge", "GeneralKnowledge"), ("AI", "AI"),
                                          ("Life hacks & tips", "LifeHacksTips"), ("café culture", "CaféCulture"),
                                          ("!!!", "Notes")])
def test_topic_folders_are_one_word(label, folder):
    assert onboard.folder_name(label) == folder


def test_subfolder_suggestion_is_the_folder_name(tmp_path, monkeypatch):
    answers(monkeypatch, "")
    assert onboard.pick_subfolder(tmp_path, "General knowledge") == tmp_path / "GeneralKnowledge"


def test_enter_skips_an_optional_question(monkeypatch):
    answers(monkeypatch, "")
    assert onboard.ask("ntfy topic (Enter to skip)", default=None, clearable=True) == ""


def keys(*chunks):
    """A fake terminal: the characters a user's keystrokes and pastes deliver, in order."""
    it = iter("".join(chunks))
    return lambda: next(it)


def test_hidden_input_shows_a_running_count(capsys):
    got = onboard.read_hidden("  token: ", keys("abc123", "\x7f", "\r"), sys.stdout.write)
    assert got == "abc12"
    assert "6 characters" in capsys.readouterr().out  # visible while pasting, before Enter


def test_hidden_input_ignores_bracketed_paste_markers():
    assert onboard.read_hidden("", keys("\x1b[200~", "secret", "\x1b[201~", "\r"), lambda s: None) == "secret"


def test_hidden_input_ctrl_c_stops():
    with pytest.raises(KeyboardInterrupt):
        onboard.read_hidden("", keys("ab\x03"), lambda s: None)


def test_a_double_paste_is_caught(monkeypatch, capsys):
    monkeypatch.setattr(onboard, "hidden_input", lambda prompt: "a1b2c3a1b2c3")
    assert onboard.secret("auth_token") == "a1b2c3"
    assert "twice" in capsys.readouterr().out


@pytest.mark.parametrize("err,expect", [
    ("RuntimeError: claude -p failed: Failed to authenticate: OAuth session expired and could not be refreshed", "/login"),
    ("RuntimeError: claude -p failed: Invalid API key · Please run /login", "/login"),
    ("RuntimeError: claude -p failed: rate limited", "rate limited"),
    ("RuntimeError: claude -p failed: API Error: 401 Unauthorized", "/login"),
    # A bare 401 in a path is not a login failure (a pytest-401 temp folder once tripped this)
    ("Claude Code isn't at C:/tmp/pytest-401/claude.exe any more", "pytest-401"),
])
def test_claude_login_failures_are_explained(err, expect):
    from jev_router import checks
    assert expect in checks.notes_error_in_words(err, "claude-code")


def test_recap_does_not_count_an_unfinished_step_4(home, monkeypatch):
    from jev_router import checks
    monkeypatch.setattr(onboard, "verify_notes", lambda cfg: None)  # Haiku answers fine...
    monkeypatch.setattr(checks, "found", lambda tool: tool)
    monkeypatch.setattr(onboard, "verify_x", lambda cfg: None)
    monkeypatch.setattr(onboard, "verify_typesafe", lambda: "no key")
    monkeypatch.setattr(onboard, "node_major", lambda node: 22)
    cfg = app.load_config()  # ...but step 4 never recorded where claude is
    assert onboard.assess(cfg)[4][0] is False
    cfg["tools"]["claude"] = "/home/u/.local/bin/claude"
    assert onboard.assess(cfg)[4][0] is True


def test_step_4_waits_for_a_login_fix_instead_of_quitting(home, monkeypatch, capsys):
    replies = iter(["RuntimeError: claude -p failed: Failed to authenticate: OAuth session expired", None])
    monkeypatch.setattr(onboard, "verify_notes", lambda cfg: next(replies))
    monkeypatch.setattr(onboard, "which", lambda name: "/home/u/.local/bin/claude")
    answers(monkeypatch, "", "")  # Enter: yes, fixed it, try again; Enter: skip "About you"
    cfg = app.load_config()
    assert onboard.step_notes(cfg, preset="claude-code")
    assert "/login" in capsys.readouterr().out
    assert cfg["tools"]["claude"] == "/home/u/.local/bin/claude"


def _uv_tool_dir(monkeypatch, tool_dir, receipt):
    """Fake `uv tool dir` and the receipt uv keeps for the scatha command."""
    (tool_dir / onboard.TOOL).mkdir(parents=True)
    (tool_dir / onboard.TOOL / "uv-receipt.toml").write_text(receipt, encoding="utf-8")
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return onboard.subprocess.CompletedProcess(cmd, 0, stdout=f"{tool_dir}\n" if cmd[:3] == ["uv", "tool", "dir"] else "", stderr="")
    monkeypatch.setattr(onboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(onboard.subprocess, "run", fake_run)
    return ran


def _receipt(src):
    return f'[tool]\nrequirements = [{{ name = "{onboard.TOOL}", editable = "{src.as_posix()}" }}]\n'


def test_a_moved_scatha_folder_gets_its_command_back(tmp_path, monkeypatch, capsys):
    # 2026-10-27: moving the folder left `scatha` pointing at the old spot, and setup only checked it was on PATH
    gone = tmp_path / "old" / "router"
    ran = _uv_tool_dir(monkeypatch, tmp_path / "tools", _receipt(gone))
    assert onboard.installed_source() == gone
    assert onboard.install_command()
    assert ["uv", "tool", "install", "--force", "--editable", str(app.REPO / "router")] in ran
    assert "is gone" in capsys.readouterr().out


def test_a_second_copy_leaves_the_command_alone(tmp_path, monkeypatch):
    # a test clone running setup must not take over the command while the real folder still exists
    other = tmp_path / "other" / "router"
    other.mkdir(parents=True)
    ran = _uv_tool_dir(monkeypatch, tmp_path / "tools", _receipt(other))
    onboard.repair_command()
    assert not any(cmd[:3] == ["uv", "tool", "install"] for cmd in ran)


def test_an_unreadable_receipt_changes_nothing(tmp_path, monkeypatch):
    ran = _uv_tool_dir(monkeypatch, tmp_path / "tools", "not toml [")
    assert onboard.installed_source() is None
    onboard.repair_command()
    assert not any(cmd[:3] == ["uv", "tool", "install"] for cmd in ran)


# 2026-10-27 pre-release review, finding 9: a failed npm install passed step 1, and Smaug failing to
# start later looked like rejected X cookies.

@pytest.fixture
def smaug(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "smaug").mkdir(parents=True)
    (repo / "smaug" / "package.json").write_text(json.dumps({"dependencies": {"dayjs": "^1", "linkedom": "^0.18"}}))
    monkeypatch.setattr(app, "REPO", repo)
    monkeypatch.setattr(onboard, "which", lambda name: "/fake/" + name)
    monkeypatch.setattr(onboard, "node_major", lambda name: 22)
    return repo / "smaug"


def installed(smaug, *names):
    for n in names:
        (smaug / "node_modules" / n).mkdir(parents=True)
        (smaug / "node_modules" / n / "package.json").write_text("{}")


def test_a_failed_npm_install_fails_step_1(home, smaug, monkeypatch, capsys):
    from types import SimpleNamespace
    installed(smaug, "dayjs")  # half-finished: one package in, so node_modules exists
    monkeypatch.setattr(onboard.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert onboard.step_prereqs(app.load_config()) is False
    assert "couldn't install Smaug's packages" in capsys.readouterr().out


def test_missing_packages_are_named_not_blamed_on_x(home, smaug, monkeypatch):
    from jev_router import checks
    installed(smaug, "dayjs")
    asked_x = []
    monkeypatch.setattr(checks, "verify_x", lambda cfg, cookies=None: asked_x.append(1))
    monkeypatch.setattr(checks, "verify_typesafe", lambda: None)
    monkeypatch.setattr(checks, "verify_notes", lambda cfg: None)
    cfg = app.load_config()
    cfg["tools"]["node"] = str(smaug / "package.json")  # any file that exists
    problems = checks.preflight(cfg)
    assert problems == ["Smaug's packages aren't all installed (linkedom), so Scatha can't fetch your bookmarks."] and asked_x == []
    assert onboard.assess(cfg)[1] == (False, "Smaug's packages aren't all installed (linkedom)")


def test_all_packages_in_place_passes(home, smaug):
    from jev_router import checks
    installed(smaug, "dayjs", "linkedom")
    assert checks.smaug_missing() == []
