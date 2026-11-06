from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nailong_cli.claims import ArtifactClaims, current_owner
from nailong_cli.config import ConfigError, load_config
from nailong_cli.taskstate import PlanJournal, pid_alive, task_keys
from tests.conftest import final, run_cli, write_user_config
from tests.test_plan import REQUEST, TEAM_PLAN, task
from tests.test_sdk_features import BASE
from tests.test_workflow import queued

BLOCKED = {"type": "blocked", "reason": "the build is red"}


def run(project, home, lines, *turns):
    build = queued(*turns)
    code, out, err, setup = run_cli(project, home, [], build=build, lines=lines, interactive=True)
    return code, out, err, setup.model.calls


def journal_of(project):
    return PlanJournal(project / ".nailong" / "tasks")


def events_of(project):
    (path,) = journal_of(project)._directory.glob("plan-*.jsonl")
    return path, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_finished_team_run_is_journaled_task_by_task(project, home):
    code, out, _, _ = run(
        project, home, f"/plan {REQUEST}\nt\n/exit\n", TEAM_PLAN, final("notes ok"), final("brief")
    )
    assert code == 0 and "Team finished: 2 of 2" in out
    _, events = events_of(project)
    kinds = [(e["event"], e.get("task")) for e in events]
    assert kinds[0] == ("plan", None) and kinds[-1] == ("finished", None)
    assert ("done", "research") in kinds and ("done", "write-up") in kinds
    state = journal_of(project).load(events[0]["at"] and journal_of(project).ids()[0])
    assert state.finished and state.committed() == {"research": "notes ok", "write-up": "brief"}


def test_a_failed_task_is_retried_with_the_failure_in_its_prompt_and_then_succeeds(project, home):
    code, out, _, calls = run(
        project,
        home,
        f"/plan {REQUEST}\nt\n/exit\n",
        TEAM_PLAN,
        BLOCKED,
        final("notes ok"),
        final("brief"),
    )
    assert code == 0 and "research failed; trying again" in out
    assert "attempt 2 of 2" in out and "Team finished: 2 of 2" in out
    assert "the build is red" in calls[2].task.instructions
    assert [e["event"] for e in events_of(project)[1] if e.get("task") == "research"] == [
        "started",
        "retry",
        "started",
        "done",
    ]


def test_when_retries_run_out_the_task_is_escalated_and_resume_keeps_what_was_done(project, home):
    code, out, _, calls = run(
        project,
        home,
        f"/plan {REQUEST}\nt\n/plan list\n/plan resume\n/exit\n",
        TEAM_PLAN,
        final("notes ok"),
        BLOCKED,
        BLOCKED,
        final("the brief"),
    )
    assert code == 0, out
    assert "Needs you: fix what stopped write-up" in out
    assert "research already done in an earlier run: kept" in out
    assert out.count("Team finished") == 2 and "Team finished: 2 of 2" in out
    assert len(calls) == 5
    assert "## research\nnotes ok" in calls[4].task.instructions
    _, events = events_of(project)
    assert ("escalated", "write-up") in [(e["event"], e.get("task")) for e in events]
    assert events[-1]["event"] == "finished"


def test_a_plan_cut_off_mid_task_is_offered_on_start_and_its_task_is_run_with_a_warning(
    project, home
):
    run(project, home, f"/plan {REQUEST}\nt\n/exit\n", TEAM_PLAN, final("notes ok"), final("brief"))
    path, events = events_of(project)
    cut = [
        e
        for e in events
        if not (e["event"] in {"done", "finished"} and e.get("task") != "research")
    ]
    path.write_text("\n".join(json.dumps(e) for e in cut) + "\n", encoding="utf-8")
    code, out, _, calls = run(
        project, home, "/plan list\n/plan resume\n/exit\n", final("brief again")
    )
    assert code == 0, out
    assert "1 unfinished plan(s)" in out
    assert "write-up running" in out or "write-up interrupted" in out
    assert "research already done in an earlier run: kept" in out
    assert "cut off while it was working on this task" in calls[0].task.instructions
    assert "## research\nnotes ok" in calls[0].task.instructions
    assert journal_of(project).load(journal_of(project).ids()[0]).finished


