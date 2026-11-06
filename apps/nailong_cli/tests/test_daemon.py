from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

from nailong_cli import schedule
from nailong_cli.daemon import Daemon, DaemonError
from tests.conftest import final, run_cli


def status_file(home):
    return home / ".local" / "state" / "nailong" / "daemon.json"


def test_the_daemon_checks_every_interval_and_keeps_what_it_did(tmp_path):
    waits = []
    answers = [["No schedule is due."], ["sch-1 ok: done"], []]
    daemon = Daemon(tmp_path, 30, lambda: answers.pop(0), sleep=waits.append)
    assert daemon.run(max_ticks=3) == 3
    assert waits == [30, 30]
    saved = json.loads(status_file(tmp_path).read_text(encoding="utf-8"))
    assert saved["ticks"] == 3 and saved["recent"] == ["No schedule is due.", "sch-1 ok: done"]
    assert saved["pid"] == 0 and "stopped" in saved
    assert daemon.status()["running"] is False


def test_a_stop_request_ends_it_after_the_current_check(tmp_path):
    calls = []
    sleeps = []

    def tick():
        calls.append(1)
        if len(calls) == 2:
            assert Daemon(tmp_path, 30, lambda: []).request_stop() is True
        return ["checked"]

    daemon = Daemon(tmp_path, 30, tick, sleep=sleeps.append)
    assert daemon.run() == 2
    assert sleeps == [30]
    assert not (tmp_path / ".local" / "state" / "nailong" / "daemon.stop").exists()


def test_a_second_daemon_is_refused_while_one_is_alive_and_a_dead_ones_record_is_ignored(tmp_path):
    state = status_file(tmp_path)
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"pid": os.getppid(), "ticks": 5}), encoding="utf-8")
    other = Daemon(tmp_path, 30, lambda: [])
    if other.status()["running"]:
        with pytest.raises(DaemonError, match="already running"):
            other.run(max_ticks=1)
    state.write_text(json.dumps({"pid": 2_000_000_000, "ticks": 5}), encoding="utf-8")
    assert other.run(max_ticks=1) == 1


def test_a_too_short_interval_is_refused_by_name(tmp_path):
    with pytest.raises(DaemonError, match="shortest allowed is 10s"):
        Daemon(tmp_path, 2, lambda: [])


def test_stop_and_status_say_so_when_nothing_runs(tmp_path):
    daemon = Daemon(tmp_path, 30, lambda: [])
    assert daemon.request_stop() is False
    assert daemon.status() == {"running": False, "note": "no daemon has run here"}


def test_the_daemon_flag_runs_a_due_prompt_like_run_schedules_and_reports(project, home):
    task = schedule.add(home, "every 1h", "Report on the repo", project, "plan")
    old = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
    items = schedule.load(home)
    items[0].created = old
    schedule.save(home, items)
    code, out, err, _ = run_cli(
        project,
        home,
        ["--daemon", "--daemon-ticks", "1", "--daemon-interval", "10"],
        final("quiet"),
    )
    assert code == 0, err
    assert "Checking the schedule every 10s" in out and "Daemon stopped after 1 check(s)." in out
    assert schedule.load(home)[0].last_status == "ok"
    files = sorted((project / ".nailong" / "schedules" / task.id).glob("*.md"))
    assert len(files) == 1 and "quiet" in files[0].read_text(encoding="utf-8")
    _, report, _, _ = run_cli(project, home, ["--daemon-status"])
    saved = json.loads(report)
    assert saved["ticks"] == 1 and saved["running"] is False
    assert any(task.id in line for line in saved["recent"])
    _, none_running, _, _ = run_cli(project, home, ["--daemon-stop"])
    assert "No daemon is running." in none_running


def test_a_bad_interval_on_the_command_line_is_a_usage_error(project, home):
    code, _, err, _ = run_cli(project, home, ["--daemon", "--daemon-interval", "1"])
    assert code != 0 and "shortest allowed is 10s" in err
