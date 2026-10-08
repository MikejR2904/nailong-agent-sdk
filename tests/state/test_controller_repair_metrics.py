import pytest

from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from nailong_agent_sdk.state.shared_state import ProvenanceRecord, provenance_hash
from nailong_agent_sdk.state.stage_gates import StageCompletenessPolicy
from tests.support.controllers import new_controller
from tests.support.plans import AGENT, arun, simple_plan

ELASTIC = GraphNodeKind.ELASTIC


def executing(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    runtime = ControllerRuntime(tmp_path, telemetry=telemetry)
    controller = new_controller(runtime)
    runtime.submit_plan(controller.controller_id, simple_plan())
    runtime.approve_plan(controller.controller_id, True)
    runtime.dispatch(controller.controller_id)
    return telemetry, runtime, controller.controller_id


def values(telemetry, runtime, controller_id, metric_id):
    run_id = runtime.get_controller(controller_id).run_id
    return [m.value for m in telemetry.list_metrics(run_id) if m.metric_id == metric_id]


def stage_failure_events(telemetry, runtime, controller_id):
    run_id = runtime.get_controller(controller_id).run_id
    return [
        event
        for event in telemetry.list_events(run_id, limit=500)
        if event.event_type == "controller.stage-failure"
    ]


def rejected_record():
    fields = ("node:T1", [], [], [], [], [], "v0", "result-hash")
    return ProvenanceRecord(
        node_id="node:T1",
        result_schema_version="v0",
        result_hash="result-hash",
        hash=provenance_hash(*fields),
    )


async def failing(node, context):
    return GraphNodeResult(status=GraphNodeStatus.FAILED, reason="probe crashed")


@pytest.mark.parametrize("path", ["explicit", "completeness", "provenance", "graph-failure"])
def test_every_path_that_enters_repair_records_the_repair_metrics(tmp_path, path):
    telemetry, runtime, cid = executing(tmp_path)
    try:
        if path == "explicit":
            runtime.record_stage_failure(cid, "the designer asked for a repair")
        elif path == "completeness":
            policy = StageCompletenessPolicy(
                policy_id="p", stage="design", required_artifact_paths=["missing.md"]
            )
            assert not runtime.evaluate_stage_completeness(cid, policy).complete
        elif path == "provenance":
            decision = runtime.verify_provenance_contract(cid, [rejected_record()], "v1")
            assert not decision.accepted
        else:
            arun(runtime.execute_graph(cid, {AGENT: failing, ELASTIC: failing}))
        assert runtime.get_controller(cid).phase is ControllerPhase.REPAIR_REQUIRED
        assert values(telemetry, runtime, cid, "controller.repair_attempt_count") == [1.0]
        assert values(telemetry, runtime, cid, "controller.escalation_count") == [0.0]
        (event,) = stage_failure_events(telemetry, runtime, cid)
        assert event.status == "repair-required" and event.payload["reason"]
    finally:
        telemetry.close()


def test_the_failure_that_passes_the_repair_cap_records_the_escalation(tmp_path):
    telemetry, runtime, cid = executing(tmp_path)
    try:
        policy = StageCompletenessPolicy(
            policy_id="p", stage="design", required_artifact_paths=["missing.md"]
        )
        first_run = runtime.get_controller(cid).run_id
        runtime.evaluate_stage_completeness(cid, policy)
        assert runtime.get_controller(cid).phase is ControllerPhase.REPAIR_REQUIRED
        runtime.submit_plan(cid, simple_plan("repaired"))
        runtime.approve_plan(cid, True)
        runtime.dispatch(cid)
        second_run = runtime.get_controller(cid).run_id
        assert second_run != first_run
        runtime.evaluate_stage_completeness(cid, policy)
        assert runtime.get_controller(cid).phase is ControllerPhase.ESCALATED

        def recorded(run_id, metric_id):
            return [m.value for m in telemetry.list_metrics(run_id) if m.metric_id == metric_id]

        assert recorded(first_run, "controller.escalation_count") == [0.0]
        assert recorded(second_run, "controller.escalation_count") == [1.0]
        assert recorded(second_run, "controller.repair_attempt_count") == [1.0]
    finally:
        telemetry.close()
