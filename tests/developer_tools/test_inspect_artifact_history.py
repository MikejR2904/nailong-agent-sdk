import json

import pytest

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.developer_tools.inspect import inspect_run
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from tests.support.agents import arun, definition, final, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import record
from tests.support.mcp_cases import BUILTIN

OPTIONS = TaskRunOptions.model_validate(
    {
        "model_endpoint": {
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-live-0123456789abcdef",
            "allow_insecure_http": True,
        },
        "permissions": {"capabilities": ["draft.write"], "approved_capabilities": ["draft.write"]},
        "declared_output_paths": ["out.txt"],
    }
)
RUN = "inspected-run"
QUIET = "quiet-run"


def write_log(root):
    return (
        root / "workspaces" / file_safe_name(RUN) / ".agent-artifacts" / "history" / "writes.jsonl"
    )


@pytest.fixture
def run_root(tmp_path):
    root = tmp_path / "run"
    services = AgentRuntimeServices.open(root)
    try:
        writer = ScriptedChatTransport(
            [
                tool_chat("w1", "write_draft", {"path": "out.txt", "content": "v1\n"}),
                tool_chat("w2", "write_draft", {"path": "out.txt", "content": "v2\n"}),
                final_chat(),
            ]
        )
        for transport, run_id, tools in (
            (writer, RUN, [BUILTIN["write_draft"]]),
            (ScriptedChatTransport([final_chat()]), QUIET, []),
        ):
            agent = definition(tools=tools, max_iterations=4)
            result = arun(
                AgentTaskRunner(services, transport=transport).run(agent, task(run_id), OPTIONS)
            )
            assert result.status is AgentRunStatus.COMPLETED, result.reason
    finally:
        services.audit_logs.close()
        services.telemetry.close()
    return root


def test_an_intact_artifact_history_is_reported_valid(run_root):
    inspected = inspect_run(run_root, RUN)
    assert inspected.artifact_history_valid is True and inspected.artifact_history_failure is None
    assert inspected.report["artifact_history_valid"] is True


def test_an_edited_artifact_history_is_reported_with_the_line_that_broke(run_root):
    log = write_log(run_root)
    lines = log.read_text("utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["manifest"]["tool_call_id"] = "forged"
    lines[0] = json.dumps(forged, separators=(",", ":"))
    log.write_text("".join(f"{line}\n" for line in lines), "utf-8")
    inspected = inspect_run(run_root, RUN)
    assert inspected.artifact_history_valid is False
    assert inspected.artifact_history_failure.startswith(
        'Line 1 of the artifact write log "writes.jsonl"'
    )
    assert inspected.telemetry_chain_valid and inspected.audit_chain_valid


def test_a_run_that_wrote_nothing_has_an_intact_empty_history(run_root):
    quiet = inspect_run(run_root, QUIET)
    assert quiet.artifact_history_valid is True and quiet.artifact_history_failure is None


def test_a_run_with_no_workspace_has_no_history_verdict(tmp_path):
    record(tmp_path, "plain-run", [final()])
    plain = inspect_run(tmp_path, "plain-run")
    assert plain.artifact_history_valid is None and plain.artifact_history_failure is None
    assert plain.report["artifact_history_valid"] is None


def test_the_command_line_exits_one_when_the_artifact_history_is_broken(run_root):
    assert cli("inspect-run", run_root, RUN).returncode == 0
    log = write_log(run_root)
    lines = log.read_text("utf-8").splitlines()
    log.write_text("".join(f"{line}\n" for line in [lines[1], lines[0], *lines[2:]]), "utf-8")
    done = cli("inspect-run", run_root, RUN)
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["artifact_history_valid"] is False
