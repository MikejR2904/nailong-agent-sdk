import asyncio

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.task_runner import TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.tools.approvals import ApprovalStatus
from tests.agent.test_task_approvals import WRITE, await_approval, runner, workspace_file
from tests.support.agents import arun, task
from tests.support.chat_provider import final_chat, tool_chat

FIRST = tool_chat("c1", "write_draft", {"path": "out.txt", "content": "drafted"})
SECOND = tool_chat("c2", "write_draft", {"path": "out.txt", "content": "other"})
ENDPOINT = {
    "base_url": "http://127.0.0.1:9/v1",
    "api_key": "sk-live-0123456789abcdef",
    "allow_insecure_http": True,
}
HUMAN = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="reviewer")


def options(mode="wait", **permissions):
    return TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"], "approval_mode": mode, **permissions},
            "declared_output_paths": ["out.txt"],
        }
    )


def events_of(services, prefix="approval."):
    return [e for e in services.telemetry.iter_events("t1") if e.event_type.startswith(prefix)]


def test_the_permissions_accept_the_narrow_approval_modes_and_refuse_an_unlisted_capability():
    parsed = options(
        single_use_capabilities=["draft.write"], bind_approvals_to_arguments=True
    ).permissions
    assert parsed.single_use_capabilities == ["draft.write"] and parsed.bind_approvals_to_arguments
    with pytest.raises(ValidationError, match="single_use_capabilities.*process.execute"):
        options(single_use_capabilities=["process.execute"])


def test_a_preapproved_single_use_capability_runs_one_call_and_blocks_the_next(tmp_path):
    subject, services = runner(tmp_path, [FIRST, SECOND, final_chat()])
    result = arun(
        subject.run(
            WRITE,
            task(),
            options(
                "block",
                approved_capabilities=["draft.write"],
                single_use_capabilities=["draft.write"],
            ),
        )
    )
    assert result.status is AgentRunStatus.BLOCKED
    assert workspace_file(services).read_text("utf-8") == "drafted"
    consumed = events_of(services, "approval.consumed")
    assert [e.payload["tool_call_id"] for e in consumed] == ["c1"]
    assert consumed[0].payload["single_use"] is True


def test_an_argument_bound_approval_does_not_cover_a_call_with_other_content(tmp_path):
    subject, services = runner(tmp_path, [FIRST, SECOND, final_chat()])

    async def scenario():
        running = asyncio.ensure_future(
            subject.run(WRITE, task(), options(bind_approvals_to_arguments=True))
        )
        first = await await_approval(subject, "t1")
        subject.decide_approval("t1", first.approval_id, True, "that text", source=HUMAN)
        second = None
        for _ in range(200):
            pending = [r for r in subject.approvals("t1") if r.status is ApprovalStatus.PENDING]
            if pending:
                second = pending[0]
                break
            await asyncio.sleep(0.02)
        assert second is not None and second.approval_id != first.approval_id
        subject.decide_approval("t1", second.approval_id, False, "not that text", source=HUMAN)
        return await asyncio.wait_for(running, 30)

    result = arun(scenario())
    assert result.status is AgentRunStatus.BLOCKED
    assert workspace_file(services).read_text("utf-8") == "drafted"


def test_revoking_a_pending_approval_ends_the_wait_and_says_it_was_revoked(tmp_path):
    subject, services = runner(tmp_path, [FIRST, final_chat()])

    async def scenario():
        running = asyncio.ensure_future(subject.run(WRITE, task(), options()))
        pending = await await_approval(subject, "t1")
        subject.revoke_approval("t1", pending.approval_id, "no longer wanted", source=HUMAN)
        return await asyncio.wait_for(running, 30), pending

    result, pending = arun(scenario())
    assert result.status is AgentRunStatus.BLOCKED
    assert result.reason == (
        f'Approval "{pending.approval_id}" for capability "draft.write" was revoked: '
        "no longer wanted"
    )
    assert not workspace_file(services).exists()
    (revoked,) = events_of(services, "approval.revoked")
    assert revoked.authority is TelemetryAuthority.HUMAN
    assert revoked.payload["decided_by"] == "reviewer"


def test_an_unrevoked_run_has_no_revocation_and_a_timeout_is_recorded_as_expiry(tmp_path):
    subject, services = runner(tmp_path, [FIRST, final_chat()])
    result = arun(subject.run(WRITE, task(), options(approval_timeout_seconds=0.3)))
    assert result.status is AgentRunStatus.BLOCKED
    assert events_of(services, "approval.revoked") == []
    (expired,) = events_of(services, "approval.expired")
    assert expired.payload["reason"].startswith("Expired: not decided within")


def test_a_policy_denial_in_a_run_is_recorded_with_its_tool_and_reason(tmp_path):
    subject, services = runner(tmp_path, [FIRST, final_chat()])
    result = arun(
        subject.run(
            WRITE,
            task(),
            options("block", allowed_paths=["sub"], approved_capabilities=["draft.write"]),
        )
    )
    assert result.status is AgentRunStatus.BLOCKED
    (denied,) = events_of(services, "approval.denied")
    assert denied.payload["tool"] == "write_draft" and denied.payload["tool_call_id"] == "c1"
    assert "outside the declared capability scope" in denied.payload["reason"]
    assert not workspace_file(services).exists()
