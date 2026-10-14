from nailong_agent_sdk.mcp.authority import McpOperation
from nailong_agent_sdk.mcp.server import create_mcp_server
from tests.support.mcp_authority import Harness, error_of, plan_payload

AUTHORITY_ERRORS = {"OperationNotPermitted", "ApprovalsDisabled", "Unauthenticated"}


def test_the_host_credential_cannot_decide_an_approval_and_nothing_changes(harness):
    controller_id = harness.controller("snap-host-refused")
    refused = harness.as_host(
        "approve_controller_plan", {"controller_id": controller_id, "approved": True}
    )
    error = error_of(refused)
    assert error["type"] == "OperationNotPermitted"
    assert 'Tool "approve_controller_plan" records an approval decision' in error["message"]
    assert "AGENT_RUNTIME_APPROVER_TOKEN" in error["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"


def test_every_approval_tool_refuses_the_host_credential(harness):
    arguments = {
        "submit_approval": {"run_id": "r", "approval_id": "a", "approved": True},
        "decide_task_approval": {"task_id": "t", "approval_id": "a", "approved": True},
        "approve_controller_plan": {"controller_id": "c", "approved": True},
        "approve_orchestration": {"orchestration_id": "o", "approved": True},
        "record_human_project_decision": {
            "project_id": "p",
            "decision_id": "d",
            "content": "c",
            "status": "locked",
            "evidence_id": "e",
        },
        "resolve_project_question": {
            "project_id": "p",
            "question_id": "q",
            "resolution": "r",
            "evidence_id": "e",
        },
        "clear_project_blocker": {
            "project_id": "p",
            "blocker_id": "b",
            "reason": "r",
            "evidence_id": "e",
        },
        "grant_elastic_capacity": {"controller_id": "c", "reason": "r", "max_elastic_depth": 1},
        "decline_elastic_requests": {"controller_id": "c", "parent_node_id": "n", "reason": "r"},
    }
    for name, sent in arguments.items():
        error = error_of(harness.as_host(name, sent))
        assert error["type"] == "OperationNotPermitted", (name, error)
        assert f'Tool "{name}"' in error["message"]


def test_the_approver_credential_cannot_start_or_steer_work(harness):
    controller_id = harness.controller("snap-approver-limits")
    attempts = {
        "start_run": {"plan": plan_payload()},
        "cancel_run": {"run_id": "r"},
        "dispatch_controller": {"controller_id": controller_id},
        "submit_controller_plan": {"controller_id": controller_id, "plan": plan_payload()},
        "cancel_controller": {"controller_id": controller_id, "reason": "x"},
        "run_agent_task": {"definition": {}, "task": {}},
        "prune_runs": {"older_than_seconds": 1, "dry_run": False},
        "record_metric_observation": {"observation": {}},
        "open_project_question": {
            "project_id": "p",
            "question_id": "q",
            "content": "c",
            "owner": "human",
            "evidence_id": "e",
        },
    }
    for name, sent in attempts.items():
        error = error_of(harness.as_approver(name, sent))
        assert error["type"] == "OperationNotPermitted", (name, error)
        assert "needs the host credential" in error["message"]
        assert "AGENT_RUNTIME_AUTH_TOKEN" in error["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"


def test_the_approver_credential_can_read_what_it_is_asked_to_decide(harness):
    controller_id = harness.controller("snap-approver-reads")
    reads = {
        "get_controller_state": {"controller_id": controller_id},
        "get_shared_state": {"controller_id": controller_id},
        "get_run_state": {"run_id": "run-404"},
        "list_run_approvals": {"run_id": "run-404"},
        "list_task_approvals": {"task_id": "task-404"},
        "list_task_files": {"task_id": "task-404"},
        "read_task_file": {"task_id": "task-404", "path": "a.txt"},
        "get_project_state": {"project_id": "project-404"},
        "get_orchestration": {"orchestration_id": "orchestration-404"},
        "list_telemetry_runs": {},
        "get_telemetry_events": {"run_id": "run-404"},
        "get_audit_log": {"run_id": "run-404"},
        "get_telemetry_metrics": {"run_id": "run-404"},
        "list_metric_definitions": {},
    }
    for name, sent in reads.items():
        errors = harness.as_approver(name, sent).get("errors") or []
        assert not {e["type"] for e in errors} & AUTHORITY_ERRORS, (name, errors)
    assert harness.as_approver("get_controller_state", {"controller_id": controller_id})["ok"]


def test_a_tool_that_is_not_classified_is_closed_to_the_approver(tmp_path):
    from nailong_agent_sdk.mcp import _shared

    classified = {
        name
        for name, operation in _classification(tmp_path).items()
        if operation is not McpOperation.HOST
    }
    assert classified == {
        "get_run_state",
        "list_run_approvals",
        "submit_approval",
        "list_task_approvals",
        "decide_task_approval",
        "list_task_files",
        "read_task_file",
        "get_project_state",
        "record_human_project_decision",
        "resolve_project_question",
        "clear_project_blocker",
        "get_orchestration",
        "approve_orchestration",
        "get_controller_state",
        "get_shared_state",
        "approve_controller_plan",
        "grant_elastic_capacity",
        "decline_elastic_requests",
        "list_telemetry_runs",
        "get_telemetry_events",
        "get_audit_log",
        "get_telemetry_metrics",
        "list_metric_definitions",
    }
    assert _shared.McpContext.tool.__kwdefaults__["operation"] is McpOperation.HOST


def _classification(tmp_path):
    from nailong_agent_sdk.mcp import _shared

    recorded = {}
    original = _shared.McpContext._offloaded

    def spy(self, function, name, exclusive, operation):
        recorded[name] = operation
        return original(self, function, name, exclusive, operation)

    _shared.McpContext._offloaded = spy
    try:
        create_mcp_server(tmp_path / "classified")
    finally:
        _shared.McpContext._offloaded = original
    return recorded


def test_approval_operations_are_disabled_without_an_approver_credential(tmp_path):
    harness = Harness(tmp_path, approver_configured=False)
    controller_id = harness.controller("snap-disabled")
    error = error_of(
        harness.as_host(
            "approve_controller_plan", {"controller_id": controller_id, "approved": True}
        )
    )
    assert error["type"] == "ApprovalsDisabled"
    assert "AGENT_RUNTIME_APPROVER_TOKEN" in error["message"]
    assert "AGENT_RUNTIME_ALLOW_HOST_APPROVALS=1" in error["message"]
    assert harness.phase(controller_id) == "awaiting-plan-approval"


def test_the_single_credential_opt_in_lets_the_host_decide_and_the_audit_says_so(tmp_path):
    harness = Harness(tmp_path, approver_configured=False, allow_host_approvals=True)
    controller_id = harness.controller("snap-single")
    decided = harness.as_host(
        "approve_controller_plan", {"controller_id": controller_id, "approved": True}
    )
    assert decided["ok"] and decided["controller"]["phase"] == "dispatch-ready"
    applied = [e for e in harness.decisions() if e["event_type"] == "mcp.decision.applied"]
    assert applied[0]["payload"]["host_may_approve"] is True
    assert applied[0]["payload"]["principal_authority"] == "host"
