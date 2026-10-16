import pytest

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.developer_tools.attribute_defect import attribute_defect
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat
from tests.support.mcp_cases import BUILTIN

ENDPOINT = {
    "base_url": "http://127.0.0.1:9/v1",
    "api_key": "sk-live-0123456789abcdef",
    "allow_insecure_http": True,
}
OPTIONS = TaskRunOptions.model_validate(
    {
        "model_endpoint": ENDPOINT,
        "permissions": {"capabilities": ["draft.write"], "approved_capabilities": ["draft.write"]},
        "declared_output_paths": ["out.txt"],
        "workspace": "shared",
    }
)
TOOLS = [BUILTIN["write_draft"], BUILTIN["edit_draft"]]


def has_bug(content, version):
    return b"BUG" in content


def work(services, identity, task_id, call_id, tool, arguments):
    agent = definition(tools=TOOLS, max_iterations=4).model_copy(update={"identity": identity})
    transport = ScriptedChatTransport([tool_chat(call_id, tool, arguments), final_chat()])
    result = arun(AgentTaskRunner(services, transport=transport).run(agent, task(task_id), OPTIONS))
    assert result.status is AgentRunStatus.COMPLETED, result.reason


@pytest.fixture
def shared_history(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        work(
            services,
            "drafter",
            "t-a",
            "a1",
            "write_draft",
            {"path": "out.txt", "content": "total = 3\n"},
        )
        work(
            services,
            "summariser",
            "t-b",
            "b1",
            "write_draft",
            {"path": "out.txt", "content": "total = 4\nBUG\n"},
        )
        work(
            services,
            "editor",
            "t-c",
            "c1",
            "edit_draft",
            {"path": "out.txt", "old_text": "total = 4", "new_text": "total = 5"},
        )
        yield services.run_root / "shared"
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_the_agent_whose_write_introduced_a_defect_is_named_with_its_run_and_call(shared_history):
    result = attribute_defect(shared_history, "out.txt", has_bug, check="contains BUG")
    assert not result.ok and result.versions == 3
    writer = result.introduced_by
    assert writer.sequence == 2 and writer.agent_identity == "summariser"
    assert writer.agent_run_id == "t-b" and writer.tool == "write_draft"
    assert writer.tool_call_id == "b1" and writer.iteration == 1
    assert writer.role is not None and writer.node_id == "t-b" and writer.task_id == "t-b"
    assert result.last_clean.agent_identity == "drafter"
    (later,) = result.later_writers
    assert later.agent_identity == "editor" and later.tool == "edit_draft"
    assert 'agent "summariser"' in result.summary and 'write_draft call "b1"' in result.summary


def test_the_editor_that_left_the_defect_in_place_is_not_blamed_for_introducing_it(shared_history):
    scanned = attribute_defect(shared_history, "out.txt", has_bug, strategy="scan")
    assert scanned.introduced_by.agent_identity == "summariser"
    assert scanned.evaluations == 3


def test_a_service_run_root_points_at_the_workspaces_that_hold_artifacts(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        work(services, "drafter", "t-a", "a1", "write_draft", {"path": "out.txt", "content": "x\n"})
        with pytest.raises(ValueError, match=r"holds no .agent-artifacts directory.*\['shared'\]"):
            attribute_defect(services.run_root, "out.txt", has_bug)
    finally:
        services.audit_logs.close()
        services.telemetry.close()
