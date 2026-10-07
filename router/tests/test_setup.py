"""Setup pieces that need no network: config, topics, schedules, answers files."""

import json
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
