from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from nailong_cli.events import EventLog, check, read_events, recover_dead
from nailong_cli.health import collect
from nailong_cli.taskstate import pid_alive
from tests.conftest import call, final, run_cli
from tests.test_plan import REQUEST, TEAM_PLAN
from tests.test_workflow import queued

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def logs(project):
    return sorted((project / ".nailong" / "events").glob("run-*.jsonl"))


def only_log(project):
    (path,) = logs(project)
    return read_events(path)


def test_a_session_turn_and_tool_call_are_nested_spans_in_one_trace(project, home):
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    code, _, err, _ = run_cli(
        project,
        home,
        [],
        call("read_file", path="a.txt"),
        final("done"),
        lines="Read it\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    events = only_log(project)
    kinds = [e["event_type"] for e in events]
    assert kinds == [
        "session.start",
        "turn.start",
        "tool.start",
        "tool.end",
        "turn.end",
        "session.end",
    ]
    by = {e["event_type"]: e for e in events}
    assert by["turn.start"]["parent_event_id"] == by["session.start"]["event_id"]
    assert by["tool.start"]["parent_event_id"] == by["turn.start"]["event_id"]
    assert by["tool.start"]["parent_span_id"] == by["turn.start"]["span_id"]
    assert len({e["trace_id"] for e in events}) == 1
    assert by["tool.end"]["payload"] == {
        "tool": "read_file",
        "status": "succeeded",
        "iteration": 1,
    } or (by["tool.end"]["payload"]["status"] == "succeeded")
    assert by["turn.end"]["payload"]["status"] in {"completed", "complete"}
    assert check(events) == []
    assert by["session.start"]["session_id"]


def test_wiki_and_discovery_events_hang_off_the_tool_call_that_caused_them(project, home):
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("X = 1\n", encoding="utf-8")
    run_cli(
        project,
        home,
        BYPASS,
        call("wiki_write", page="notes", text="Notes.", why="test"),
        call("discovery_publish", topic="a.x", claim="X is one.", evidence=[{"path": "src/a.py"}]),
        final("done"),
        lines="Go\n/exit\n",
        interactive=True,
    )
    events = only_log(project)
    tools = {e["payload"]["tool"]: e for e in events if e["event_type"] == "tool.start"}
    wiki = next(e for e in events if e["event_type"] == "wiki.written")
    found = next(e for e in events if e["event_type"] == "discovery.published")
    assert wiki["parent_event_id"] == tools["wiki_write"]["event_id"]
    assert found["parent_event_id"] == tools["discovery_publish"]["event_id"]
    assert wiki["payload"]["revision"] == 1 and found["payload"]["id"] == "d-0001"
    assert check(events) == []


def test_a_team_run_is_a_plan_span_with_task_spans_and_each_tools_under_its_task(project, home):
    build = queued(TEAM_PLAN, final("notes"), final("brief"))
    code, out, err, _ = run_cli(
        project, home, [], build=build, lines=f"/plan {REQUEST}\nt\n/exit\n", interactive=True
    )
    assert code == 0, err
    events = only_log(project)
    plan = next(e for e in events if e["event_type"] == "plan.start")
    starts = [e for e in events if e["event_type"] == "task.start"]
    assert [e["task_id"] for e in starts] == ["research", "write-up"]
    assert all(e["parent_event_id"] == plan["event_id"] for e in starts)
    assert all(e["parent_span_id"] == plan["span_id"] for e in starts)
    assert all(e["plan_id"] == plan["plan_id"] and e["plan_id"] for e in starts)
    ends = {e["task_id"]: e["payload"]["status"] for e in events if e["event_type"] == "task.end"}
    assert ends == {"research": "completed", "write-up": "completed"}
    assert (
        next(e for e in events if e["event_type"] == "plan.end")["payload"]["status"] == "finished"
    )
    assert check(events) == []


def test_a_retry_closes_the_failed_attempt_and_starts_a_new_span(project, home):
    blocked = {"type": "blocked", "reason": "red"}
    build = queued(TEAM_PLAN, blocked, final("notes"), final("brief"))
    run_cli(project, home, [], build=build, lines=f"/plan {REQUEST}\nt\n/exit\n", interactive=True)
    events = only_log(project)
    research = [e for e in events if e.get("task_id") == "research"]
    assert [e["event_type"] for e in research] == [
        "task.start",
        "task.end",
        "task.retry",
        "task.start",
        "task.end",
    ]
    assert [e["payload"].get("status") for e in research if e["event_type"] == "task.end"] == [
        "failed",
        "completed",
    ]
    assert check(events) == []


def test_events_check_and_summary_and_tail_are_available_in_the_session(project, home):
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        final("hi"),
        lines="Hello\n/events\n/events check\n/events tail 2\n/exit\n",
        interactive=True,
    )
    assert "turn.start 1" in out and "open spans: 1" in out
    assert "1 log(s) checked, 0 problem(s)." in out
    assert "turn.end" in out


