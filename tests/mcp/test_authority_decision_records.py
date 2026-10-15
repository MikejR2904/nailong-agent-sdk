from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from tests.support.mcp_authority import (
    Harness,
    deferred_run,
    orchestration_payloads,
    plan_payload,
    stamp,
)


def telemetry_events(harness, run_id, *event_types):
    result = harness.as_approver(
        "get_telemetry_events", {"run_id": run_id, "event_types": list(event_types)}
    )
    assert result["ok"], result
    return result["events"]


def test_a_plan_decision_names_the_principal_in_the_controller_log_and_telemetry(harness):
    controller_id = harness.controller("snap-source-plan")
    decided = harness.as_approver(
        "approve_controller_plan", harness.decided(controller_id, reason="reviewed")
    )
    assert decided["ok"], decided
    principal = harness.approver.principal_id
    recorded = next(
        e for e in decided["controller"]["events"] if e["type"] == "plan-approval-recorded"
    )
    assert recorded["details"] == {
        "approved": True,
        "reason": "reviewed",
        "decided_by": principal,
        "decision_authority": "human",
    }
    (event,) = telemetry_events(harness, controller_id, "controller.plan-approved")
    assert event["authority"] == "human"
    assert event["payload"]["decided_by"] == principal
    assert decided["decision_record"]["authority"] == "approver"
    assert decided["decision_record"]["principal"] == principal


def test_a_run_approval_names_the_principal_that_decided_it(harness):
    started = harness.as_host("start_run", {"plan": plan_payload()})
    run_id = started["run"]["run_id"]
    requested = (
        HarnessCoordinator(harness.run_root)
        .approvals(run_id)
        .request(run_id, "node:T1", "draft.write", "writes the draft")
    )
    listed = harness.as_approver("list_run_approvals", {"run_id": run_id})
    item = next(a for a in listed["approvals"] if a["approval_id"] == requested.approval_id)
    assert item["decided_by"] is None and item["decision_authority"] is None
    decided = harness.as_approver(
        "submit_approval",
        {
            "run_id": run_id,
            "approval_id": requested.approval_id,
            "approved": True,
            "request_hash": item["request_hash"],
            "issued_at_utc": stamp(),
        },
    )
    assert decided["ok"], decided
    assert decided["approval"]["decided_by"] == harness.approver.principal_id
    assert decided["approval"]["decision_authority"] == "human"
    relisted = harness.as_approver("list_run_approvals", {"run_id": run_id})["approvals"]
    stored = next(a for a in relisted if a["approval_id"] == requested.approval_id)
    assert stored["decided_by"] == harness.approver.principal_id


def test_an_elastic_grant_names_the_principal_in_its_telemetry(harness):
    controller_id, run_id = deferred_run(harness, "snap-source-grant")
    seen = harness.as_approver("get_run_state", {"run_id": run_id})["run"]["run_hash"]
    granted = harness.as_approver(
        "grant_elastic_capacity",
        {
            "controller_id": controller_id,
            "max_elastic_depth": 1,
            "reason": "designer approved",
            "run_hash": seen,
            "issued_at_utc": stamp(),
        },
    )
    assert granted["ok"], granted
    (event,) = telemetry_events(harness, run_id, "graph.elastic-capacity-granted")
    assert event["authority"] == "human"
    assert event["payload"]["decided_by"] == harness.approver.principal_id
    assert event["payload"]["decision_authority"] == "human"
    assert event["payload"]["reason"] == "designer approved"


def test_declining_elastic_requests_names_the_principal_in_its_telemetry(harness):
    controller_id, run_id = deferred_run(harness, "snap-source-decline")
    seen = harness.as_approver("get_run_state", {"run_id": run_id})["run"]["run_hash"]
    declined = harness.as_approver(
        "decline_elastic_requests",
        {
            "controller_id": controller_id,
            "parent_node_id": "node:T1",
            "reason": "out of budget",
            "run_hash": seen,
            "issued_at_utc": stamp(),
        },
    )
    assert declined["ok"], declined
    (event,) = telemetry_events(harness, run_id, "graph.elastic-requests-declined")
    assert event["authority"] == "human"
    assert event["payload"]["decided_by"] == harness.approver.principal_id
    assert event["payload"]["reason"] == "out of budget"


def test_an_orchestration_decision_names_the_principal(harness):
    policy, request = orchestration_payloads("snap-source-orchestration")
    prepared = harness.as_host("prepare_orchestration", {"policy": policy, "request": request})
    orchestration_id = prepared["orchestration"]["orchestration_id"]
    assert harness.as_host(
        "submit_orchestration_for_approval", {"orchestration_id": orchestration_id}
    )["ok"]
    plan_hash = harness.as_approver("get_orchestration", {"orchestration_id": orchestration_id})[
        "binding"
    ]["plan_hash"]
    decided = harness.as_approver(
        "approve_orchestration",
        {
            "orchestration_id": orchestration_id,
            "approved": True,
            "plan_hash": plan_hash,
            "issued_at_utc": stamp(),
        },
    )
    assert decided["ok"], decided
    (event,) = telemetry_events(harness, orchestration_id, "orchestration.plan-decision")
    assert event["authority"] == "human"
    assert event["payload"]["decided_by"] == harness.approver.principal_id
    assert event["status"] == "approved"


def test_a_human_project_decision_names_the_principal_in_the_state_history(harness):
    schema = {"schema_id": "s-v1", "stage": "design"}
    assert harness.as_host(
        "initialize_project_state", {"project_id": "p-source", "stage_schema": schema}
    )["ok"]
    seen = harness.as_approver("get_project_state", {"project_id": "p-source"})["state"][
        "state_hash"
    ]
    applied = harness.as_approver(
        "record_human_project_decision",
        {
            "project_id": "p-source",
            "decision_id": "d1",
            "content": "use X",
            "status": "locked",
            "evidence_id": "e-1",
            "state_hash": seen,
            "issued_at_utc": stamp(),
        },
    )
    assert applied["ok"], applied
    summary = applied["state"]["last_action"]["summary"]
    assert summary["decided_by"] == harness.approver.principal_id
    assert summary["decision_authority"] == "human"


def test_a_local_process_decision_is_recorded_as_the_local_principal(tmp_path):
    local = Harness(tmp_path, approver_configured=False, require_principal=False)
    controller_id = local.controller("snap-source-local")
    decided = local.call(
        "approve_controller_plan", {"controller_id": controller_id, "approved": True}
    )
    assert decided["ok"], decided
    recorded = next(
        e for e in decided["controller"]["events"] if e["type"] == "plan-approval-recorded"
    )
    assert recorded["details"]["decided_by"] == "local-process"
    assert recorded["details"]["decision_authority"] == "human"
