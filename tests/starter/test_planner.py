import asyncio

import pytest

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import Agent, ModelSetup
from nailong_agent_sdk.starter.planner import (
    DraftTask,
    PlanDraft,
    PlanDraftError,
    Planner,
    PlannerError,
    Role,
    choose_architecture,
    compile_draft,
)

RESEARCH = Role("researcher", "finds facts")
WRITER = Role("writer", "writes files", writable=True)
BUILDER = Role("builder", "edits code", writable=True)
REQUEST = "Compare vendors A and B on pricing and security, then write a one-page brief."


def task(task_id, role="researcher", produces=(), needs=()):
    return DraftTask(
        id=task_id,
        role=role,
        instructions=f"do {task_id}",
        done_when=f"{task_id} done",
        produces=list(produces),
        needs=list(needs),
    )


def draft(*tasks):
    return PlanDraft(rationale="because", tasks=list(tasks))


def team_draft():
    return draft(
        task("pricing", produces=["pricing_notes"]),
        task("security", produces=["security_notes"]),
        task("brief", role="writer", needs=["pricing_notes", "security_notes"]),
    )


def test_a_draft_without_a_specification_becomes_a_valid_plan():
    plan, tasks = compile_draft(team_draft(), REQUEST, [RESEARCH, WRITER])
    by_id = {item.id: item for item in tasks}
    assert by_id["brief"].depends_on == ("pricing", "security")
    assert by_id["pricing"].depends_on == ()
    assert {proof.parent_task_id for proof in plan.dependency_proofs} == {"pricing", "security"}
    assert plan.tasks[0].signal_uses[0].source_spans[0].startswith("request:Compare vendors")


@pytest.mark.parametrize(
    ("tasks", "fragment"),
    [
        ([task("a", needs=["ghost"])], 'needs "ghost", which no task produces'),
        ([task("a", produces=["x"], needs=["x"])], "which it produces itself"),
        ([task("a", produces=["x"]), task("b", produces=["x"])], "produced by both"),
        ([task("a", role="chef")], 'uses role "chef"'),
        ([task("###")], "must be kebab-case"),
        ([task("a", produces=["!!!"])], "use snake_case"),
        ([task("a"), task("a")], 'Task id "a" is used more than once'),
        (
            [task("a", produces=["x"], needs=["y"]), task("b", produces=["y"], needs=["x"])],
            "each wait on another of them",
        ),
    ],
)
def test_a_draft_that_cannot_work_is_refused_with_the_exact_problem(tasks, fragment):
    with pytest.raises(PlanDraftError) as raised:
        compile_draft(draft(*tasks), REQUEST, [RESEARCH, WRITER])
    assert fragment in str(raised.value)


def test_more_tasks_than_allowed_is_refused_with_the_limit():
    many = [task(f"t{number}") for number in range(4)]
    with pytest.raises(PlanDraftError, match="has 4 tasks; the most allowed is 3"):
        compile_draft(draft(*many), REQUEST, [RESEARCH], max_tasks=3)


def test_roles_that_write_are_run_one_after_another_and_readers_stay_parallel():
    plan, tasks = compile_draft(
        draft(
            task("read-one"),
            task("read-two"),
            task("edit-one", role="builder"),
            task("edit-two", role="builder"),
        ),
        REQUEST,
        [RESEARCH, BUILDER],
    )
    by_id = {item.id: item for item in tasks}
    assert by_id["edit-one"].depends_on == () and by_id["edit-two"].depends_on == ("edit-one",)
    assert by_id["read-one"].depends_on == () and by_id["read-two"].depends_on == ()


def test_a_writer_order_follows_the_data_not_the_draft_order():
    plan, tasks = compile_draft(
        draft(
            task("second", role="builder", needs=["part"]),
            task("first", role="builder", produces=["part"]),
        ),
        REQUEST,
        [BUILDER],
    )
    assert {item.id: item.depends_on for item in tasks} == {"first": (), "second": ("first",)}


