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
    answers(monkeypatch, "2", "1", "TIL", "")  # change row 2: same vault, fix the typo; then save
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
    answers(monkeypatch, "1", "")  # the vault, then save the review as shown
    assert onboard.step_topics(app.load_config(), picked, draft=False)
    assert [t.destination for t in Taxonomy.load(home / "topics.yaml").topics] == [str(vault / "AI"), str(vault / "Journalism")]


def test_wizard_points_at_the_setup_script_not_a_bare_command(monkeypatch):
    for platform, script in [("win32", r".\setup.ps1 x-login"), ("linux", "./setup.sh x-login")]:
        monkeypatch.setattr(onboard.sys, "platform", platform)
        assert onboard.setup_cmd("x-login") == script


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