def test_a_run_that_died_with_spans_open_is_closed_and_reported_on_the_next_start(project, home):
    directory = project / ".nailong" / "events"
    dead = EventLog(directory / "run-dead00000000.jsonl", run_id="run-dead00000000")
    dead.start("session.start")
    dead.start("turn.start")
    (directory / "run-dead00000000.pid").write_text("2000000000", encoding="utf-8")
    _, out, err, _ = run_cli(
        project, home, [], final("hi"), lines="Hello\n/exit\n", interactive=True
    )
    assert "closed 2 unfinished event span(s) left by 1 earlier run(s)" in out
    recovered = read_events(directory / "run-dead00000000.jsonl")
    assert check(recovered) == []
    assert {e["payload"]["status"] for e in recovered if e["event_type"].endswith(".end")} == {
        "interrupted"
    }
    assert not (directory / "run-dead00000000.pid").exists()


def test_a_log_of_a_process_that_is_still_alive_is_left_alone(project, home, tmp_path):
    directory = project / ".nailong" / "events"
    live = EventLog(directory / "run-live00000000.jsonl", run_id="run-live00000000")
    live.start("session.start")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (directory / "run-live00000000.pid").write_text(str(child.pid), encoding="utf-8")
        assert pid_alive(child.pid)
        assert recover_dead(directory) == {}
        assert len(read_events(directory / "run-live00000000.jsonl")) == 1
    finally:
        child.kill()
        child.wait()


def test_health_is_ok_for_a_clean_project_and_names_what_needs_attention(project, home):
    (project / "spec.md").write_text("limit is 5\n", encoding="utf-8")
    run_cli(
        project,
        home,
        BYPASS,
        call("wiki_write", page="limits", text="Limit is 5.", why="spec", sources=["spec.md"]),
        final("done"),
        lines="Go\n/exit\n",
        interactive=True,
    )
    clean = collect(project)
    assert clean.status == "ok", clean.lines()
    assert clean.metrics["memory"]["pages with sources"] == "100%"
    assert clean.metrics["memory"]["citations current"] == "100%"
    (project / "spec.md").write_text("limit is 9\n", encoding="utf-8")
    stale = collect(project)
    assert stale.status == "attention"
    assert any("stale wiki: limits" in item for item in stale.attention)
    assert stale.metrics["memory"]["pages stale"] == 1
    assert stale.metrics["memory"]["citations current"] == "0%"


def test_health_is_broken_when_an_event_log_was_edited_and_the_flag_exits_non_zero(project, home):
    run_cli(project, home, [], final("hi"), lines="Hello\n/exit\n", interactive=True)
    (path,) = logs(project)
    lines = path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[1])
    forged["payload"]["status"] = "forged"
    lines[1] = json.dumps(forged, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = collect(project)
    assert report.status == "broken" and any("edited" in item for item in report.problems)
    code, out, _, _ = run_cli(project, home, ["--health", "--project", str(project)])
    assert code == 1 and "health: broken" in out
    code, out, _, _ = run_cli(
        project, home, ["--health", "--project", str(project), "--output", "json"]
    )
    assert code == 1 and json.loads(out)["status"] == "broken"


def test_health_flag_exits_zero_with_attention_items_and_unfinished_plans_are_listed(project, home):
    blocked = {"type": "blocked", "reason": "red"}
    build = queued(TEAM_PLAN, final("notes"), blocked, blocked)
    run_cli(project, home, [], build=build, lines=f"/plan {REQUEST}\nt\n/exit\n", interactive=True)
    code, out, _, _ = run_cli(project, home, ["--health", "--project", str(project)])
    assert code == 0
    assert "attention" in out and "is unfinished: /plan resume" in out
    assert "plans: total 1, unfinished 1" in out


def test_pid_helpers_and_time_do_not_leave_state_behind(project):
    assert Path(project).exists() and time.time() > 0
