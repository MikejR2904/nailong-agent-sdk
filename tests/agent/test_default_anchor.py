import os

from nailong_agent_sdk.agent.run_checkpointer import ANCHOR_HOME_VARIABLE, RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    checkpoint_path,
    read_checkpoints,
)
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat

ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-live-0123456789abcdef"}
OPTIONS = TaskRunOptions.model_validate(
    {"model_endpoint": {**ENDPOINT, "allow_insecure_http": True}}
)


def close(services):
    services.audit_logs.close()
    services.telemetry.close()


def run_task(services):
    subject = AgentTaskRunner(services, transport=ScriptedChatTransport([final_chat()]))
    result = arun(subject.run(definition(), task("t1"), OPTIONS))
    assert result.status is AgentRunStatus.COMPLETED, result.reason


def test_a_service_opened_without_a_checkpointer_anchors_outside_the_run_root(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        assert services.checkpointer is not None
        home = os.environ[ANCHOR_HOME_VARIABLE]
        assert not services.checkpointer.sink.is_inside(tmp_path / "run")
        run_task(services)
        records = read_checkpoints(checkpoint_path(services.checkpointer.sink.directory, "t1"))
        assert records and str(services.checkpointer.sink.directory).startswith(home)
    finally:
        close(services)


def test_the_default_anchor_verifies_a_finished_run_as_matching(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        run_task(services)
        report = services.checkpointer.check(
            services.run_root, services.telemetry, services.audit_logs, "t1"
        )
        assert report.verification.verdict is CheckpointVerdict.MATCHES
        assert len(services.telemetry.list_events("t1", limit=500)) > 0
    finally:
        close(services)


def test_none_switches_the_anchor_off(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run", checkpointer=None)
    try:
        assert services.checkpointer is None
    finally:
        close(services)


def test_the_key_is_created_once_and_reused_and_each_run_root_has_its_own_sink(tmp_path):
    first = RunHeadCheckpointer.local(tmp_path / "a", tmp_path / "home")
    second = RunHeadCheckpointer.local(tmp_path / "a", tmp_path / "home")
    other = RunHeadCheckpointer.local(tmp_path / "b", tmp_path / "home")
    message = b"m"
    assert second.signer.verify(message, first.signer.sign(message))
    assert first.sink.directory == second.sink.directory != other.sink.directory
    assert isinstance(first.sink, FileCheckpointSink)
    assert len(list((tmp_path / "home").glob("anchor.key"))) == 1


def test_an_unusable_anchor_home_leaves_the_service_unanchored_and_says_why(
    tmp_path, monkeypatch, caplog
):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setenv(ANCHOR_HOME_VARIABLE, str(blocked))
    with caplog.at_level("WARNING"):
        services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        assert services.checkpointer is None
        assert ANCHOR_HOME_VARIABLE in caplog.text and "tamper-evident only" in caplog.text
    finally:
        close(services)
