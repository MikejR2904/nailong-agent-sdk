import asyncio
import re

from nailong_agent_sdk.mcp import authority
from nailong_agent_sdk.mcp.authority import authenticated_as
from tests.support.mcp_authority import (
    DECISIONS,
    deferred_run,
    error_of,
    orchestration_payloads,
    plan_payload,
    stamp,
)


def test_project_state_decisions_are_bound_to_the_state_the_approver_saw(harness):
    schema = {"schema_id": "s-v1", "stage": "design"}
    assert harness.as_host(
        "initialize_project_state", {"project_id": "p-1", "stage_schema": schema}
    )
    seen = harness.as_approver("get_project_state", {"project_id": "p-1"})["state"]["state_hash"]
    assert harness.as_host(
        "open_project_question",
        {
            "project_id": "p-1",
            "question_id": "q1",
            "content": "why?",
            "owner": "human",
            "evidence_id": "e-1",
        },
    )["ok"]
    sent = {
        "project_id": "p-1",
        "decision_id": "d1",
        "content": "use X",
        "status": "locked",
        "evidence_id": "e-2",
        "state_hash": seen,
        "issued_at_utc": stamp(),
    }
    stale = error_of(harness.as_approver("record_human_project_decision", sent))
    assert stale["type"] == "ApprovalStale" and "state_hash" in stale["message"]
    current = harness.as_approver("get_project_state", {"project_id": "p-1"})["state"]["state_hash"]
    applied = harness.as_approver(
        "record_human_project_decision", {**sent, "state_hash": current, "issued_at_utc": stamp()}
    )
    assert applied["ok"] and applied["state"]["decisions"][0]["authority"] == "human"
    assert applied["decision_record"]["principal"] == harness.approver.principal_id
    replay = error_of(harness.as_approver("record_human_project_decision", sent))
    assert replay["type"] == "ApprovalStale"
    refused = harness.as_host("record_human_project_decision", sent)
    assert error_of(refused)["type"] == "OperationNotPermitted"


def test_concurrent_decisions_bound_to_one_state_apply_exactly_once(harness):
    schema = {"schema_id": "s-v1", "stage": "design"}
    assert harness.as_host(
        "initialize_project_state", {"project_id": "p-race", "stage_schema": schema}
    )
    seen = harness.as_approver("get_project_state", {"project_id": "p-race"})["state"]["state_hash"]

    async def race():
        with authenticated_as(harness.approver):
            return await asyncio.gather(
                *[
                    harness.server.call_tool(
                        "record_human_project_decision",
                        {
                            "project_id": "p-race",
                            "decision_id": f"d{index}",
                            "content": f"choice {index}",
                            "status": "locked",
                            "evidence_id": f"e{index}",
                            "state_hash": seen,
                            "issued_at_utc": stamp(),
                        },
                    )
                    for index in range(8)
                ]
            )

    results = [item.structured_content for item in asyncio.run(race())]
    applied = [item for item in results if item["ok"]]
    refused = [item for item in results if not item["ok"]]
    assert len(applied) == 1, [item.get("errors") for item in refused]
    assert {item["errors"][0]["type"] for item in refused} == {"ApprovalStale"}
    state = harness.as_approver("get_project_state", {"project_id": "p-race"})["state"]
    assert state["revision"] == 1 and len(state["decisions"]) == 1


def test_concurrent_plan_approvals_apply_exactly_once(harness):
    controller_id = harness.controller("snap-plan-race")
    sent = harness.decided(controller_id)

    async def race():
        with authenticated_as(harness.approver):
            return await asyncio.gather(
                *[harness.server.call_tool("approve_controller_plan", sent) for _ in range(8)]
            )

    results = [item.structured_content for item in asyncio.run(race())]
    assert sum(1 for item in results if item["ok"]) == 1
    applied = [e for e in harness.decisions() if e["event_type"] == "mcp.decision.applied"]
    assert len(applied) == 1
    assert harness.phase(controller_id) == "dispatch-ready"


def test_run_approvals_list_a_hash_and_the_decision_must_match_it(harness):
    from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator

    started = harness.as_host("start_run", {"plan": plan_payload()})
    run_id = started["run"]["run_id"]
    requested = (
        HarnessCoordinator(harness.run_root)
        .approvals(run_id)
        .request(run_id, "node:T1", "draft.write", "writes the draft")
    )
    listed = harness.as_approver("list_run_approvals", {"run_id": run_id})
    assert listed["ok"]
    item = next(a for a in listed["approvals"] if a["approval_id"] == requested.approval_id)
    assert re.fullmatch(r"[0-9a-f]{64}", item["request_hash"])
    sent = {
        "run_id": run_id,
        "approval_id": requested.approval_id,
        "approved": True,
        "request_hash": "f" * 64,
        "issued_at_utc": stamp(),
    }
    stale = error_of(harness.as_approver("submit_approval", sent))
    assert stale["type"] == "ApprovalStale"
    decided = harness.as_approver("submit_approval", {**sent, "request_hash": item["request_hash"]})
    assert decided["ok"] and decided["approval"]["status"] == "approved"
    again = error_of(
        harness.as_approver("submit_approval", {**sent, "request_hash": item["request_hash"]})
    )
    assert "already has the decision" in again["message"]


