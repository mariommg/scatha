"""The schedule still runs when the data folder's path has spaces, quotes, $ or %.

From the 2026-10-27 pre-release review (finding 7): the systemd unit and the crontab line
put the wrapper's path in unquoted, so a data folder under "scheduled space/" registered fine
and then never ran. What these pin: every place a path goes (the PowerShell and bash
wrappers, the Task Scheduler command, the systemd unit, the crontab line, the plist) gives
back exactly that path under its own quoting rules. The Windows wrapper is run for real here,
with a stand-in for uv; the systemd unit is checked by systemd itself where it's installed.
"""

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from jev_router import scheduler

NASTY = "/home/o'neil/My Notes/$HOME 100% \"q\" back\\slash/.scatha/run.sh"
SCHED = {"cadence": "weekly", "day": "Saturday", "time": "00:01"}


def systemd_unquote(line: str) -> list[str]:
    """systemd's rules for an Exec line: words, double-quoted with C escapes; then %% -> % and $$ -> $."""
    words, i = [], 0
    while i < len(line):
        if line[i] == " ":
            i += 1
            continue
        if line[i] == '"':
            i, w = i + 1, ""
            while line[i] != '"':
                if line[i] == "\\":
                    i += 1
                w += line[i]
                i += 1
            i += 1
        else:
            j = line.find(" ", i)
            j = len(line) if j < 0 else j
            w, i = line[i:j], j
        assert not re.search(r"(?<!%)%(?!%)", w.replace("%%", "")), f"bare % in {w!r}"
        words.append(w.replace("%%", "%").replace("$$", "$"))
    return words


def test_systemd_gets_the_whole_path_as_one_argument():
    service, _ = scheduler.systemd_units(SCHED, Path(NASTY))
    exec_line = next(l for l in service.splitlines() if l.startswith("ExecStart="))[len("ExecStart="):]
    assert systemd_unquote(exec_line) == ["/bin/bash", str(Path(NASTY))]


def test_cron_gets_the_whole_path_as_one_argument():
    line = scheduler.cron_line(SCHED, Path(NASTY))
    command = line.split(" ", 5)[5]
    assert "%" not in command.replace("\\%", "")  # cron would cut the line at a bare %
    assert shlex.split(command.replace("\\%", "%")) == ["/bin/bash", str(Path(NASTY))]


def test_the_bash_wrapper_quotes_every_path():
    home, repo, uv = Path("/srv/My $Notes/o'neil"), Path("/opt/sca tha"), "/usr/bin/u v"
    text = scheduler.wrapper_sh(home, repo, uv)
    export = next(l for l in text.splitlines() if l.startswith("export SCATHA_HOME="))
    assert shlex.split(export[len("export "):]) == [f"SCATHA_HOME={home}"]
    run = next(l for l in text.splitlines() if "jev_router.run" in l)
    assert shlex.split(run)[:4] == [uv, "run", "--project", str(repo / "router")]


def test_the_plist_escapes_the_path():
    plist = scheduler.launchd_plist(SCHED, PurePosixPath("/Users/a&b/<x>/run.sh"), "com.x")
    assert "<string>/Users/a&amp;b/&lt;x&gt;/run.sh</string>" in plist


@pytest.mark.skipif(sys.platform != "win32", reason="runs the real PowerShell wrapper")
def test_the_powershell_wrapper_runs_from_a_folder_with_an_apostrophe(tmp_path):
    home, repo = tmp_path / "O'Neil notes" / ".scatha", tmp_path / "sca'tha repo"
    seen = tmp_path / "args.txt"
    uv = tmp_path / "fake uv.cmd"  # a stand-in for uv: writes down what it was given
    uv.write_text(f'@echo off\r\necho %* > "{seen}"\r\n', encoding="ascii")
    wrapper = home / "run.ps1"
    home.mkdir(parents=True)
    wrapper.write_text(scheduler.wrapper_ps1(home, repo, str(uv)), encoding="utf-8-sig", newline="\r\n")
    p = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert str(repo / "router") in seen.read_text() and (home / "work" / "logs").is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="needs PowerShell's parser")
def test_the_task_scheduler_command_parses_with_an_apostrophe(tmp_path):
    script = scheduler.windows_ps(SCHED, tmp_path / "O'Neil" / "run.ps1")
    check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
             "[Console]::In.ReadToEnd(), [ref]$null, [ref]$e); $e.Count")
    p = subprocess.run(["powershell.exe", "-NoProfile", "-Command", check], input=script,
                       capture_output=True, text=True, timeout=60)
    assert p.stdout.strip() == "0", p.stdout + p.stderr


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="needs systemd")
def test_systemd_accepts_the_unit(tmp_path):
    wrapper = tmp_path / "scheduled space" / "run.sh"
    wrapper.parent.mkdir()
    wrapper.write_text("exit 0\n")
    service, _ = scheduler.systemd_units(SCHED, wrapper)
    unit = tmp_path / "scatha-probe.service"
    unit.write_text(service)
    p = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True)
    assert "No such file" not in p.stderr and "scheduled" not in p.stderr, p.stderr
