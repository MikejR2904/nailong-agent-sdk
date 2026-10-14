import asyncio
import json

from nailong_agent_sdk.mcp.server import create_mcp_server
from tests.support.mcp_authority import (
    APPROVER_SECRET,
    DECISIONS,
    HOST_SECRET,
    RULES,
    SNAPSHOT,
    Harness,
    error_of,
    plan_payload,
    stamp,
)


def test_an_approver_decision_must_carry_its_binding_and_its_issue_time(harness):
    controller_id = harness.controller("snap-binding-required")
    bare = {"controller_id": controller_id, "approved": True}
    error = error_of(harness.as_approver("approve_controller_plan", bare))
    assert error["type"] == "ApprovalBindingRequired" and "plan_hash" in error["message"]
    hashed = {**bare, "plan_hash": harness.plan_hash(controller_id)}
    error = error_of(harness.as_approver("approve_controller_plan", hashed))
    assert error["type"] == "ApprovalBindingRequired" and "issued_at_utc" in error["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"
    refused = [e for e in harness.decisions() if e["event_type"] == "mcp.decision.refused"]
    assert [e["payload"]["reason_type"] for e in refused] == [
        "ApprovalBindingRequired",
        "ApprovalBindingRequired",
    ]


def test_a_bound_and_fresh_approver_decision_is_applied_and_audited(harness):
    controller_id = harness.controller("snap-applied")
    sent = harness.decided(controller_id, reason="reviewed", approver_label="reviewer-7")
    decided = harness.as_approver("approve_controller_plan", sent)
    assert decided["ok"] and decided["controller"]["phase"] == "dispatch-ready"
    record = decided["decision_record"]
    assert record["authority"] == "approver" and record["stream"] == DECISIONS
    assert record["principal"] == harness.approver.principal_id
    events = harness.decisions()
    assert [e["event_type"] for e in events] == [
        "mcp.decision.authorized",
        "mcp.decision.applied",
    ]
    payload = events[1]["payload"]
    assert payload["tool"] == "approve_controller_plan" and payload["approved"] is True
    assert payload["binding_expected"] == payload["binding_actual"] == sent["plan_hash"]
    assert payload["principal"] == harness.approver.principal_id
    assert payload["approver_label"] == "reviewer-7" and payload["reason"] == "reviewed"
    assert APPROVER_SECRET not in json.dumps(events) and HOST_SECRET not in json.dumps(events)
    assert events[1]["actor"]["identifier"] == harness.approver.principal_id


def test_a_decision_bound_to_a_superseded_plan_is_stale_and_not_applied(harness):
    controller_id = harness.controller("snap-stale")
    first = harness.plan_hash(controller_id)
    rejected = harness.as_approver(
        "approve_controller_plan",
        harness.decided(controller_id, approved=False, reason="rework"),
    )
    assert rejected["ok"] and rejected["controller"]["phase"] == "planning"
    changed = plan_payload(plan_id="plan-second")
    assert harness.as_host(
        "submit_controller_plan", {"controller_id": controller_id, "plan": changed}
    )["ok"]
    second = harness.plan_hash(controller_id)
    assert second != first
    stale = error_of(
        harness.as_approver(
            "approve_controller_plan",
            {
                "controller_id": controller_id,
                "approved": True,
                "plan_hash": first,
                "issued_at_utc": stamp(),
            },
        )
    )
    assert stale["type"] == "ApprovalStale"
    assert f"plan_hash {first[:12]}" in stale["message"] and second[:12] in stale["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"
    fresh = harness.as_approver(
        "approve_controller_plan",
        {
            "controller_id": controller_id,
            "approved": True,
            "plan_hash": second,
            "issued_at_utc": stamp(),
        },
    )
    assert fresh["ok"] and fresh["controller"]["phase"] == "dispatch-ready"


def test_a_decision_issued_too_long_ago_or_in_the_future_is_expired(harness):
    controller_id = harness.controller("snap-expired")
    old = error_of(
        harness.as_approver(
            "approve_controller_plan",
            harness.decided(controller_id, issued_at_utc=stamp(3600)),
        )
    )
    assert old["type"] == "ApprovalExpired" and "at most 300s old" in old["message"]
    future = error_of(
        harness.as_approver(
            "approve_controller_plan",
            harness.decided(controller_id, issued_at_utc=stamp(-3600)),
        )
    )
    assert future["type"] == "ApprovalExpired" and "in the future" in future["message"]
    naive = error_of(
        harness.as_approver(
            "approve_controller_plan",
            harness.decided(controller_id, issued_at_utc="2026-01-01T00:00:00"),
        )
    )
    assert naive["type"] == "ApprovalExpired" and "without a UTC offset" in naive["message"]
    garbled = error_of(
        harness.as_approver(
            "approve_controller_plan",
            harness.decided(controller_id, issued_at_utc="yesterday"),
        )
    )
    assert garbled["type"] == "ApprovalExpired" and "not an ISO-8601" in garbled["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"


def test_the_decision_age_limit_is_the_policys(tmp_path):
    harness = Harness(tmp_path, approval_max_age_seconds=5.0)
    controller_id = harness.controller("snap-age")
    error = error_of(
        harness.as_approver(
            "approve_controller_plan",
            harness.decided(controller_id, issued_at_utc=stamp(30)),
        )
    )
    assert error["type"] == "ApprovalExpired" and "at most 5s old" in error["message"]


def test_a_replayed_decision_is_rejected_by_state_and_audited_as_failed(harness):
    controller_id = harness.controller("snap-replay")
    sent = harness.decided(controller_id)
    assert harness.as_approver("approve_controller_plan", sent)["ok"]
    replay = error_of(harness.as_approver("approve_controller_plan", sent))
    assert "dispatch-ready" in replay["message"] and "awaiting-plan-approval" in replay["message"]
    kinds = [e["event_type"] for e in harness.decisions()]
    assert kinds == [
        "mcp.decision.authorized",
        "mcp.decision.applied",
        "mcp.decision.authorized",
        "mcp.decision.failed",
    ]
    failed = harness.decisions()[-1]["payload"]
    assert failed["error_type"] == "ValueError"


def test_a_label_the_caller_supplies_is_recorded_and_grants_nothing(harness):
    controller_id = harness.controller("snap-label")
    forged = harness.as_host(
        "approve_controller_plan",
        {
            "controller_id": controller_id,
            "approved": True,
            "approver_label": "approver",
            "reason": "I am the approver",
        },
    )
    assert error_of(forged)["type"] == "OperationNotPermitted"
    assert harness.phase(controller_id) == "awaiting-plan-approval"
    refused = [e for e in harness.decisions() if e["event_type"] == "mcp.authority.refused"]
    assert refused[0]["payload"]["principal_authority"] == "host"
    assert refused[0]["payload"]["principal"] == harness.host.principal_id


def test_a_local_caller_keeps_working_without_bindings_but_a_stale_one_is_still_refused(tmp_path):
    local = create_mcp_server(tmp_path / "local")

    def invoke(name, arguments):
        return asyncio.run(local.call_tool(name, arguments)).structured_content

    created = invoke(
        "create_controller",
        {
            "snapshot": {"snapshot_id": "snap-local", **SNAPSHOT},
            "profile": {"stage": "design", "source_snapshot_id": "snap-local"},
            "routing_rules": RULES,
            "gap_metadata": {},
        },
    )
    controller_id = created["controller"]["controller_id"]
    invoke("submit_controller_plan", {"controller_id": controller_id, "plan": plan_payload()})
    stale = invoke(
        "approve_controller_plan",
        {"controller_id": controller_id, "approved": True, "plan_hash": "0" * 64},
    )
    assert error_of(stale)["type"] == "ApprovalStale"
    decided = invoke("approve_controller_plan", {"controller_id": controller_id, "approved": True})
    assert decided["ok"] and decided["decision_record"]["authority"] == "local"


def test_a_service_that_loses_its_principal_refuses_instead_of_assuming_local(harness):
    controller_id = harness.controller("snap-no-principal")
    error = error_of(
        harness.call("approve_controller_plan", {"controller_id": controller_id, "approved": True})
    )
    assert error["type"] == "Unauthenticated"
    assert "without an authenticated principal" in error["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"