def test_an_elastic_grant_is_bound_to_the_run_state_the_approver_saw(harness):
    controller_id, run_id = deferred_run(harness, "snap-elastic-bound")
    seen = harness.as_approver("get_run_state", {"run_id": run_id})["run"]["run_hash"]
    sent = {
        "controller_id": controller_id,
        "max_elastic_depth": 1,
        "reason": "designer approved",
        "run_hash": seen,
        "issued_at_utc": stamp(),
    }
    assert error_of(harness.as_host("grant_elastic_capacity", sent))["type"] == (
        "OperationNotPermitted"
    )
    missing = error_of(
        harness.as_approver(
            "grant_elastic_capacity", {k: v for k, v in sent.items() if k != "run_hash"}
        )
    )
    assert missing["type"] == "ApprovalBindingRequired" and "run_hash" in missing["message"]
    wrong = error_of(harness.as_approver("grant_elastic_capacity", {**sent, "run_hash": "0" * 64}))
    assert wrong["type"] == "ApprovalStale"
    granted = harness.as_approver("grant_elastic_capacity", sent)
    assert granted["ok"], granted
    assert granted["run"]["graph"]["spawn_records"][0]["status"] == "accepted"
    assert granted["decision_record"]["authority"] == "approver"


def test_declining_elastic_requests_needs_the_approver_and_a_current_run_hash(harness):
    controller_id, run_id = deferred_run(harness, "snap-elastic-decline-bound")
    seen = harness.as_approver("get_run_state", {"run_id": run_id})["run"]["run_hash"]
    sent = {
        "controller_id": controller_id,
        "parent_node_id": "node:T1",
        "reason": "out of budget",
        "run_hash": seen,
        "issued_at_utc": stamp(),
    }
    assert error_of(harness.as_host("decline_elastic_requests", sent))["type"] == (
        "OperationNotPermitted"
    )
    declined = harness.as_approver("decline_elastic_requests", sent)
    assert declined["ok"] and declined["run"]["graph"]["spawn_records"][0]["status"] == (
        "discarded"
    )
    replay = error_of(harness.as_approver("decline_elastic_requests", sent))
    assert replay["type"] == "ApprovalStale"


def test_an_orchestration_decision_is_bound_to_its_plan_and_needs_the_approver(harness):
    policy, request = orchestration_payloads("snap-orchestration-authority")
    prepared = harness.as_host("prepare_orchestration", {"policy": policy, "request": request})
    assert prepared["ok"], prepared
    orchestration_id = prepared["orchestration"]["orchestration_id"]
    assert harness.as_host(
        "submit_orchestration_for_approval", {"orchestration_id": orchestration_id}
    )
    viewed = harness.as_approver("get_orchestration", {"orchestration_id": orchestration_id})
    plan_hash = viewed["binding"]["plan_hash"]
    sent = {
        "orchestration_id": orchestration_id,
        "approved": True,
        "plan_hash": plan_hash,
        "issued_at_utc": stamp(),
    }
    assert error_of(harness.as_host("approve_orchestration", sent))["type"] == (
        "OperationNotPermitted"
    )
    stale = error_of(harness.as_approver("approve_orchestration", {**sent, "plan_hash": "1" * 64}))
    assert stale["type"] == "ApprovalStale"
    decided = harness.as_approver("approve_orchestration", sent)
    assert decided["ok"] and decided["orchestration"]["status"] == "approved"
    assert decided["decision_record"]["principal"] == harness.approver.principal_id


def test_the_decision_stream_is_never_pruned(harness):
    controller_id = harness.controller("snap-protected-stream")
    assert harness.as_approver("approve_controller_plan", harness.decided(controller_id))["ok"]
    report = harness.as_host(
        "prune_runs",
        {"older_than_seconds": 0.001, "dry_run": False, "include_unfinished": True},
    )
    assert report["ok"]
    assert DECISIONS not in [run["run_id"] for run in report["report"]["runs"]]
    assert any("protected_run_ids" in reason for reason in report["report"]["kept"])
    assert harness.decisions()


def test_a_failed_outcome_audit_is_reported_to_the_caller_not_swallowed(harness, monkeypatch):
    controller_id = harness.controller("snap-audit-warning")
    original = authority.DecisionGate._append

    def flaky(self, event_type, *args, **kwargs):
        if event_type == "mcp.decision.applied":
            raise OSError("the audit volume is full")
        return original(self, event_type, *args, **kwargs)

    monkeypatch.setattr(authority.DecisionGate, "_append", flaky)
    decided = harness.as_approver("approve_controller_plan", harness.decided(controller_id))
    assert decided["ok"] and decided["controller"]["phase"] == "dispatch-ready"
    warning = decided["decision_record"]["warning"]
    assert "OSError: the audit volume is full" in warning and DECISIONS in warning


def test_a_decision_is_not_applied_when_its_authorization_cannot_be_recorded(harness, monkeypatch):
    controller_id = harness.controller("snap-audit-closed")
    original = authority.DecisionGate._append

    def broken(self, event_type, *args, **kwargs):
        if event_type == "mcp.decision.authorized":
            raise OSError("the audit volume is full")
        return original(self, event_type, *args, **kwargs)

    monkeypatch.setattr(authority.DecisionGate, "_append", broken)
    error = error_of(harness.as_approver("approve_controller_plan", harness.decided(controller_id)))
    assert error["type"] == "OSError" and "audit volume is full" in error["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"
