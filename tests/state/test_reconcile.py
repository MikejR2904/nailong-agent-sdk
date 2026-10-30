import pytest

from nailong_agent_sdk.agent.orchestrator import OrchestrationStatus, Orchestrator
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from tests.support.agents import arun
from tests.support.controllers import executing_runtime
from tests.support.orchestration import (
    close_all,
    make_factory,
    make_policy,
    make_request,
    pipeline,
)
from tests.support.plans import ok


def work_items(runtime, controller_id):
    return {
        item.work_item_id: item.status.value
        for item in runtime.project_state(controller_id).work_items
    }


def changes(report):
    return [(action.store, action.change) for action in report.actions]


def submitted(tmp_path, blast=5):
    orchestrator = Orchestrator(tmp_path, make_policy())
    record = arun(orchestrator.prepare(make_request(blast=blast)))
    return orchestrator, record.orchestration_id


def force(orchestrator, orchestration_id, **fields):
    record = orchestrator.get(orchestration_id)
    orchestrator._store.save(record.model_copy(update=fields))


def test_a_node_result_the_run_holds_but_project_state_lacks_is_written_by_reconcile(tmp_path):
    runtime, controller_id = executing_runtime(tmp_path)
    run_id = runtime.get_controller(controller_id).run_id
    runtime._harness.record_node_result(run_id, "node:T1", ok())
    assert work_items(runtime, controller_id) == {}
    report = runtime.reconcile(controller_id)
    assert changes(report) == [
        ("project-state", 'work item "node:T1" set to completed from the run result')
    ]
    assert work_items(runtime, controller_id) == {"node:T1": "completed"}
    assert runtime.reconcile(controller_id).actions == []


def test_reconcile_cancels_a_controller_whose_run_was_cancelled_and_records_the_cancelled_nodes(
    tmp_path,
):
    runtime, controller_id = executing_runtime(tmp_path)
    run_id = runtime.get_controller(controller_id).run_id
    runtime._harness.cancel_run(run_id)
    report = runtime.reconcile(controller_id)
    assert [store for store, _change in changes(report)].count("controller") == 1
    assert runtime.get_controller(controller_id).phase is ControllerPhase.CANCELLED
    assert work_items(runtime, controller_id)["node:T1"] == "cancelled"
    assert runtime.reconcile(controller_id).actions == []


def test_reconcile_changes_nothing_in_a_consistent_controller(tmp_path):
    runtime, controller_id = executing_runtime(tmp_path)
    runtime.record_node_result(controller_id, "node:T1", ok())
    before = runtime.get_controller(controller_id)
    revision = runtime.project_state(controller_id).revision
    assert runtime.reconcile(controller_id).actions == []
    assert runtime.get_controller(controller_id) == before
    assert runtime.project_state(controller_id).revision == revision


def test_cancelling_a_controller_records_the_nodes_it_cancelled_in_project_state(tmp_path):
    runtime, controller_id = executing_runtime(tmp_path)
    runtime.cancel(controller_id, "stop")
    items = work_items(runtime, controller_id)
    assert items["node:T1"] == "cancelled" and items["node:T3"] == "cancelled"
    assert items["node:T2"] == "blocked"


def test_a_plan_the_controller_holds_for_approval_moves_a_prepared_orchestration_forward(tmp_path):
    orchestrator, oid = submitted(tmp_path)
    try:
        orchestrator.submit_for_approval(oid)
        force(orchestrator, oid, status=OrchestrationStatus.PREPARED)
        report = orchestrator.reconcile(oid)
        assert changes(report) == [
            (
                "orchestration",
                'status "prepared" -> "awaiting-plan-approval": controller "controller-1" holds '
                "the compiled plan for approval",
            )
        ]
        assert orchestrator.get(oid).status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
        assert orchestrator.reconcile(oid).actions == []
    finally:
        orchestrator._telemetry.close()


@pytest.mark.parametrize(
    ("approved", "expected"),
    [(True, OrchestrationStatus.APPROVED), (False, OrchestrationStatus.PREPARED)],
)
def test_a_plan_decision_the_controller_made_reaches_the_orchestration(
    tmp_path, approved, expected
):
    orchestrator, oid = submitted(tmp_path)
    try:
        record = orchestrator.submit_for_approval(oid)
        orchestrator.controller_runtime().approve_plan(record.controller_id, approved)
        assert orchestrator.get(oid).status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
        orchestrator.reconcile(oid)
        assert orchestrator.get(oid).status is expected
    finally:
        orchestrator._telemetry.close()


