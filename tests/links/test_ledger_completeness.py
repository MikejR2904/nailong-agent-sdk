import sqlite3

import pytest

from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.hashing import canonical_hash, canonical_json
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.audit_log import AuditLogEntry
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    TerminalLinkState,
    checkpoint_path,
    read_checkpoints,
)
from nailong_agent_sdk.observability.telemetry_models import TelemetryEvent
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat
from tests.support.ledger_checks import ledger_summary
from tests.support.mcp_cases import BUILTIN
from tests.support.orchestration import close_all, make_factory, make_policy, make_request, pipeline

SIGNER = HmacSha256Signer(b"ledger-key-0123456789abcdef-0123", signer_id="ledger")
ENDPOINT = {
    "base_url": "http://127.0.0.1:9/v1",
    "api_key": "sk-live-0123456789abcdef",
    "allow_insecure_http": True,
}
WRITE = definition(tools=[BUILTIN["write_draft"]], max_iterations=6)
OPTIONS = TaskRunOptions.model_validate(
    {
        "model_endpoint": ENDPOINT,
        "permissions": {"capabilities": ["draft.write"], "approved_capabilities": ["draft.write"]},
        "declared_output_paths": ["out.txt", "notes/b.md"],
    }
)


def checkpointing(root, elsewhere):
    return RunHeadCheckpointer(SIGNER, FileCheckpointSink(elsewhere))


def run_scenario(tmp_path):
    services = AgentRuntimeServices.open(
        tmp_path / "run", checkpointer=checkpointing(tmp_path / "run", tmp_path / "elsewhere")
    )
    transport = ScriptedChatTransport(
        [
            tool_chat("c1", "write_draft", {"path": "out.txt", "content": "first line\n"}),
            tool_chat("c2", "write_draft", {"path": "notes/b.md", "content": "# note"}),
            final_chat(),
        ]
    )
    result = arun(
        AgentTaskRunner(services, transport=transport).run(WRITE, task("scenario-1"), OPTIONS)
    )
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    return services


def close(services):
    services.audit_logs.close()
    services.telemetry.close()


def test_a_task_scenario_leaves_two_complete_ledgers_and_exactly_its_artifacts(tmp_path):
    services = run_scenario(tmp_path)
    try:
        summary = ledger_summary(services.telemetry, services.audit_logs, "scenario-1")
        assert (summary.turns_started, summary.model_turns) == (3, 3)
        assert (summary.tool_requests, summary.tool_completions, summary.tool_results) == (2, 2, 2)
        assert summary.other_events == {"run.created": 1, "run.completed": 1}
        assert summary.terminal_link is TerminalLinkState.LINKED
        workspace = services.run_root / "workspaces" / "scenario-1"
        written = sorted(
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file() and ".agent-artifacts" not in path.parts
        )
        assert written == ["notes/b.md", "out.txt"]
        assert (workspace / "out.txt").read_text("utf-8") == "first line\n"
        metrics = {
            item.metric_id: item.value
            for item in services.telemetry.list_metrics("scenario-1")
            if item.value is not None
        }
        assert metrics["agent.model_turn_attempt_count"] == summary.model_turns
        assert metrics["agent.tool_call_attempt_count"] == summary.tool_results
        report = services.checkpointer.check(
            services.run_root, services.telemetry, services.audit_logs, "scenario-1"
        )
        assert report.verification.verdict is CheckpointVerdict.MATCHES and report.ok
        (record,) = read_checkpoints(checkpoint_path(tmp_path / "elsewhere", "scenario-1"))
        assert record.telemetry_sequence == summary.events
        assert record.audit_sequence == summary.entries
    finally:
        close(services)


def test_an_orchestrated_graph_run_leaves_complete_ledgers_for_every_node(tmp_path):
    run_root = tmp_path / "run"
    services = AgentRuntimeServices.open(
        run_root, checkpointer=checkpointing(run_root, tmp_path / "elsewhere")
    )
    orchestrator, record, executed, services = pipeline(
        run_root, make_policy(), make_request(blast=5), make_factory(), services=services
    )
    try:
        assert executed.graph_run_id is not None
        assert len(record.assignments) == 3
        for assignment in record.assignments:
            run_id = f"task-{assignment.node_id}"
            summary = ledger_summary(services.telemetry, services.audit_logs, run_id)
            assert (summary.turns_started, summary.model_turns) == (1, 1)
            assert summary.tool_requests == summary.tool_completions == summary.tool_results == 0
            assert summary.other_events == {} and summary.terminal_link is TerminalLinkState.LINKED
            report = services.checkpointer.check(
                services.run_root, services.telemetry, services.audit_logs, run_id
            )
            assert report.verification.verdict is CheckpointVerdict.MATCHES, (run_id, report)
    finally:
        close_all(orchestrator, services)
        services.audit_logs.close()


