import threading
import time

import pytest

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskOptionError, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    checkpoint_path,
    read_checkpoints,
)
from nailong_agent_sdk.tools.artifact_anchor import (
    ArtifactAnchorVerdict,
    artifact_anchor_name,
    verify_artifact_anchor,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from nailong_agent_sdk.tools.core import core_tool_definitions
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat

SIGNER = HmacSha256Signer(b"periodic-key-0123456789abcdef-01!", signer_id="periodic")
ENDPOINT = {
    "base_url": "http://127.0.0.1:9/v1",
    "api_key": "sk-live-0123456789abcdef",
    "allow_insecure_http": True,
}
BUILTIN = {item.name: item for item in core_tool_definitions()}
SLEEPER = definition(tools=[BUILTIN["sleep"], BUILTIN["write_draft"]], max_iterations=6)


def options(**changes):
    return TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {
                "capabilities": ["utility.wait", "draft.write"],
                "approved_capabilities": ["draft.write"],
            },
            "declared_output_paths": ["out.txt"],
            **changes,
        }
    )


def services_in(tmp_path):
    return AgentRuntimeServices.open(
        tmp_path / "run",
        checkpointer=RunHeadCheckpointer(SIGNER, FileCheckpointSink(tmp_path / "elsewhere")),
    )


def script():
    return [
        tool_chat("c1", "write_draft", {"path": "out.txt", "content": "early"}),
        tool_chat("c2", "sleep", {"seconds": 1.5}),
        final_chat(),
    ]


def test_an_interval_without_a_checkpointer_is_refused_before_the_run_starts(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run", checkpointer=None)
    subject = AgentTaskRunner(services, transport=ScriptedChatTransport([final_chat()]))
    with pytest.raises(TaskOptionError, match="checkpoint_interval_seconds.*RunHeadCheckpointer"):
        arun(subject.run(SLEEPER, task(), options(checkpoint_interval_seconds=0.5)))


def test_a_long_run_leaves_signed_checkpoints_and_artifact_anchors_before_it_ends(tmp_path):
    services = services_in(tmp_path)
    mid_run = []
    subject = AgentTaskRunner(services, transport=ScriptedChatTransport(script()))

    async def scenario():
        import asyncio

        running = asyncio.ensure_future(
            subject.run(SLEEPER, task(), options(checkpoint_interval_seconds=0.3))
        )
        await asyncio.sleep(1.1)
        mid_run.extend(read_checkpoints(checkpoint_path(tmp_path / "elsewhere", "t1")))
        return await running

    result = arun(scenario())
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert len(mid_run) >= 2, "no checkpoint was taken while the run was still going"
    sequences = [item.telemetry_sequence for item in mid_run]
    assert sequences == sorted(sequences)
    final = read_checkpoints(checkpoint_path(tmp_path / "elsewhere", "t1"))
    assert len(final) > len(mid_run)
    report = services.checkpointer.check(
        services.run_root, services.telemetry, services.audit_logs, "t1"
    )
    assert report.verification.verdict is CheckpointVerdict.MATCHES
    store = ArtifactStore(services.run_root / "workspaces" / "t1", read_only=True)
    anchors = verify_artifact_anchor(
        store, FileCheckpointSink(tmp_path / "elsewhere"), artifact_anchor_name("t1"), SIGNER
    )
    assert anchors.verdict is ArtifactAnchorVerdict.MATCHES and anchors.anchors >= 2
    services.audit_logs.close()
    services.telemetry.close()


def test_a_checkpoint_taken_while_the_audit_log_is_being_appended_is_never_torn(tmp_path):
    services = services_in(tmp_path)
    try:
        services.audit_logs.append("busy", "note", {"n": 0}, task_id="busy", iteration=0)
        stop = threading.Event()
        failures = []

        def writer():
            index = 1
            while not stop.is_set():
                services.audit_logs.append(
                    "busy", "note", {"n": index, "pad": "x" * 200}, task_id="busy", iteration=index
                )
                index += 1

        thread = threading.Thread(target=writer)
        thread.start()
        deadline = time.monotonic() + 3
        taken = 0
        while time.monotonic() < deadline:
            try:
                services.checkpoint_run("busy")
                taken += 1
            except Exception as error:
                failures.append(f"{type(error).__name__}: {error}")
        stop.set()
        thread.join()
        assert taken > 5 and failures == [], failures[:3]
    finally:
        services.audit_logs.close()
        services.telemetry.close()
