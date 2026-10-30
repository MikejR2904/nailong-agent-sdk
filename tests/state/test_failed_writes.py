import pytest

from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from tests.support.controllers import new_controller
from tests.support.plans import AGENT, arun, ok, run_ok, simple_plan


def fail_next(monkeypatch, target, name, error):
    real = getattr(target, name)
    pending = [error]

    def wrapper(*args, **kwargs):
        if pending:
            raise pending.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(target, name, wrapper)


def durable(tmp_path, cid):
    record = ControllerRuntime(tmp_path).get_controller(cid)
    return record.model_dump(exclude={"events_entry_count", "events_integrity_hash"})


def live(runtime, cid):
    record = runtime.get_controller(cid)
    return record.model_dump(exclude={"events_entry_count", "events_integrity_hash"})


def runtime_at(tmp_path, stage):
    runtime = ControllerRuntime(tmp_path)
    cid = new_controller(runtime).controller_id
    if stage == "planning":
        return runtime, cid
    runtime.submit_plan(cid, simple_plan())
    if stage == "awaiting-approval":
        return runtime, cid
    runtime.approve_plan(cid, True)
    if stage == "dispatch-ready":
        return runtime, cid
    runtime.dispatch(cid)
    if stage == "executing":
        return runtime, cid
    arun(runtime.execute_graph(cid, {AGENT: run_ok}))
    return runtime, cid


OPERATIONS = {
    "submit_plan": ("planning", lambda runtime, cid: runtime.submit_plan(cid, simple_plan())),
    "apply_advisory_architecture": (
        "planning",
        lambda runtime, cid: runtime.apply_advisory_architecture(cid, "multi-agent", "fan out"),
    ),
    "approve_plan": ("awaiting-approval", lambda runtime, cid: runtime.approve_plan(cid, True)),
    "dispatch": ("dispatch-ready", lambda runtime, cid: runtime.dispatch(cid)),
    "record_stage_failure": (
        "executing",
        lambda runtime, cid: runtime.record_stage_failure(cid, "boom"),
    ),
    "cancel": ("executing", lambda runtime, cid: runtime.cancel(cid, "stop")),
    "complete": ("executed", lambda runtime, cid: runtime.complete(cid)),
}


@pytest.mark.parametrize("name", OPERATIONS)
def test_a_save_that_fails_leaves_the_controller_exactly_where_it_was(tmp_path, monkeypatch, name):
    stage, operation = OPERATIONS[name]
    runtime, cid = runtime_at(tmp_path, stage)
    before = live(runtime, cid)
    assert durable(tmp_path, cid) == before
    fail_next(monkeypatch, runtime._controller_store, "save", OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        operation(runtime, cid)
    assert live(runtime, cid) == before
    assert durable(tmp_path, cid) == before
    operation(runtime, cid)
    after = live(runtime, cid)
    assert after != before
    assert durable(tmp_path, cid) == after


def test_a_run_that_cannot_start_leaves_the_controller_ready_to_dispatch_again(
    tmp_path, monkeypatch
):
    runtime, cid = runtime_at(tmp_path, "dispatch-ready")
    before = live(runtime, cid)
    fail_next(monkeypatch, runtime._harness, "start_run", OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        runtime.dispatch(cid)
    assert live(runtime, cid) == before
    assert runtime.get_controller(cid).phase is ControllerPhase.DISPATCH_READY
    record, run = runtime.dispatch(cid)
    assert record.phase is ControllerPhase.EXECUTING and record.run_id == run.run_id


def test_a_controller_that_could_not_be_saved_is_not_kept_in_memory(tmp_path, monkeypatch):
    runtime = ControllerRuntime(tmp_path)
    fail_next(monkeypatch, runtime._controller_store, "save", OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        new_controller(runtime)
    with pytest.raises(ValueError, match='Controller "controller-1" is unknown'):
        runtime.get_controller("controller-1")


def test_a_run_change_that_could_not_be_saved_is_not_kept_in_the_cached_graph(
    tmp_path, monkeypatch
):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    fail_next(monkeypatch, coordinator._store, "save", OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        coordinator.record_node_result(run_id, "node:T1", ok())
    assert coordinator.get_run_state(run_id).graph["statuses"]["node:T1"] == "runnable"
    committed = coordinator.record_node_result(run_id, "node:T1", ok())
    assert committed.graph["statuses"]["node:T1"] == "completed"
    assert HarnessCoordinator(tmp_path).get_run_state(run_id).run_hash == committed.run_hash