def reseal_telemetry_without(telemetry, run_id, event_type):
    connection = sqlite3.connect(telemetry.database_path)
    rows = connection.execute(
        "SELECT event_json FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
    ).fetchall()
    events = [TelemetryEvent.parse_stored(raw) for (raw,) in rows]
    dropped = next(index for index, event in enumerate(events) if event.event_type == event_type)
    kept = events[:dropped] + events[dropped + 1 :]
    connection.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
    previous = None
    for sequence, event in enumerate(kept, start=1):
        draft = event.model_copy(
            update={"sequence": sequence, "previous_event_hash": previous, "integrity_hash": ""}
        )
        sealed = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        previous = sealed.integrity_hash
        connection.execute(
            "INSERT INTO events (event_id, run_id, sequence, occurred_at_utc, event_type, "
            "status, severity, integrity_hash, previous_event_hash, payload_json, event_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sealed.event_id,
                run_id,
                sealed.sequence,
                sealed.occurred_at_utc,
                sealed.event_type,
                sealed.status,
                sealed.severity.value,
                sealed.integrity_hash,
                sealed.previous_event_hash,
                canonical_json(sealed.payload),
                sealed.model_dump_json(),
            ),
        )
    connection.commit()
    connection.close()


def reseal_audit_without(services, run_id, event_type):
    services.audit_logs.close()
    path = services.run_root / ".agent-audit-logs" / f"{file_safe_name(run_id)}.jsonl"
    entries = [
        AuditLogEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dropped = next(index for index, entry in enumerate(entries) if entry.event_type == event_type)
    kept = entries[:dropped] + entries[dropped + 1 :]
    previous = None
    lines = []
    for sequence, entry in enumerate(kept, start=1):
        draft = entry.model_copy(
            update={"sequence": sequence, "previous_hash": previous, "integrity_hash": ""}
        )
        sealed = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        previous = sealed.integrity_hash
        lines.append(sealed.model_dump_json())
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def test_a_telemetry_event_dropped_and_the_chain_resealed_is_still_caught(tmp_path):
    services = run_scenario(tmp_path)
    try:
        ledger_summary(services.telemetry, services.audit_logs, "scenario-1")
        reseal_telemetry_without(services.telemetry, "scenario-1", "agent.turn-started")
        assert services.telemetry.chain_break("scenario-1") is None
        with pytest.raises(
            AssertionError, match="cites a telemetry event that is not in the ledger"
        ):
            ledger_summary(services.telemetry, services.audit_logs, "scenario-1")
    finally:
        close(services)


def test_a_model_turn_dropped_from_the_audit_log_and_resealed_is_caught_twice(tmp_path):
    services = run_scenario(tmp_path)
    try:
        before = ledger_summary(services.telemetry, services.audit_logs, "scenario-1")
        assert before.turns_started == before.model_turns == 3
        reseal_audit_without(services, "scenario-1", "model-turn")
        assert services.audit_logs.chain_break("scenario-1") is None
        entries = services.audit_logs.list_entries("scenario-1", limit=1_000)
        assert sum(entry.event_type == "model-turn" for entry in entries) == 2
        with pytest.raises(AssertionError, match="telemetry event recorded audit entry"):
            ledger_summary(services.telemetry, services.audit_logs, "scenario-1")
        report = services.checkpointer.check(
            services.run_root, services.telemetry, services.audit_logs, "scenario-1"
        )
        assert not report.ok
        assert report.verification.verdict in {
            CheckpointVerdict.TRUNCATED,
            CheckpointVerdict.DIVERGED,
        }
        assert report.terminal_link.state is TerminalLinkState.BROKEN
    finally:
        close(services)