def test_a_task_whose_instructions_changed_is_not_treated_as_done(project, home):
    run(project, home, f"/plan {REQUEST}\nt\n/exit\n", TEAM_PLAN, final("notes ok"), final("brief"))
    path, events = events_of(project)
    for event in events:
        if event["event"] == "plan":
            for item in event["draft"]:
                if item["id"] == "research":
                    item["instructions"] = "do something else entirely"
    cut = [e for e in events if e["event"] != "finished"]
    path.write_text("\n".join(json.dumps(e) for e in cut) + "\n", encoding="utf-8")
    state = journal_of(project).load(journal_of(project).ids()[0])
    from nailong_cli.config import load_config
    from nailong_cli.plan_command import cli_roles

    proposal = state.proposal(cli_roles(load_config(project, home=home)))
    assert state.committed() == {"research": "notes ok", "write-up": "brief"}
    assert state.committed(task_keys(proposal)) == {}


def test_an_abandoned_plan_is_no_longer_offered(project, home):
    run(project, home, f"/plan {REQUEST}\nt\n/exit\n", TEAM_PLAN, final("a"), BLOCKED, BLOCKED)
    plan_id = journal_of(project).ids()[0]
    _, out, err, _ = run(
        project, home, f"/plan show {plan_id}\n/plan abandon {plan_id}\n/plan list\n/exit\n"
    )
    assert plan_id in out and f"abandoned {plan_id}" in out and "No unfinished plans." in out
    _, again, _, _ = run(project, home, "/exit\n")
    assert "unfinished plan" not in again


def test_resume_with_nothing_to_resume_and_ambiguous_ids_are_explained(project, home):
    _, out, err, _ = run(project, home, "/plan resume\n/exit\n")
    assert "There is no unfinished plan to resume." in out + err
    _, out2, err2, _ = run(project, home, "/plan resume plan-20990101-000000-abcd\n/exit\n")
    assert "There is no plan" in out2 + err2


def test_a_request_that_merely_starts_with_a_command_word_is_still_a_request(project, home):
    code, out, _, calls = run(
        project, home, "/plan list the files in the repo\nc\n/exit\n", TEAM_PLAN, final("x")
    )
    assert "Suggested:" in out and len(calls) >= 1


def test_only_one_live_process_may_run_a_plan_and_a_dead_ones_lock_is_taken_over(tmp_path):
    journal = PlanJournal(tmp_path / "tasks")
    (tmp_path / "tasks").mkdir()
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = tmp_path / "tasks" / "plan-x.running"
        lock.write_text(str(live.pid), encoding="utf-8")
        assert pid_alive(live.pid)
        with pytest.raises(ValueError, match=f"being run by process {live.pid}"):
            journal.acquire("plan-x")
    finally:
        live.kill()
        live.wait()
    deadline = time.time() + 10
    while pid_alive(live.pid) and time.time() < deadline:
        time.sleep(0.05)
    taken = journal.acquire("plan-x")
    assert taken.read_text(encoding="utf-8").strip().isdigit()
    journal.release(taken)
    assert not taken.exists()


def test_task_keys_change_when_a_task_or_anything_upstream_of_it_changes():
    from nailong_agent_sdk.starter.planner import PlanDraft, compile_draft

    from nailong_cli.plan_command import cli_roles

    def keys_for(instructions):
        first = task("first", "explorer", produces=["a"])
        first["instructions"] = instructions
        draft = PlanDraft(
            rationale="r",
            tasks=[first, task("second", "worker", needs=["a"]), task("other", "explorer")],
        )
        from types import SimpleNamespace

        roles = cli_roles(SimpleNamespace(agents={}))
        plan, tasks = compile_draft(draft, REQUEST, roles)
        return task_keys(SimpleNamespace(request=REQUEST, tasks=tasks))

    base, changed = keys_for("one"), keys_for("two")
    assert base["first"] != changed["first"] and base["second"] != changed["second"]
    assert base["other"] == changed["other"]


