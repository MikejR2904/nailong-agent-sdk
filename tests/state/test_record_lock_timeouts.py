import threading
from contextlib import contextmanager

import pytest

from nailong_agent_sdk.agent.orchestrator import Orchestrator
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.record_locks import RecordLocks
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.project_state_store import (
    FileProjectStateStore,
    InMemoryProjectStateStore,
)
from tests.support.agents import arun
from tests.support.controllers import new_controller
from tests.support.orchestration import make_policy, make_request
from tests.support.plans import ok, simple_plan
from tests.support.project_state import SCHEMA, T, transition


@contextmanager
def held_by_another_thread(lock):
    ready, release = threading.Event(), threading.Event()

    def holder():
        with lock:
            ready.set()
            release.wait(timeout=60)

    thread = threading.Thread(target=holder)
    thread.start()
    assert ready.wait(timeout=30)
    try:
        yield
    finally:
        release.set()
        thread.join(timeout=60)


def impatient(store):
    store._locks = RecordLocks(
        store._locks._directory, timeout_code=store._locks._timeout_code, timeout_seconds=0.3
    )
    return store


def test_a_run_that_stays_locked_is_reported_with_the_run_lock_code(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    impatient(coordinator._store)
    with held_by_another_thread(HarnessCoordinator(tmp_path)._store.locked(run_id)):
        with pytest.raises(AgentSdkError) as raised:
            coordinator.cancel_run(run_id)
    assert raised.value.code == "RUN_LOCK_TIMEOUT"
    assert f'"{run_id}.lock"' in str(raised.value)
    assert coordinator.cancel_run(run_id).cancelled


def test_a_controller_that_stays_locked_is_reported_with_the_controller_lock_code(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    controller_id = new_controller(runtime).controller_id
    impatient(runtime._controller_store)
    with held_by_another_thread(
        ControllerRuntime(tmp_path)._controller_store.locked(controller_id)
    ):
        with pytest.raises(AgentSdkError) as raised:
            runtime.submit_plan(controller_id, simple_plan())
    assert raised.value.code == "CONTROLLER_LOCK_TIMEOUT"
    assert runtime.submit_plan(controller_id, simple_plan()).plan is not None


def test_an_orchestration_that_stays_locked_is_reported_with_the_orchestration_lock_code(
    tmp_path,
):
    orchestrator = Orchestrator(tmp_path, make_policy())
    other = None
    try:
        record = arun(orchestrator.prepare(make_request()))
        impatient(orchestrator._store)
        other = Orchestrator.resume(tmp_path, record.orchestration_id)
        with held_by_another_thread(other._store.locked(record.orchestration_id)):
            with pytest.raises(AgentSdkError) as raised:
                orchestrator.submit_for_approval(record.orchestration_id)
        assert raised.value.code == "ORCHESTRATION_LOCK_TIMEOUT"
        assert orchestrator.submit_for_approval(record.orchestration_id).controller_id is not None
    finally:
        orchestrator._telemetry.close()
        if other is not None:
            other._telemetry.close()


def test_a_project_state_that_stays_locked_is_reported_with_the_project_lock_code(tmp_path):
    store = FileProjectStateStore(tmp_path)
    store.ensure("shared", SCHEMA)
    impatient(store)
    payload = {"question_id": "q1", "content": "?", "owner": "human"}
    with held_by_another_thread(FileProjectStateStore(tmp_path).locked("shared")):
        with pytest.raises(AgentSdkError) as raised:
            store.apply("shared", transition(T.QUESTION_OPENED, payload, action="q1"))
    assert raised.value.code == "PROJECT_STATE_LOCK_TIMEOUT"
    assert store.apply("shared", transition(T.QUESTION_OPENED, payload, action="q1")).revision == 1


def test_asking_for_a_record_that_does_not_exist_leaves_no_lock_file_behind(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    runtime = ControllerRuntime(tmp_path, coordinator=coordinator)
    orchestrator = Orchestrator(tmp_path, make_policy(), controller_runtime=runtime)
    store = FileProjectStateStore(tmp_path)
    try:
        with pytest.raises(ValueError, match='Run "run-99" is unknown'):
            coordinator.cancel_run("run-99")
        with pytest.raises(ValueError, match='Controller "controller-99" is unknown'):
            runtime.submit_plan("controller-99", simple_plan())
        with pytest.raises(ValueError, match='Orchestration "orchestration-99" is unknown'):
            orchestrator.cancel("orchestration-99", "none")
        with pytest.raises(ValueError, match='Project state "nothing" is unknown'):
            store.apply("nothing", transition(T.QUESTION_OPENED, {}, action="q"))
        assert {path.name for path in tmp_path.rglob("*.lock")} == {"pol.lock"}
    finally:
        orchestrator._telemetry.close()


def test_a_read_only_project_store_cannot_be_locked(tmp_path):
    FileProjectStateStore(tmp_path).ensure("shared", SCHEMA)
    store = FileProjectStateStore(tmp_path, read_only=True)
    with pytest.raises(RuntimeError, match='opened read-only; "locked" is not available'):
        with store.locked("shared"):
            pass


def test_the_controller_runtime_also_works_over_an_in_memory_project_state_store(tmp_path):
    runtime = ControllerRuntime(tmp_path, project_state_store=InMemoryProjectStateStore())
    controller_id = new_controller(runtime).controller_id
    runtime.submit_plan(controller_id, simple_plan())
    runtime.approve_plan(controller_id, True)
    runtime.dispatch(controller_id)
    runtime.record_node_result(controller_id, "node:T1", ok())
    items = {
        item.work_item_id: item.status.value
        for item in runtime.project_state(controller_id).work_items
    }
    assert items == {"node:T1": "completed"}
    runtime.cancel(controller_id, "stop")
    assert runtime.reconcile(controller_id).actions == []
