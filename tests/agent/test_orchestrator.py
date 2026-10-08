import time

import pytest

from nailong_agent_sdk.agent.orchestrator import (
    OrchestrationStatus,
    Orchestrator,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import (
    ModelBinding,
)
from nailong_agent_sdk.state.orchestration_models import (
    ControllerPhase,
    WorkflowArchitecture,
)
from nailong_agent_sdk.state.planning import Plan
from tests.support.agents import arun
from tests.support.orchestration import (
    close_all,
    independent_plan,
    make_factory,
    make_policy,
    make_request,
    pipeline,
)
from tests.support.plans import task as plan_task


def test_multi_agent_pipeline_end_to_end(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        assert (
            record.architecture is WorkflowArchitecture.MULTI_AGENT and len(record.assignments) == 3
        )
        assert executed.status is OrchestrationStatus.EXECUTED and executed.graph_run_id == "run-1"
        controller = orchestrator.controller_runtime().get_controller(executed.controller_id)
        assert controller.phase is ControllerPhase.EXECUTING
        run = orchestrator.controller_runtime()._harness.get_run_state(executed.graph_run_id)
        assert set(run.graph["statuses"].values()) == {"completed"}
        assert all(
            r["status"] == "completed" and r["provenance_hash"]
            for r in run.graph["results"].values()
        )
        items = {
            w.work_item_id: w.status.value
            for w in orchestrator.controller_runtime()
            .project_state(executed.controller_id)
            .work_items
        }
        assert items == {"node:T1": "completed", "node:T2": "completed", "node:T3": "completed"}
        events = [
            e.event_type for e in services.telemetry.list_events(record.orchestration_id, limit=100)
        ]
        assert events == [
            "orchestration.prepared",
            "orchestration.plan-presented",
            "orchestration.plan-decision",
            "orchestration.dispatched",
            "orchestration.executed",
        ]
        persisted = orchestrator.get(record.orchestration_id)
        assert persisted.status is OrchestrationStatus.EXECUTED
        orchestrator.controller_runtime().complete(executed.controller_id)
    finally:
        close_all(orchestrator, services)


def test_single_agent_collapse_runs_one_worker(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=0), make_factory()
    )
    try:
        assert (
            record.architecture is WorkflowArchitecture.SINGLE_AGENT
            and len(record.assignments) == 1
        )
        assert record.assignments[0].node_id == "node:single-plan"
        assert record.execution_plan.tasks[0].locked_interface["orchestrated_task_ids"] == [
            "T1",
            "T2",
            "T3",
        ]
        run = orchestrator.controller_runtime()._harness.get_run_state(executed.graph_run_id)
        assert list(run.graph["statuses"]) == ["node:single-plan"]
    finally:
        close_all(orchestrator, services)


def test_worker_failure_is_not_reported_as_a_successful_execution(tmp_path):
    factory = make_factory(model_turns=[{"type": "blocked", "reason": "cannot proceed"}])
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), factory
    )
    try:
        controller = orchestrator.controller_runtime().get_controller(executed.controller_id)
        assert (
            executed.status is not OrchestrationStatus.EXECUTED
            or controller.phase is not ControllerPhase.EXECUTING
        )
    finally:
        close_all(orchestrator, services)


def test_failed_workers_trigger_repair_phase(tmp_path):
    factory = make_factory(
        model_turns=[{"type": "tool-call", "call": {"id": "c1", "name": "nope", "arguments": {}}}]
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), factory
    )
    try:
        controller = orchestrator.controller_runtime().get_controller(executed.controller_id)
        assert controller.phase is ControllerPhase.REPAIR_REQUIRED
    finally:
        close_all(orchestrator, services)


def test_max_parallel_agents_is_enforced(tmp_path):
    concurrency = {"now": 0, "max": 0}
    policy = make_policy(max_total=12, max_parallel=2)
    orchestrator, record, executed, services = pipeline(
        tmp_path,
        policy,
        make_request(plan=independent_plan(6), blast=5),
        make_factory(concurrency=concurrency),
    )
    try:
        assert 1 <= concurrency["max"] <= 2
    finally:
        close_all(orchestrator, services)


def test_policy_violations_are_rejected_at_prepare(tmp_path):
    def prepare(policy, request):
        orchestrator = Orchestrator(tmp_path / f"o{time.monotonic_ns()}", policy)
        try:
            return arun(orchestrator.prepare(request))
        finally:
            orchestrator._telemetry.close()

    with pytest.raises(ValueError, match="unknown skills"):
        prepare(make_policy(), make_request(skills=("ghost",)))
    with pytest.raises(ValueError, match="not authorized by any assignment profile"):
        prepare(make_policy(), make_request(skills=("s2",)).model_copy(update={}))
    with pytest.raises(ValueError, match="exceeds policy max_total_agents"):
        prepare(
            make_policy(max_total=2, max_parallel=2),
            make_request(plan=independent_plan(3), blast=5),
        )
    with pytest.raises(ValueError, match="max_instances"):
        prepare(make_policy(max_instances=1), make_request(plan=independent_plan(2), blast=5))
    with pytest.raises(ValueError, match="disables multi-agent"):
        prepare(make_policy(multi=False), make_request(blast=5))
    with pytest.raises(ValueError, match="Plan validation failed"):
        prepare(
            make_policy(),
            make_request(plan=Plan(plan_id="bad", tasks=[plan_task("T1", deps=["ghost"])])),
        )
    with pytest.raises(ValueError, match="names unregistered tool"):
        Orchestrator(tmp_path / "t1", make_policy(tools=("nonexistent",)))
    with pytest.raises(ValueError, match='lacks capability "utility.brief"'):
        Orchestrator(tmp_path / "t2", make_policy(capabilities=()))
    with pytest.raises(ValueError, match="no user profile|No user profile|No stage profile"):
        request = make_request().model_copy(update={"stage": "other"})
        prepare(make_policy(), request)


