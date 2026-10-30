import pytest

from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.project_state_models import StateAuthority
from nailong_agent_sdk.state.project_state_store import (
    FileProjectStateStore,
    InMemoryProjectStateStore,
)
from tests.support.controllers import executing_runtime
from tests.support.project_state import SCHEMA, T, transition

HUMAN = StateAuthority.HUMAN
CONTROLLER = StateAuthority.CONTROLLER
HARNESS = StateAuthority.HARNESS


def open_question(question_id="q1", owner="human"):
    return transition(
        T.QUESTION_OPENED,
        {"question_id": question_id, "content": "Which clock?", "owner": owner},
        action=f"open-{question_id}",
    )


def blocked_tool_outcome(call_id="c1"):
    return transition(
        T.TOOL_OUTCOME,
        {
            "tool_call_id": call_id,
            "tool_name": "run_openroad",
            "status": "blocked",
            "error": "approval pending",
        },
        action=call_id,
    )


def resolve(
    question_id="q1", actor=HUMAN, action="resolve-q1", resolution="Use the 100 MHz clock."
):
    return transition(
        T.QUESTION_RESOLVED,
        {"question_id": question_id, "resolution": resolution},
        actor=actor,
        action=action,
    )


def clear(blocker_id="tool:c1", actor=CONTROLLER, action="clear-c1", reason="Approval granted."):
    return transition(
        T.BLOCKER_CLEARED,
        {"blocker_id": blocker_id, "reason": reason},
        actor=actor,
        action=action,
    )


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    if request.param == "memory":
        created = InMemoryProjectStateStore()
    else:
        created = FileProjectStateStore(tmp_path)
    created.ensure("p", SCHEMA)
    return created


def test_a_human_owned_question_is_closed_by_a_human_resolution(store):
    store.apply("p", open_question())
    state = store.apply("p", resolve())
    assert state.open_questions == []
    assert state.last_action.kind == "question-resolved"
    assert state.last_action.summary["resolution"] == "Use the 100 MHz clock."
    assert [event.kind.value for event in store.events("p")] == [
        "question-opened",
        "question-resolved",
    ]


def test_only_the_owner_may_resolve_a_question(store):
    store.apply("p", open_question("human-q", owner="human"))
    store.apply("p", open_question("ctrl-q", owner="controller"))
    for actor in (HARNESS, CONTROLLER):
        with pytest.raises(ValueError, match='Question "human-q" is owned by the human'):
            store.apply("p", resolve("human-q", actor=actor))
    with pytest.raises(ValueError, match='Question "ctrl-q" is owned by the controller'):
        store.apply("p", resolve("ctrl-q", actor=HARNESS))
    store.apply("p", resolve("ctrl-q", actor=CONTROLLER, action="resolve-ctrl"))
    store.apply("p", resolve("human-q", actor=HUMAN, action="resolve-human"))
    assert store.load("p").open_questions == []


def test_resolving_an_unknown_or_already_resolved_question_names_it(store):
    with pytest.raises(ValueError, match='Open question "q1" does not exist in project state "p"'):
        store.apply("p", resolve())
    store.apply("p", open_question())
    store.apply("p", resolve())
    with pytest.raises(ValueError, match='Open question "q1" does not exist'):
        store.apply("p", resolve(action="again"))


def test_a_resolution_needs_a_question_id_and_a_resolution(store):
    store.apply("p", open_question())
    with pytest.raises(ValueError, match='missing the required payload field "resolution"'):
        store.apply(
            "p", transition(T.QUESTION_RESOLVED, {"question_id": "q1"}, actor=HUMAN, action="x")
        )
    with pytest.raises(ValueError, match="resolution must not be blank"):
        store.apply("p", resolve(resolution="   "))
    assert [question.question_id for question in store.load("p").open_questions] == ["q1"]


def test_a_blocker_is_cleared_by_the_controller_or_a_human_only(store):
    store.apply("p", blocked_tool_outcome())
    assert [blocker.blocker_id for blocker in store.load("p").blocked] == ["tool:c1"]
    with pytest.raises(ValueError, match="Only controller or human authority may clear a blocker"):
        store.apply("p", clear(actor=HARNESS))
    state = store.apply("p", clear(actor=HUMAN))
    assert state.blocked == [] and state.last_action.kind == "blocker-cleared"
    store.apply("p", blocked_tool_outcome("c2"))
    assert store.apply("p", clear("tool:c2", CONTROLLER, "clear-c2")).blocked == []


def test_clearing_an_unknown_blocker_or_without_a_reason_is_refused(store):
    with pytest.raises(ValueError, match='Blocker "tool:c1" does not exist in project state "p"'):
        store.apply("p", clear())
    store.apply("p", blocked_tool_outcome())
    with pytest.raises(ValueError, match="reason must not be blank"):
        store.apply("p", clear(reason=" "))
    assert len(store.load("p").blocked) == 1


def test_resolving_the_question_and_clearing_the_blocker_empties_both_lists(store):
    store.apply("p", open_question())
    store.apply("p", blocked_tool_outcome())
    before = store.load("p")
    assert [question.question_id for question in before.open_questions] == ["q1"]
    assert [blocker.blocker_id for blocker in before.blocked] == ["tool:c1"]
    store.apply("p", resolve())
    store.apply("p", clear())
    after = store.load("p")
    assert after.open_questions == [] and after.blocked == []


def test_a_closed_question_survives_a_restart_as_history_not_as_state(tmp_path):
    first = FileProjectStateStore(tmp_path)
    first.ensure("p", SCHEMA)
    first.apply("p", open_question())
    first.apply("p", resolve())
    second = FileProjectStateStore(tmp_path)
    assert second.load("p").open_questions == []
    assert [event.kind.value for event in second.events("p")] == [
        "question-opened",
        "question-resolved",
    ]


def test_the_controller_closes_its_own_questions_and_blockers_for_a_dispatched_project(tmp_path):
    runtime, cid = executing_runtime(tmp_path)
    assert isinstance(runtime, ControllerRuntime)
    project_id = runtime.get_controller(cid).project_state_id
    store = runtime._project_state_store
    store.apply(project_id, open_question("ctrl-q", owner="controller"))
    store.apply(project_id, blocked_tool_outcome())
    runtime.resolve_question(cid, "ctrl-q", resolution="Settled by the plan.")
    runtime.clear_blocker(cid, "tool:c1", reason="The approval was granted.")
    state = runtime.project_state(cid)
    assert state.open_questions == [] and state.blocked == []
    store.apply(project_id, open_question("h-q", owner="human"))
    with pytest.raises(ValueError, match='Question "h-q" is owned by the human'):
        runtime.resolve_question(cid, "h-q", resolution="The controller must not decide this.")
    assert [question.question_id for question in runtime.project_state(cid).open_questions] == [
        "h-q"
    ]