def planned(*tasks, roles=(RESEARCH, WRITER, BUILDER)):
    return compile_draft(draft(*tasks), REQUEST, list(roles))[1]


def test_one_task_stays_a_single_agent():
    choice = choose_architecture(planned(task("only")))
    assert not choice.team and choice.reasons == ("the job is a single task",)


def test_two_roles_default_to_a_team():
    choice = choose_architecture(
        planned(task("a", produces=["x"]), task("b", role="writer", needs=["x"]))
    )
    assert choice.team and "2 roles are involved (researcher, writer)" in choice.reasons[0]


def test_independent_tasks_default_to_a_team_even_with_one_role():
    choice = choose_architecture(planned(task("a"), task("b")))
    assert choice.team and "a and b can run at the same time" in choice.reasons


def test_one_role_working_in_a_line_stays_a_single_agent():
    choice = choose_architecture(planned(task("a", produces=["x"]), task("b", needs=["x"])))
    assert not choice.team and "one after another" in choice.reasons[0]


class Sequence:
    def __init__(self, *turns):
        self.turns = [ScriptedModel([turn]) for turn in turns]
        self.calls = []

    async def next_turn(self, context):
        self.calls.append(context)
        return await self.turns[len(self.calls) - 1].next_turn(context)


def setup(*turns):
    return ModelSetup(Sequence(*turns), "scripted", "scripted-1")


def final(output):
    return {"type": "final", "output": output}


def answer(text):
    return final({"status": "complete", "answer": text})


def plan_output(*tasks, rationale="split by topic"):
    return final(draft(*tasks).model_dump() | {"rationale": rationale})


def test_the_planner_asks_the_model_once_and_returns_a_proposal(tmp_path):
    model = setup(plan_output(task("a", produces=["x"]), task("b", role="writer", needs=["x"])))
    planner = Planner(model=model, roles=[RESEARCH, WRITER], workspace=tmp_path)
    proposal = planner.plan_sync(REQUEST)
    assert [item.id for item in proposal.tasks] == ["a", "b"] and proposal.choice.team
    assert proposal.gap_metadata.categories_touched == ["researcher", "writer"]
    assert "Suggested: a team" in proposal.summary()
    system = str(model.model.calls[0].task.instructions)
    assert REQUEST in system


def test_a_bad_plan_goes_back_to_the_model_with_the_problem(tmp_path):
    model = setup(
        plan_output(task("a", needs=["ghost"])),
        plan_output(task("a", produces=["ghost"]), task("b", needs=["ghost"])),
    )
    planner = Planner(model=model, roles=[RESEARCH], workspace=tmp_path)
    proposal = planner.plan_sync(REQUEST)
    assert [item.id for item in proposal.tasks] == ["a", "b"]
    retry = model.model.calls[1].task.instructions
    assert 'needs "ghost", which no task produces' in retry and "corrected plan" in retry


def test_a_planner_that_never_gets_it_right_stops_and_names_the_problem(tmp_path):
    bad = plan_output(task("a", role="chef"))
    planner = Planner(
        model=setup(bad, bad, bad), roles=[RESEARCH], max_attempts=3, workspace=tmp_path
    )
    with pytest.raises(PlannerError, match='No valid plan after 3 attempts.*role "chef"'):
        planner.plan_sync(REQUEST)


def test_an_empty_request_and_duplicate_roles_are_refused(tmp_path):
    planner = Planner(model=setup(), roles=[RESEARCH], workspace=tmp_path)
    with pytest.raises(ValueError, match="request to plan is empty"):
        planner.plan_sync("   ")
    with pytest.raises(ValueError, match="Role names must be unique"):
        Planner(model=setup(), roles=[RESEARCH, RESEARCH], workspace=tmp_path)


def proposal_for(tmp_path, *tasks, roles=(RESEARCH, WRITER)):
    planner = Planner(model=setup(plan_output(*tasks)), roles=roles, workspace=tmp_path / "p")
    return planner.plan_sync(REQUEST)