def test_binding_validation_blocks_authority_escalation(tmp_path):
    cases = [
        (make_factory(identity_override="impostor"), "identity does not equal"),
        (make_factory(tools=("brief", "web_fetch")), "tools outside the user profile"),
        (make_factory(node_override="node:elsewhere"), "declared graph node"),
        (
            make_factory(binding_override=ModelBinding(provider="evil", model="x")),
            "model does not equal",
        ),
    ]
    for factory, fragment in cases:
        run_root = tmp_path / f"b{time.monotonic_ns()}"
        orchestrator = Orchestrator(run_root, make_policy())
        services = AgentRuntimeServices.open(run_root)
        try:
            record = arun(orchestrator.prepare(make_request(blast=5)))
            orchestrator.submit_for_approval(record.orchestration_id)
            orchestrator.approve(record.orchestration_id, True)
            with pytest.raises(ValueError, match=fragment):
                arun(orchestrator.dispatch_and_execute(record.orchestration_id, services, factory))
        finally:
            close_all(orchestrator, services)


def test_dispatch_requires_approval_and_matching_run_root(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    services = AgentRuntimeServices.open(tmp_path)
    other = AgentRuntimeServices.open(tmp_path / "other")
    try:
        record = arun(orchestrator.prepare(make_request(blast=5)))
        with pytest.raises(ValueError, match="Only an approved orchestration can dispatch"):
            arun(
                orchestrator.dispatch_and_execute(record.orchestration_id, services, make_factory())
            )
        orchestrator.submit_for_approval(record.orchestration_id)
        with pytest.raises(ValueError, match="Only an approved orchestration"):
            arun(
                orchestrator.dispatch_and_execute(record.orchestration_id, services, make_factory())
            )
        orchestrator.approve(record.orchestration_id, True)
        with pytest.raises(ValueError, match="must use the orchestrator run_root"):
            arun(orchestrator.dispatch_and_execute(record.orchestration_id, other, make_factory()))
        with pytest.raises(ValueError, match='Orchestration "ghost" is unknown'):
            orchestrator.get("ghost")
    finally:
        close_all(orchestrator, services)
        other.telemetry.close()


def test_rejection_returns_to_prepared_and_can_be_resubmitted(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    try:
        record = arun(orchestrator.prepare(make_request(blast=5)))
        orchestrator.submit_for_approval(record.orchestration_id)
        rejected = orchestrator.approve(record.orchestration_id, False, "rework")
        assert rejected.status is OrchestrationStatus.PREPARED
        again = orchestrator.submit_for_approval(record.orchestration_id)
        assert again.status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
        orchestrator.approve(record.orchestration_id, True)
        with pytest.raises(ValueError, match="not awaiting a plan decision"):
            orchestrator.approve(record.orchestration_id, True)
    finally:
        orchestrator._telemetry.close()


def test_resume_after_restart_continues_the_same_orchestration(tmp_path):
    first = Orchestrator(tmp_path, make_policy())
    record = arun(first.prepare(make_request(blast=5)))
    first.submit_for_approval(record.orchestration_id)
    first._telemetry.close()
    second = Orchestrator.resume(tmp_path, record.orchestration_id)
    services = AgentRuntimeServices.open(tmp_path)
    try:
        assert (
            second.get(record.orchestration_id).status is OrchestrationStatus.AWAITING_PLAN_APPROVAL
        )
        second.approve(record.orchestration_id, True)
        executed = arun(
            second.dispatch_and_execute(record.orchestration_id, services, make_factory()),
            timeout=300,
        )
        assert executed.status is OrchestrationStatus.EXECUTED
    finally:
        close_all(second, services)


def test_policy_rewrite_under_the_same_id_is_rejected(tmp_path):
    first = Orchestrator(tmp_path, make_policy())
    first._telemetry.close()
    with pytest.raises(ValueError, match="already exists with different authority fields"):
        Orchestrator(tmp_path, make_policy(max_total=3, max_parallel=2))


def test_cancel_paths(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    try:
        record = arun(orchestrator.prepare(make_request(blast=5)))
        cancelled = orchestrator.cancel(record.orchestration_id, "no longer needed")
        assert cancelled.status is OrchestrationStatus.CANCELLED
        record2 = arun(orchestrator.prepare(make_request(blast=5)))
        orchestrator.submit_for_approval(record2.orchestration_id)
        cancelled2 = orchestrator.cancel(record2.orchestration_id, "stop")
        assert cancelled2.status is OrchestrationStatus.CANCELLED
        controller = orchestrator.controller_runtime().get_controller(
            record2.controller_id or cancelled2.controller_id
        )
        assert controller.phase is ControllerPhase.CANCELLED
    finally:
        orchestrator._telemetry.close()


def test_an_orchestration_whose_controller_completed_cannot_be_cancelled(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        runtime = orchestrator.controller_runtime()
        runtime.complete(executed.controller_id)
        before = runtime._harness.get_run_state(executed.graph_run_id)
        with pytest.raises(
            ValueError,
            match=rf'Controller "{executed.controller_id}" is already completed, a terminal phase',
        ):
            orchestrator.cancel(record.orchestration_id, "too late")
        assert orchestrator.get(record.orchestration_id).status is OrchestrationStatus.EXECUTED
        after = runtime._harness.get_run_state(executed.graph_run_id)
        assert after.cancelled is False and after.run_hash == before.run_hash
        events = [
            e.event_type for e in services.telemetry.list_events(record.orchestration_id, limit=100)
        ]
        assert "orchestration.cancelled" not in events
    finally:
        close_all(orchestrator, services)
