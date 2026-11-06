from __future__ import annotations

from tests.conftest import final, run_cli, tool_names, write_user_config
from tests.test_terminal_ui import run_fancy
from tests.test_workflow import queued

REQUEST = "Compare vendors A and B, then write the brief."


def draft(*tasks):
    return {"type": "final", "output": {"rationale": "split by role", "tasks": list(tasks)}}


def task(task_id, role, produces=(), needs=()):
    return {
        "id": task_id,
        "role": role,
        "instructions": f"do {task_id}",
        "done_when": f"{task_id} is done",
        "produces": list(produces),
        "needs": list(needs),
    }


TEAM_PLAN = draft(
    task("research", "explorer", produces=["notes"]),
    task("write-up", "worker", needs=["notes"]),
)
ONE_TASK_PLAN = draft(task("only", "explorer"))
BAD_PLAN = draft(task("a", "explorer", needs=["ghost"]))


def run(project, home, lines, *turns):
    build = queued(*turns)
    code, out, err, setup = run_cli(project, home, [], build=build, lines=lines, interactive=True)
    return code, out, err, setup.model.calls


def test_a_team_plan_asks_and_then_runs_each_task_in_order(project, home):
    code, out, _, calls = run(
        project,
        home,
        f"/plan {REQUEST}\nt\n/exit\n",
        TEAM_PLAN,
        final("found two vendors"),
        final("the brief"),
    )
    assert code == 0 and "Suggested: a team" in out
    assert "Run as a [t]eam, keep it in one [a]gent, or [c]ancel?" in out
    assert out.index("▸ research [explorer]") < out.index("▸ write-up [worker]")
    assert "Team finished: 2 of 2 task(s) completed." in out
    assert "## research\nfound two vendors" in calls[2].task.instructions


def test_choosing_one_agent_runs_every_step_in_a_single_turn(project, home):
    code, out, _, calls = run(
        project, home, f"/plan {REQUEST}\na\n/exit\n", TEAM_PLAN, final("did it all")
    )
    assert code == 0 and "did it all" in out and len(calls) == 2
    assert "Work through these steps in order" in calls[1].task.instructions
    assert "Team finished" not in out


def test_cancelling_runs_nothing_after_the_plan(project, home):
    code, out, _, calls = run(project, home, f"/plan {REQUEST}\nc\n/exit\n", TEAM_PLAN)
    assert code == 0 and "Cancelled." in out and len(calls) == 1


def test_a_plan_that_needs_only_one_agent_runs_without_asking(project, home):
    code, out, _, calls = run(
        project, home, f"/plan {REQUEST}\n/exit\n", ONE_TASK_PLAN, final("quick answer")
    )
    assert code == 0 and "Suggested: one agent" in out and "quick answer" in out
    assert "Run as a [t]eam" not in out and len(calls) == 2


def test_with_a_menu_available_the_choice_is_a_menu_and_the_second_option_keeps_one_agent(
    project, home
):
    asked = []

    def chooser(title, options):
        asked.append((title, options))
        return 1

    code, out, _ = run_fancy(
        project,
        home,
        TEAM_PLAN,
        final("did it all"),
        lines=f"/plan {REQUEST}\n/exit\n",
        chooser=chooser,
    )
    assert code == 0 and "did it all" in out
    title, options = asked[0]
    assert title == "How should this run?" and options[1] == "Keep everything in one agent"
    assert options[0].startswith("Run as a team (2 tasks: explorer, worker)")


def test_a_plan_that_cannot_be_fixed_is_reported_and_the_session_carries_on(project, home):
    code, out, err, calls = run(
        project, home, f"/plan {REQUEST}\n/status\n/exit\n", BAD_PLAN, BAD_PLAN, BAD_PLAN
    )
    assert code == 0 and "No valid plan after 3 attempts" in err and "ghost" in err
    assert "mode " in out and len(calls) == 3


def test_explorers_cannot_change_anything_and_workers_can(project, home):
    _, _, _, calls = run(
        project,
        home,
        f"/plan {REQUEST}\nt\n/exit\n",
        TEAM_PLAN,
        final("notes"),
        final("done"),
    )
    explorer, worker = tool_names(calls[1]), tool_names(calls[2])
    assert "read_file" in explorer and "write_file" not in explorer
    assert "run_command" not in explorer
    assert "write_file" in worker and "run_command" in worker


def test_the_agents_you_configure_become_roles_the_planner_can_use(project, home):
    write_user_config(
        home,
        'provider = "anthropic"\nmodel = "test-model"\napi_key_env = "TEST_KEY"\n'
        '[agents.reviewer]\ndescription = "Reviews a change for bugs."\n'
        'instructions = "You review code."\n',
    )
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    _, _, _, calls = run(project, home, f"/plan {REQUEST}\nc\n/exit\n", TEAM_PLAN)
    instructions = calls[0].task.instructions
    assert REQUEST in instructions
    system = " ".join(
        section["value"]["text"]
        for section in calls[0].prompt.model_dump()["sections"]
        if section["kind"] == "instructions"
    )
    assert "- reviewer: Reviews a change for bugs." in system
    assert "- explorer:" in system and "- worker:" in system


def test_plan_without_a_request_shows_how_to_use_it(project, home):
    _, out, _, _ = run(project, home, "/plan\n/exit\n")
    assert "Usage: /plan <what you want done>" in out
