from concurrent.futures import ThreadPoolExecutor

from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from tests.mcp.test_server_task_lifecycle import WRITE_TOOL, write_options
from tests.support.agents import definition, task
from tests.support.chat_provider import chat_provider, final_chat, tool_chat
from tests.support.mcp_authority import Harness, error_of, plan_payload, stamp
from tests.support.mcp_server import Server


def run_with_approval(harness):
    started = harness.as_host("start_run", {"plan": plan_payload()})
    run_id = started["run"]["run_id"]
    registry = HarnessCoordinator(harness.run_root).approvals(run_id)
    requested = registry.request(run_id, "node:T1", "draft.write", "writes the draft")
    listed = harness.as_approver("list_run_approvals", {"run_id": run_id})
    item = next(a for a in listed["approvals"] if a["approval_id"] == requested.approval_id)
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
    return run_id, requested.approval_id, item["request_hash"]


def revocation(run_id, approval_id, bound_hash, **changes):
    return {
        "run_id": run_id,
        "approval_id": approval_id,
        "reason": "the scope changed",
        "request_hash": bound_hash,
        "issued_at_utc": stamp(),
        **changes,
    }


def test_the_host_credential_cannot_revoke_an_approval_and_nothing_changes(tmp_path):
    harness = Harness(tmp_path)
    run_id, approval_id, request_hash = run_with_approval(harness)
    refused = error_of(
        harness.as_host("revoke_run_approval", revocation(run_id, approval_id, request_hash))
    )
    assert refused["type"] == "OperationNotPermitted"
    assert 'Tool "revoke_run_approval" records an approval decision' in refused["message"]
    stored = harness.as_approver("list_run_approvals", {"run_id": run_id})["approvals"]
    assert [a["status"] for a in stored] == ["approved"]


def test_an_approver_revokes_a_bound_approval_and_the_decision_stream_records_it(tmp_path):
    harness = Harness(tmp_path)
    run_id, approval_id, request_hash = run_with_approval(harness)
    revoked = harness.as_approver(
        "revoke_run_approval", revocation(run_id, approval_id, request_hash)
    )
    assert revoked["ok"], revoked
    assert revoked["approval"]["status"] == "revoked"
    assert revoked["approval"]["decided_by"] == harness.approver.principal_id
    assert revoked["decision_record"]["authority"] == "approver"
    names = {e["payload"]["tool"]: e["event_type"] for e in harness.decisions()}
    assert names["revoke_run_approval"] == "mcp.decision.applied"
    run_events = harness.as_approver("get_telemetry_events", {"run_id": run_id, "limit": 100})
    types = [e["event_type"] for e in run_events["events"]]
    assert "approval.revoked" in types and "approval.granted" in types
    again = error_of(
        harness.as_approver("revoke_run_approval", revocation(run_id, approval_id, request_hash))
    )
    assert 'has the decision "revoked" and cannot be revoked' in again["message"]


def test_a_revocation_without_the_hash_or_with_a_stale_one_is_refused(tmp_path):
    harness = Harness(tmp_path)
    run_id, approval_id, request_hash = run_with_approval(harness)
    unbound = harness.as_approver(
        "revoke_run_approval", revocation(run_id, approval_id, "unused", request_hash=None)
    )
    assert error_of(unbound)["type"] == "ApprovalBindingRequired"
    stale = harness.as_approver("revoke_run_approval", revocation(run_id, approval_id, "f" * 64))
    assert error_of(stale)["type"] == "ApprovalStale"
    stored = harness.as_approver("list_run_approvals", {"run_id": run_id})["approvals"]
    assert [a["status"] for a in stored] == ["approved"]


def test_a_waiting_task_stops_when_an_operator_revokes_its_pending_approval_over_the_wire(tmp_path):
    instance = Server(tmp_path, extra_environment={"AGENT_RUNTIME_ALLOW_HOST_APPROVALS": "1"})
    try:
        script = [tool_chat("c1", "write_draft", {"path": "out.txt", "content": "x"}), final_chat()]
        with chat_provider(script) as (base_url, _):
            arguments = {
                "definition": definition(tools=[WRITE_TOOL], max_iterations=6).model_dump(
                    mode="json"
                ),
                "task": task("mcp-revoke-1").model_dump(mode="json"),
                "runtime_options": write_options(base_url, approval_mode="wait"),
            }
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(instance.call, "run_agent_task", arguments)
                pending = []
                for _ in range(200):
                    listed = instance.call("list_task_approvals", {"task_id": "mcp-revoke-1"})
                    pending = [a for a in listed.get("approvals", []) if a["status"] == "pending"]
                    if pending:
                        break
                    import time

                    time.sleep(0.1)
                assert pending
                revoked = instance.call(
                    "revoke_task_approval",
                    {
                        "task_id": "mcp-revoke-1",
                        "approval_id": pending[0]["approval_id"],
                        "reason": "operator stopped it",
                    },
                )
                assert revoked["ok"] and revoked["approval"]["status"] == "revoked"
                run = running.result(60)
        assert run["ok"] and run["result"]["status"] == "blocked", run
        events = instance.call("get_telemetry_events", {"run_id": "mcp-revoke-1", "limit": 200})
        assert "approval.revoked" in [e["event_type"] for e in events["events"]]
        missing = instance.call(
            "revoke_task_approval",
            {"task_id": "mcp-revoke-1", "approval_id": "approval-1", "reason": "late"},
        )
        assert missing["errors"][0]["message"] == 'No agent task "mcp-revoke-1" is running.'
    finally:
        instance.stop()
