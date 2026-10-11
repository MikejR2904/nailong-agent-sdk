import asyncio
import json
import time

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskOptionError, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.tools.approvals import ApprovalStatus
from nailong_agent_sdk.tools.core import core_tool_definitions
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat

KEY = "sk-live-0123456789abcdef"
ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": KEY, "allow_insecure_http": True}
BUILTIN = {item.name: item for item in core_tool_definitions()}
WRITE = definition(tools=[BUILTIN["write_draft"]], max_iterations=6)
CALL = tool_chat("c1", "write_draft", {"path": "out.txt", "content": "drafted"})


def options(**permissions):
    return TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {
                "capabilities": ["draft.write"],
                "approval_mode": "wait",
                **permissions,
            },
            "declared_output_paths": ["out.txt"],
        }
    )


def runner(tmp_path, bodies):
    services = AgentRuntimeServices.open(tmp_path / "service")
    return AgentTaskRunner(services, transport=ScriptedChatTransport(bodies)), services


async def await_approval(subject, task_id, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            pending = [r for r in subject.approvals(task_id) if r.status is ApprovalStatus.PENDING]
        except TaskOptionError:
            pending = []
        if pending:
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError(f'task "{task_id}" never asked for an approval')


def drive(subject, run_options, decide, bodies_task="t1"):
    async def scenario():
        running = asyncio.ensure_future(subject.run(WRITE, task(bodies_task), run_options))
        request = await await_approval(subject, bodies_task)
        outcome = decide(request)
        return await asyncio.wait_for(running, 30), request, outcome

    return arun(scenario())


def workspace_file(services, task_id="t1"):
    return services.run_root / "workspaces" / task_id / "out.txt"


def test_a_run_that_waits_for_approval_continues_when_the_caller_approves(tmp_path):
    subject, services = runner(tmp_path, [CALL, final_chat()])
    result, request, decided = drive(
        subject,
        options(),
        lambda request: subject.decide_approval("t1", request.approval_id, True, "reviewed"),
    )
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert workspace_file(services).read_text("utf-8") == "drafted"
    assert (request.capability, request.status) == ("draft.write", ApprovalStatus.PENDING)
    assert decided.status is ApprovalStatus.APPROVED and decided.decision_reason == "reviewed"
    events = [
        event
        for event in services.telemetry.iter_events("t1")
        if event.event_type.startswith("approval.")
    ]
    assert [(e.event_type, e.status) for e in events] == [
        ("approval.requested", "pending"),
        ("approval.decided", "approved"),
    ]
    requested, decision = (event.payload for event in events)
    assert requested["approval_id"] == request.approval_id
    assert requested["capability"] == "draft.write" and requested["tool"] == "write_draft"
    assert requested["timeout_seconds"] == 300.0
    assert decision["decision_reason"] == "reviewed"


def test_one_approval_covers_the_capability_for_the_rest_of_the_run(tmp_path):
    second = tool_chat("c2", "write_draft", {"path": "out.txt", "content": "again"})
    subject, services = runner(tmp_path, [CALL, second, final_chat()])
    result, _, _ = drive(
        subject,
        options(),
        lambda request: subject.decide_approval("t1", request.approval_id, True),
    )
    assert result.status is AgentRunStatus.COMPLETED
    approvals = [
        e for e in services.telemetry.iter_events("t1") if e.event_type == "approval.requested"
    ]
    assert len(approvals) == 1


def test_a_rejection_ends_the_run_blocked_and_names_the_approval_and_the_reason(tmp_path):
    subject, services = runner(tmp_path, [CALL, final_chat()])
    result, request, _ = drive(
        subject,
        options(),
        lambda request: subject.decide_approval("t1", request.approval_id, False, "not this file"),
    )
    assert result.status is AgentRunStatus.BLOCKED
    assert result.reason == (
        f'Approval "{request.approval_id}" for capability "draft.write" was rejected: not this file'
    )
    assert not workspace_file(services).exists()


def test_a_rejection_without_a_reason_still_says_what_was_rejected(tmp_path):
    subject, _ = runner(tmp_path, [CALL, final_chat()])
    result, request, _ = drive(
        subject,
        options(),
        lambda request: subject.decide_approval("t1", request.approval_id, False),
    )
    assert result.status is AgentRunStatus.BLOCKED
    assert (
        result.reason
        == f'Approval "{request.approval_id}" for capability "draft.write" was rejected.'
    )


def test_an_approval_nobody_decides_ends_the_run_blocked_after_the_timeout(tmp_path):
    subject, services = runner(tmp_path, [CALL, final_chat()])
    started = time.monotonic()
    result = arun(subject.run(WRITE, task(), options(approval_timeout_seconds=0.4)))
    waited = time.monotonic() - started
    assert result.status is AgentRunStatus.BLOCKED
    assert (
        result.reason
        == 'Approval "approval-1" for capability "draft.write" was not decided within 0.4s.'
    )
    assert 0.35 <= waited < 10
    assert not workspace_file(services).exists()
    decided = [
        e for e in services.telemetry.iter_events("t1") if e.event_type == "approval.decided"
    ]
    assert [e.status for e in decided] == ["timed-out"]


def test_the_default_still_blocks_at_once_without_waiting(tmp_path):
    subject, _ = runner(tmp_path, [CALL, final_chat()])
    blocking = TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"]},
            "declared_output_paths": ["out.txt"],
        }
    )
    started = time.monotonic()
    result = arun(subject.run(WRITE, task(), blocking))
    assert result.status is AgentRunStatus.BLOCKED
    assert result.reason == 'Capability "draft.write" requires a typed approval decision.'
    assert time.monotonic() - started < 5