def test_claims_stop_a_second_task_from_editing_a_file_the_first_holds(tmp_path):
    claims = ArtifactClaims(tmp_path / "claims", tmp_path)
    claims.claim("task-a", "src/app.py")
    claims.claim("task-a", "src/app.py")
    with pytest.raises(ValueError, match="SRC/app.py is being edited by task-a"):
        claims.claim("task-b", "SRC/app.py")
    with pytest.raises(ValueError, match="wait for them"):
        claims.check("main", "src/app.py")
    claims.claim("task-b", "src/other.py")
    assert claims.holders() == {"src/app.py": "task-a", "src/other.py": "task-b"}
    assert claims.release("task-a") == ["src/app.py"]
    claims.claim("task-b", "src/app.py")


def test_a_claim_left_by_a_process_that_died_does_not_block_anyone(tmp_path):
    claims = ArtifactClaims(tmp_path / "claims", tmp_path)
    (tmp_path / "claims").mkdir()
    (tmp_path / "claims" / "claims.json").write_text(
        json.dumps({"a.py": {"owner": "ghost", "pid": 2_000_000_000}}), encoding="utf-8"
    )
    claims.claim("task-b", "a.py")
    assert claims.holders() == {"a.py": "task-b"}


def test_the_edit_hook_refuses_the_losing_task_with_a_message_and_lets_the_main_agent_read(
    tmp_path,
):
    import asyncio
    from types import SimpleNamespace

    claims = ArtifactClaims(tmp_path / "claims", tmp_path)

    def attempt(owner, tool="write_file", path="a.py"):
        token = current_owner.set(owner)
        try:
            event = SimpleNamespace(tool=tool, arguments={"path": path}, result=None)
            return asyncio.run(claims.before_tool(event))
        finally:
            current_owner.reset(token)

    assert attempt("task-a") is None
    assert "being edited by task-a" in attempt("task-b")
    assert attempt("task-b", tool="read_file") is None
    assert "being edited by task-a" in attempt(None)
    claims.release("task-a")
    assert attempt("task-b") is None


def test_the_new_settings_are_range_checked_and_default_to_careful_values(project, home):
    config = load_config(project, home=home)
    assert config.task_attempts == 2 and config.team_parallel == 1
    write_user_config(home, BASE + "task_attempts = 9\n")
    with pytest.raises(ConfigError, match="task_attempts must be a whole number from 1 to 5"):
        load_config(project, home=home)
    write_user_config(home, BASE + "team_parallel = 0\n")
    with pytest.raises(ConfigError, match="team_parallel must be a whole number from 1 to 4"):
        load_config(project, home=home)


def test_a_process_killed_in_the_middle_of_a_plan_leaves_a_journal_the_next_one_can_resume(
    tmp_path,
):
    from nailong_cli.config import load_config  # noqa: F401  (imports must work in the child)

    project = tmp_path / "proj"
    project.mkdir()
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "support" / "crashing_plan.py"), str(project)],
        stdout=subprocess.PIPE,
        text=True,
    )
    plan_id = child.stdout.readline().strip()
    assert plan_id.startswith("plan-")
    child.kill()
    child.wait()
    deadline = time.time() + 10
    while pid_alive(child.pid) and time.time() < deadline:
        time.sleep(0.05)
    journal = PlanJournal(project / ".nailong" / "tasks")
    assert (project / ".nailong" / "tasks" / f"{plan_id}.running").exists()
    state = journal.load(plan_id)
    assert state.unfinished and state.committed() == {"first": "first result"}
    assert state.tasks["second"].status == "running" and state.interrupted() == ["second"]
    lock = journal.acquire(plan_id)
    assert lock.read_text(encoding="utf-8").strip() == str(__import__("os").getpid())
    journal.release(lock)