def worker_factory(tmp_path, replies):
    created = {}

    def factory(role, item):
        model = setup(
            *[answer(text) if isinstance(text, str) else text for text in replies[item.id]]
        )
        agent = Agent(
            model=model,
            instructions=role.description,
            workspace=tmp_path / "w" / item.id,
            name=item.id,
        )
        created[item.id] = model.model
        return agent

    factory.created = created
    return factory


def test_a_team_runs_in_waves_and_a_later_task_receives_the_earlier_results(tmp_path):
    proposal = proposal_for(
        tmp_path,
        task("pricing", produces=["p"]),
        task("security", produces=["s"]),
        task("brief", role="writer", needs=["p", "s"]),
    )
    events = []
    factory = worker_factory(
        tmp_path, {"pricing": ["per-seat pricing"], "security": ["SOC 2"], "brief": ["the brief"]}
    )
    result = asyncio.run(
        proposal.run_team(factory, on_task=lambda e, t, a: events.append((e, t.id)))
    )
    assert result.ok and result.text == "the brief"
    prompt = factory.created["brief"].calls[0].task.instructions
    assert "## pricing\nper-seat pricing" in prompt and "## security\nSOC 2" in prompt
    assert ("start", "brief") in events and events.index(("done", "pricing")) < events.index(
        ("start", "brief")
    )
    assert result.order[-1] == "brief"


def test_when_a_task_fails_the_tasks_that_need_it_are_skipped_not_run(tmp_path):
    proposal = proposal_for(
        tmp_path,
        task("pricing", produces=["p"]),
        task("brief", role="writer", needs=["p"]),
    )
    failing = {"type": "blocked", "reason": "no sources reachable"}
    factory = worker_factory(tmp_path, {"pricing": [failing], "brief": ["never runs"]})
    result = asyncio.run(proposal.run_team(factory))
    assert not result.ok
    assert result.outcomes["pricing"].status == "failed"
    assert result.outcomes["brief"].status == "skipped"
    assert "depends on pricing, which did not complete" in result.outcomes["brief"].reason
    assert "brief" not in factory.created


def test_several_final_tasks_are_all_reported_under_their_names(tmp_path):
    proposal = proposal_for(tmp_path, task("one"), task("two"))
    factory = worker_factory(tmp_path, {"one": ["first result"], "two": ["second result"]})
    result = asyncio.run(proposal.run_team(factory))
    assert result.text == "## one\nfirst result\n\n## two\nsecond result"


def test_a_worker_that_raises_fails_its_task_with_the_exception_named(tmp_path):
    proposal = proposal_for(tmp_path, task("one"))

    def factory(role, item):
        raise RuntimeError("no workspace")

    result = asyncio.run(proposal.run_team(factory))
    assert result.outcomes["one"].reason == "RuntimeError: no workspace"


def test_keeping_everything_in_one_agent_runs_the_steps_in_order(tmp_path):
    proposal = proposal_for(
        tmp_path,
        task("pricing", produces=["p"]),
        task("brief", role="writer", needs=["p"]),
    )
    model = setup(answer("all done"))

    def solo(roles):
        assert {role.name for role in roles} == {"researcher", "writer"}
        return Agent(model=model, instructions="do it", workspace=tmp_path / "solo")

    result = asyncio.run(proposal.run_solo(solo))
    assert result.text == "all done"
    prompt = model.model.calls[0].task.instructions
    assert REQUEST in prompt and prompt.index("do pricing") < prompt.index("do brief")


def test_harmless_naming_slips_are_fixed_instead_of_sent_back_to_the_model():
    plan, tasks = compile_draft(
        draft(
            task("Read_And Summarize", role="Researcher", produces=["Summary-Notes"]),
            task("write up", role="WRITER", needs=["summary_notes"]),
        ),
        REQUEST,
        [RESEARCH, WRITER],
    )
    by_id = {item.id: item for item in tasks}
    assert set(by_id) == {"read-and-summarize", "write-up"}
    assert by_id["read-and-summarize"].role == "researcher" and by_id["write-up"].role == "writer"
    assert by_id["write-up"].depends_on == ("read-and-summarize",)