def test_cancelling_a_run_that_is_waiting_for_approval_stops_it_promptly(tmp_path):
    subject, services = runner(tmp_path, [CALL, final_chat()])

    def cancel(request):
        assert subject.cancel("t1") is True

    started = time.monotonic()
    result, _, _ = drive(subject, options(approval_timeout_seconds=600), cancel)
    assert result.status is AgentRunStatus.CANCELLED
    assert result.reason == 'Tool "write_draft" was interrupted because the run was cancelled.'
    assert time.monotonic() - started < 10
    assert not workspace_file(services).exists()


def test_approvals_are_kept_per_task_and_a_decision_names_the_task_it_belongs_to(tmp_path):
    subject, _ = runner(tmp_path, [CALL, final_chat()])

    def probe(request):
        with pytest.raises(TaskOptionError) as other_task:
            subject.decide_approval("someone-else", request.approval_id, True)
        assert str(other_task.value) == 'No agent task "someone-else" is running.'
        with pytest.raises(TaskOptionError) as unknown:
            subject.decide_approval("t1", "approval-99", True)
        assert str(unknown.value) == 'Agent task "t1" has no approval request "approval-99".'
        subject.decide_approval("t1", request.approval_id, True)
        with pytest.raises(ValueError) as again:
            subject.decide_approval("t1", request.approval_id, False)
        assert 'already has the decision "approved"' in str(again.value)
        assert [a.status for a in subject.approvals("t1")] == [ApprovalStatus.APPROVED]

    result, _, _ = drive(subject, options(), probe)
    assert result.status is AgentRunStatus.COMPLETED
    with pytest.raises(TaskOptionError, match='No agent task "t1" is running.'):
        subject.approvals("t1")


def test_the_waiting_options_are_validated():
    for bad in (0, -1, 86_401):
        with pytest.raises(ValidationError, match="approval_timeout_seconds"):
            options(approval_timeout_seconds=bad)
    with pytest.raises(ValidationError, match="approval_mode"):
        options(approval_mode="ask")
    parsed = json.loads(options().permissions.model_dump_json())
    assert parsed["approval_mode"] == "wait" and parsed["approval_timeout_seconds"] == 300.0


def test_a_watchdog_shorter_than_the_approval_wait_is_refused_up_front():
    for field in ("tool_call_timeout_seconds", "run_deadline_seconds"):
        with pytest.raises(ValidationError) as raised:
            TaskRunOptions.model_validate(
                {
                    "model_endpoint": ENDPOINT,
                    "permissions": {
                        "capabilities": ["draft.write"],
                        "approval_mode": "wait",
                        "approval_timeout_seconds": 300,
                    },
                    field: 60,
                }
            )
        message = str(raised.value)
        assert f"{field} (60) is shorter than permissions.approval_timeout_seconds (300)" in message
    TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"], "approval_mode": "wait"},
            "tool_call_timeout_seconds": 600,
        }
    )
    TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"]},
            "tool_call_timeout_seconds": 5,
        }
    )
