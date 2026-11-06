from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nailong_cli import schedule
from tests.conftest import final, run_cli

MONDAY_8 = datetime.fromisoformat("2026-11-02T08:00:00")


def item(when, created="2026-11-02T08:00:00", last_run=None):
    return schedule.Scheduled(
        id="sch-1",
        when=when,
        prompt="p",
        project="/x",
        mode="plan",
        created=created,
        last_run=last_run,
    )


@pytest.mark.parametrize(
    ("text", "normal"),
    [
        ("Every 30M", "every 30m"),
        ("daily 9:05", "daily 09:05"),
        ("weekdays 18:00", "weekdays 18:00"),
        ("0 9 * * 1-5", "0 9 * * 1-5"),
        ("*/15 * * * *", "*/15 * * * *"),
    ],
)
def test_good_schedules_are_accepted_and_normalised(text, normal):
    assert schedule.check_when(text) == normal


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("sometimes", "is not a schedule"),
        ("every 0m", "at least 1"),
        ("daily 25:00", "outside 00:00 to 23:59"),
        ("61 * * * *", "outside 0 to 59"),
        ("*/0 * * * *", "positive number"),
        ("a * * * *", "is not a number"),
    ],
)
def test_bad_schedules_say_what_is_wrong(text, fragment):
    with pytest.raises(ValueError, match=fragment):
        schedule.check_when(text)


def test_an_interval_is_due_once_it_has_passed_since_the_last_run():
    task = item("every 2h", last_run="2026-11-02T08:00:00")
    assert not schedule.is_due(task, MONDAY_8 + timedelta(hours=1, minutes=59))
    assert schedule.is_due(task, MONDAY_8 + timedelta(hours=2))


def test_a_daily_time_is_due_after_that_time_and_not_twice_in_a_day():
    task = item("daily 09:00", created="2026-11-02T08:00:00")
    assert not schedule.is_due(task, datetime.fromisoformat("2026-11-02T08:59:00"))
    assert schedule.is_due(task, datetime.fromisoformat("2026-11-02T09:01:00"))
    ran = item("daily 09:00", last_run="2026-11-02T09:01:00")
    assert not schedule.is_due(ran, datetime.fromisoformat("2026-11-02T23:00:00"))
    assert schedule.is_due(ran, datetime.fromisoformat("2026-11-03T09:00:00"))


def test_weekdays_skip_the_weekend():
    friday_run = item("weekdays 09:00", last_run="2026-11-06T09:01:00")
    assert not schedule.is_due(friday_run, datetime.fromisoformat("2026-11-08T12:00:00"))
    assert schedule.is_due(friday_run, datetime.fromisoformat("2026-11-09T09:00:00"))


def test_cron_fields_match_minutes_hours_and_weekdays():
    task = item("30 9 * * 1-5", last_run="2026-11-02T00:00:00")
    assert schedule.is_due(task, datetime.fromisoformat("2026-11-02T09:30:00"))
    assert not schedule.is_due(task, datetime.fromisoformat("2026-11-02T09:29:00"))
    saturday = item("30 9 * * 1-5", last_run="2026-11-07T00:00:00")
    assert not schedule.is_due(saturday, datetime.fromisoformat("2026-11-07T23:59:00"))


def test_schedules_are_saved_listed_and_removed(tmp_path):
    first = schedule.add(tmp_path, "daily 09:00", "check status", tmp_path, "plan")
    assert [i.id for i in schedule.load(tmp_path)] == [first.id]
    with pytest.raises(ValueError, match="mode must be one of"):
        schedule.add(tmp_path, "daily 09:00", "x", tmp_path, "auto")
    with pytest.raises(ValueError, match="cannot be empty"):
        schedule.add(tmp_path, "daily 09:00", "  ", tmp_path, "plan")
    schedule.remove(tmp_path, first.id)
    assert schedule.load(tmp_path) == []
    with pytest.raises(ValueError, match='no schedule "nope"'):
        schedule.remove(tmp_path, "nope")


def test_a_corrupt_schedule_file_is_named(tmp_path):
    path = schedule.schedule_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="schedule file .* cannot be read"):
        schedule.load(tmp_path)


def test_only_due_schedules_run_and_each_run_is_recorded_once(tmp_path):
    due = schedule.add(tmp_path, "every 1h", "a", tmp_path, "plan")
    later = schedule.add(tmp_path, "every 5d", "b", tmp_path, "plan")
    ran = []

    def run_one(task):
        ran.append(task.id)
        return "ok", "done"

    moment = datetime.now().astimezone() + timedelta(hours=2)
    lines = schedule.run_due(tmp_path, run_one, moment)
    assert ran == [due.id] and lines == [f"{due.id} ok: done"]
    assert schedule.run_due(tmp_path, run_one, moment) == ["No schedule is due."]
    assert later.id not in ran


def test_a_second_runner_stops_while_one_is_active(tmp_path):
    schedule.add(tmp_path, "every 1h", "a", tmp_path, "plan")
    inner = []

    def run_one(task):
        inner.extend(schedule.run_due(tmp_path, lambda t: ("ok", "x"), None))
        return "ok", "outer"

    schedule.run_due(tmp_path, run_one, datetime.now().astimezone() + timedelta(hours=2))
    assert inner == ["Another nailong --run-schedules is already running; nothing done."]


def test_the_schedule_command_adds_lists_and_removes(project, home):
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        lines=(
            "/schedule add daily 09:00 mode=accept-edits -- summarise the repo\n"
            "/schedule\n/schedule add sometimes -- x\n/schedule install\n/exit\n"
        ),
        interactive=True,
    )
    assert "daily 09:00 [accept-edits] summarise the repo" in out
    assert "is not a schedule" in err and "--run-schedules" in out
    saved = schedule.load(home)
    assert len(saved) == 1 and saved[0].mode == "accept-edits"


def test_run_schedules_runs_a_due_prompt_unattended_and_keeps_its_output(project, home):
    task = schedule.add(home, "every 1h", "Report on the repo", project, "plan")
    old = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
    items = schedule.load(home)
    items[0].created = old
    schedule.save(home, items)
    code, out, err, _ = run_cli(project, home, ["--run-schedules"], final("all quiet"))
    assert code == 0, err
    assert f"{task.id} ok" in out
    files = sorted((project / ".nailong" / "schedules" / task.id).glob("*.md"))
    assert len(files) == 1 and "all quiet" in files[0].read_text(encoding="utf-8")
    assert schedule.load(home)[0].last_status == "ok"
    _, again, _, _ = run_cli(project, home, ["--run-schedules"], final("never"))
    assert "No schedule is due." in again
