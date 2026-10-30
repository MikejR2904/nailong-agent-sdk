from dataclasses import replace

import pytest

from nailong_agent_sdk.agent.task_runner import TaskOptionError, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    EpisodeKind,
    ToolDefinition,
)
from nailong_agent_sdk.tools.registry import HarnessToolExecutor, HarnessToolRegistry
from tests.agent.test_task_approvals import ENDPOINT, WRITE, runner
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import final_chat, tool_chat
from tests.support.tools import build, call, run

PRIVILEGED_WORDS = ("approv", "grant", "revoke", "authoriz", "permission")


def test_no_tool_an_agent_can_call_grants_or_revokes_an_approval():
    names = HarnessToolRegistry().names()
    assert not [name for name in names if any(word in name for word in PRIVILEGED_WORDS)]


@pytest.mark.parametrize(
    "name",
    ["submit_approval", "decide_task_approval", "revoke_task_approval", "revoke_run_approval"],
)
def test_an_agent_that_declares_an_approval_tool_is_refused_because_nothing_implements_it(
    tmp_path, name
):
    subject, _ = runner(tmp_path, [final_chat()])
    declared = ToolDefinition(
        name=name,
        description="d",
        input_schema={"type": "object"},
        episode_kind=EpisodeKind.ACTION,
    )
    options = TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"]},
            "declared_output_paths": ["out.txt"],
        }
    )
    with pytest.raises(TaskOptionError, match=f"Tools \\['{name}'\\] have no implementation"):
        arun(subject.run(definition(tools=[declared]), task(), options))


def test_an_agent_cannot_write_the_approvals_file_to_approve_itself(tmp_path):
    attack = tool_chat(
        "c1",
        "write_draft",
        {
            "path": "../../.agent-runs/t1.approvals.json",
            "content": '{"next_id": 9, "requests": []}',
        },
    )
    subject, services = runner(tmp_path, [attack, final_chat()])
    options = TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"], "approved_capabilities": []},
            "declared_output_paths": ["out.txt"],
        }
    )
    result = arun(subject.run(WRITE, task(), options))
    assert result.status is not AgentRunStatus.COMPLETED or result.iterations >= 1
    assert not (services.run_root / ".agent-runs" / "t1.approvals.json").exists()
    assert not list(services.run_root.rglob("*.approvals.json"))


def test_a_child_node_does_not_inherit_the_approval_of_its_parent(tmp_path):
    executor, context = build(tmp_path, declared=("out.txt",))
    assert run(call(executor, "write_draft", path="out.txt", content="1")).status == "succeeded"
    child_context = replace(context, node_id="node-1.child", approval_ids_by_capability={})
    child = HarnessToolExecutor(executor._registry, child_context)
    blocked = run(call(child, "write_draft", path="out.txt", content="2"))
    assert blocked.status == "blocked"
    assert blocked.failure.code == "TOOL_APPROVAL_REQUIRED"
    assert len(context.artifacts.history("out.txt")) == 1


def test_a_preapproval_is_the_hosts_statement_and_names_its_source(tmp_path):
    subject, services = runner(tmp_path, [final_chat()])
    options = TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {
                "capabilities": ["draft.write"],
                "approved_capabilities": ["draft.write"],
            },
            "declared_output_paths": ["out.txt"],
        }
    )
    arun(subject.run(WRITE, task(), options))
    (event,) = [
        e for e in services.telemetry.iter_events("t1") if e.event_type == "approval.decided"
    ]
    assert event.payload["decided_by"] == "run-options"
