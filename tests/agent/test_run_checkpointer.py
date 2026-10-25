import json

import pytest

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentExecutor
from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    TerminalLinkState,
    checkpoint_path,
    read_checkpoints,
)
from nailong_agent_sdk.state.graph import StateGraph
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat
from tests.support.elastic_agents import binding
from tests.support.plans import n

SIGNER = HmacSha256Signer(b"runtime-key-0123456789abcdef-01!", signer_id="runtime")
ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-live-0123456789abcdef"}
OPTIONS = TaskRunOptions.model_validate(
    {"model_endpoint": {**ENDPOINT, "allow_insecure_http": True}}
)


def open_services(tmp_path, sink=None):
    sink = sink or FileCheckpointSink(tmp_path / "elsewhere")
    return AgentRuntimeServices.open(
        tmp_path / "run", checkpointer=RunHeadCheckpointer(SIGNER, sink)
    )


def close(services):
    services.audit_logs.close()
    services.telemetry.close()


def run_checkpointed_task(services, task_id="t1"):
    subject = AgentTaskRunner(services, transport=ScriptedChatTransport([final_chat()]))
    result = arun(subject.run(definition(), task(task_id), OPTIONS))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    return result


def test_a_task_run_is_checkpointed_when_it_ends_and_verifies_as_matching(tmp_path):
    services = open_services(tmp_path)
    try:
        run_checkpointed_task(services)
        records = read_checkpoints(checkpoint_path(tmp_path / "elsewhere", "t1"))
        assert len(records) == 1 and records[0].signer_id == "runtime"
        events = services.telemetry.list_events("t1", limit=500)
        assert events[-1].event_type == "run.completed"
        assert records[0].telemetry_sequence == events[-1].sequence
        report = services.checkpointer.check(
            services.run_root, services.telemetry, services.audit_logs, "t1"
        )
        assert report.verification.verdict is CheckpointVerdict.MATCHES and report.ok
        assert report.terminal_link.state is TerminalLinkState.LINKED
    finally:
        close(services)


def test_a_failed_checkpoint_is_recorded_by_name_and_does_not_fail_the_run(tmp_path):
    class FullDisk(FileCheckpointSink):
        def append(self, run_id, record):
            raise OSError("no space left on the checkpoint volume")

    services = open_services(tmp_path, FullDisk(tmp_path / "elsewhere"))
    try:
        result = run_checkpointed_task(services)
        assert result.status is AgentRunStatus.COMPLETED
        failures = {
            event.payload["subject"]: event
            for event in services.telemetry.list_events("t1", limit=500)
            if event.event_type == "run.checkpoint-failed"
        }
        assert set(failures) <= {"run-head", "artifact-log"} and "run-head" in failures
        failure = failures["run-head"]
        assert failure.status == "failed"
        assert failure.payload["error_type"] == "OSError"
        assert "no space left on the checkpoint volume" in failure.payload["error"]
        assert not checkpoint_path(tmp_path / "elsewhere", "t1").exists()
    finally:
        close(services)


def test_a_refused_checkpoint_names_the_run_and_the_reason(tmp_path):
    services = open_services(tmp_path)
    try:
        run_checkpointed_task(services)
        path = checkpoint_path(tmp_path / "elsewhere", "t1")
        record = json.loads(path.read_text("utf-8"))
        record["telemetry_sequence"] = 1
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        run_checkpointed_task(services)
        failures = [
            event
            for event in services.telemetry.list_events("t1", limit=500)
            if event.event_type == "run.checkpoint-failed"
        ]
        assert len(failures) == 1
        assert failures[0].payload["error_code"] == "RUN_CHECKPOINT_MISMATCH"
        assert "signature-invalid" in failures[0].payload["error"]
    finally:
        close(services)


def test_without_a_checkpointer_nothing_is_written_or_recorded(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run", checkpointer=None)
    try:
        run_checkpointed_task(services)
        types = {event.event_type for event in services.telemetry.list_events("t1", limit=500)}
        assert "run.checkpoint-failed" not in types
        assert services.checkpoint_run("t1") is None
        assert not (tmp_path / "elsewhere").exists()
    finally:
        close(services)


def test_services_refuse_a_sink_inside_the_run_root_when_they_open(tmp_path):
    sink = FileCheckpointSink(tmp_path / "run" / "checkpoints")
    with pytest.raises(AgentSdkError) as refused:
        AgentRuntimeServices.open(tmp_path / "run", checkpointer=RunHeadCheckpointer(SIGNER, sink))
    assert refused.value.code == "RUN_CHECKPOINT_INSIDE_RUN_ROOT"
    assert "another volume or host" in str(refused.value)


def test_every_node_agent_of_a_graph_run_is_checkpointed(tmp_path):
    services = open_services(tmp_path)
    try:
        executor = GraphAgentExecutor(
            services, {"root": binding("root", tool=False), "after": binding("after", tool=False)}
        )
        graph = StateGraph([n("root"), n("after", ["root"])])
        arun(graph.execute(executor.executors(), run_id="run-7"))
        for node_id in ("root", "after"):
            run_id = f"task-{node_id}"
            report = services.checkpointer.check(
                services.run_root, services.telemetry, services.audit_logs, run_id
            )
            assert report.verification.verdict is CheckpointVerdict.MATCHES, report
            assert report.terminal_link.state is TerminalLinkState.LINKED
            (record,) = read_checkpoints(checkpoint_path(tmp_path / "elsewhere", run_id))
            assert record.run_id == run_id
    finally:
        close(services)


def test_the_audit_payload_cap_is_chosen_when_the_services_open(tmp_path):
    small = AgentRuntimeServices.open(tmp_path / "small", audit_max_payload_chars=300)
    try:
        entry = small.audit_logs.append("r", "big", {"text": "x" * 1_000})
        assert entry.payload["truncated"] is True and len(entry.payload["preview"]) == 300
    finally:
        close(small)
    with pytest.raises(ValueError, match="max_payload_chars must be at least 256"):
        AgentRuntimeServices.open(tmp_path / "tiny", audit_max_payload_chars=100)