def test_a_dispatch_the_controller_made_reaches_the_orchestration(tmp_path):
    orchestrator, oid = submitted(tmp_path)
    try:
        record = orchestrator.submit_for_approval(oid)
        orchestrator.approve(oid, True)
        _controller, run = orchestrator.controller_runtime().dispatch(record.controller_id)
        assert orchestrator.get(oid).status is OrchestrationStatus.APPROVED
        orchestrator.reconcile(oid)
        stored = orchestrator.get(oid)
        assert stored.status is OrchestrationStatus.DISPATCHED
        assert stored.graph_run_id == run.run_id
    finally:
        orchestrator._telemetry.close()


@pytest.mark.parametrize("stage", ["prepared", "submitted", "approved"])
def test_a_cancel_the_controller_made_reaches_the_orchestration(tmp_path, stage):
    orchestrator, oid = submitted(tmp_path)
    try:
        record = orchestrator.submit_for_approval(oid)
        if stage == "approved":
            orchestrator.approve(oid, True)
        if stage == "prepared":
            force(orchestrator, oid, status=OrchestrationStatus.PREPARED)
        orchestrator.controller_runtime().cancel(record.controller_id, "stop")
        orchestrator.reconcile(oid)
        assert orchestrator.get(oid).status is OrchestrationStatus.CANCELLED
    finally:
        orchestrator._telemetry.close()


def test_an_orchestration_without_a_controller_has_nothing_to_reconcile(tmp_path):
    orchestrator, oid = submitted(tmp_path)
    try:
        assert orchestrator.reconcile(oid).actions == []
    finally:
        orchestrator._telemetry.close()


def test_a_run_that_finished_settles_a_dispatched_orchestration(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        force(orchestrator, record.orchestration_id, status=OrchestrationStatus.DISPATCHED)
        report = orchestrator.reconcile(record.orchestration_id)
        stored = orchestrator.get(record.orchestration_id)
        assert stored.status is OrchestrationStatus.EXECUTED
        assert stored.graph_run_id == executed.graph_run_id
        assert [store for store, _change in changes(report)] == ["orchestration"]
    finally:
        close_all(orchestrator, services)


def test_a_failed_run_is_not_settled_before_the_controller_recorded_the_failure(tmp_path):
    failing = make_factory(
        model_turns=[{"type": "tool-call", "call": {"id": "c1", "name": "nope", "arguments": {}}}]
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=0), failing
    )
    try:
        oid = record.orchestration_id
        assert executed.status is OrchestrationStatus.FAILED
        runtime = orchestrator.controller_runtime()
        controller = runtime.get_controller(executed.controller_id)
        assert controller.phase is ControllerPhase.REPAIR_REQUIRED
        force(orchestrator, oid, status=OrchestrationStatus.DISPATCHED)
        orchestrator.reconcile(oid)
        assert orchestrator.get(oid).status is OrchestrationStatus.FAILED
        executing = runtime._machine(executed.controller_id)
        executing.record = executing.record.model_copy(update={"phase": ControllerPhase.EXECUTING})
        runtime._controller_store.save(executing.record)
        force(orchestrator, oid, status=OrchestrationStatus.DISPATCHED)
        assert orchestrator.reconcile(oid).actions == []
        assert orchestrator.get(oid).status is OrchestrationStatus.DISPATCHED
    finally:
        close_all(orchestrator, services)


def test_a_run_that_is_still_going_is_not_settled(tmp_path):
    orchestrator, oid = submitted(tmp_path)
    services = AgentRuntimeServices.open(tmp_path)
    try:
        record = orchestrator.submit_for_approval(oid)
        orchestrator.approve(oid, True)
        runtime = orchestrator.controller_runtime()
        runtime.dispatch(record.controller_id)
        orchestrator.reconcile(oid)
        assert orchestrator.get(oid).status is OrchestrationStatus.DISPATCHED
        run = runtime.get_run(record.controller_id)
        assert "runnable" in run.graph["statuses"].values()
        assert orchestrator.reconcile(oid).actions == []
    finally:
        close_all(orchestrator, services)


def test_retrying_an_approval_after_a_failed_write_succeeds_and_records_the_decision(
    tmp_path, monkeypatch
):
    orchestrator, oid = submitted(tmp_path)
    try:
        orchestrator.submit_for_approval(oid)
        real = orchestrator._store.save

        def failing_save(record):
            monkeypatch.setattr(orchestrator._store, "save", real)
            raise OSError("disk full")

        monkeypatch.setattr(orchestrator._store, "save", failing_save)
        with pytest.raises(OSError, match="disk full"):
            orchestrator.approve(oid, True, "ok")
        assert orchestrator.get(oid).status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
        retried = orchestrator.approve(oid, True, "ok")
        assert retried.status is OrchestrationStatus.APPROVED
        with pytest.raises(ValueError, match="not awaiting a plan decision"):
            orchestrator.approve(oid, True, "ok")
    finally:
        orchestrator._telemetry.close()
