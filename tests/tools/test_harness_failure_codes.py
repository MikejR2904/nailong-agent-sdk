import sys

from nailong_agent_sdk.tools.policy import CapabilityGrant
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from tests.support.tools import build, call, run

HELLO = CommandTemplate(
    name="hello", command=[sys.executable, "-c", "print('hi')"], timeout_seconds=30
)


def test_a_tool_the_harness_does_not_know_carries_its_name_and_code(tmp_path):
    executor, _ = build(tmp_path)
    result = run(call(executor, "does_not_exist"))
    assert result.status == "failed"
    assert result.failure.code == "HARNESS_TOOL_UNKNOWN"
    assert result.failure.message == result.error
    assert result.failure.details == {"tool": "does_not_exist"}


def test_a_role_without_the_capability_is_denied_by_policy_with_both_names(tmp_path):
    executor, _ = build(tmp_path, grants=[CapabilityGrant(role="someone-else", capabilities=[])])
    result = run(call(executor, "brief", text="hello"))
    assert result.status == "blocked"
    assert result.failure.code == "TOOL_DENIED_BY_POLICY"
    assert result.failure.message == result.error
    assert result.failure.details == {"tool": "brief", "capability": "utility.brief"}


def test_a_pending_approval_is_typed_and_names_the_request_to_decide(tmp_path):
    executor, context = build(tmp_path, templates=[HELLO])
    result = run(call(executor, "run_registered_command", template_name="hello"))
    assert result.status == "blocked"
    assert result.failure.code == "TOOL_APPROVAL_REQUIRED"
    assert result.failure.details == {
        "tool": "run_registered_command",
        "capability": "process.execute",
        "approval_id": result.output["approval_id"],
    }
    pending = [item for item in context.approvals.list() if item.status.value == "pending"]
    assert [item.approval_id for item in pending] == [result.failure.details["approval_id"]]


def test_a_rejected_approval_is_a_denial_that_carries_the_approvers_reason(tmp_path):
    executor, context = build(tmp_path, templates=[HELLO])
    first = run(call(executor, "run_registered_command", template_name="hello"))
    approval_id = first.output["approval_id"]
    context.approvals.submit(approval_id, False, "nope")
    context.approval_ids_by_capability["process.execute"] = approval_id
    result = run(call(executor, "run_registered_command", template_name="hello"))
    assert result.status == "blocked" and result.error == "nope"
    assert result.failure.code == "TOOL_DENIED_BY_POLICY"
    assert result.failure.message == "nope"
