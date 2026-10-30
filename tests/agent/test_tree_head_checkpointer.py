import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink
from nailong_agent_sdk.observability.transparency import (
    TreeHeadVerdict,
    verify_run_tree_heads,
)
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat

ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-live-0123456789abcdef"}
OPTIONS = TaskRunOptions.model_validate(
    {"model_endpoint": {**ENDPOINT, "allow_insecure_http": True}}
)


def run_task(services):
    subject = AgentTaskRunner(services, transport=ScriptedChatTransport([final_chat()]))
    result = arun(subject.run(definition(), task("t1"), OPTIONS))
    assert result.status is AgentRunStatus.COMPLETED, result.reason


def test_a_checkpointer_with_tree_heads_publishes_ed25519_heads_a_verifier_can_check(tmp_path):
    signer = Ed25519Signer.generate("run-signer")
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    services = AgentRuntimeServices.open(
        tmp_path / "run", checkpointer=RunHeadCheckpointer(signer, sink, tree_heads=True)
    )
    try:
        run_task(services)
        results = verify_run_tree_heads(
            services.telemetry, services.audit_logs, "t1", sink, signer.verifier()
        )
        assert {item.verdict for item in results.values()} == {TreeHeadVerdict.MATCHES}
        assert all(item.published_size > 5 for item in results.values())
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_tree_heads_are_off_unless_asked_for(tmp_path):
    signer = Ed25519Signer.generate()
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    services = AgentRuntimeServices.open(
        tmp_path / "run", checkpointer=RunHeadCheckpointer(signer, sink)
    )
    try:
        run_task(services)
        results = verify_run_tree_heads(
            services.telemetry, services.audit_logs, "t1", sink, signer.verifier()
        )
        assert {item.verdict for item in results.values()} == {TreeHeadVerdict.NO_HEAD}
    finally:
        services.audit_logs.close()
        services.telemetry.close()
