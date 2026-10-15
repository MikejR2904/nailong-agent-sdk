import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.retention import RetentionPolicy, pruned_heads_lookup
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.hashing import canonical_hash
from nailong_agent_sdk.observability.audit_log import AuditLogEntry
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    PrunedHeads,
    checkpoint_path,
    export_run_head_checkpoint,
    read_checkpoints,
    verify_run_head_checkpoints,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call

POLICY = RetentionPolicy(older_than_seconds=3_600)
SIGNER = HmacSha256Signer(b"prune-key-0123456789abcdef-0123!", signer_id="ops-hmac")


def later():
    return datetime.now(UTC) + timedelta(days=2)


def run_task(services, task_id, calls=2):
    turns = [tool_call(f"{task_id}-c{index}", "echo", {"i": index}) for index in range(calls)]
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=calls + 3),
        ScriptedModel([*turns, final()]),
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    result = arun(subject.run(task(task_id)))
    assert result.status is AgentRunStatus.COMPLETED, (result.status, result.reason)
    return result


def prune(services):
    report = services.retention().prune(POLICY, dry_run=False, now=later())
    assert report.runs, "retention pruned nothing"
    return report


def verify(services, destination, run_id="run-a", lookup=True):
    return verify_run_head_checkpoints(
        services.telemetry,
        services.audit_logs,
        run_id,
        checkpoint_path(destination, run_id),
        SIGNER,
        pruned_lookup=pruned_heads_lookup(services.run_root) if lookup else None,
    )


def checkpoint(services, destination, run_id="run-a"):
    return export_run_head_checkpoint(
        services.telemetry,
        services.audit_logs,
        run_id,
        SIGNER,
        destination,
        pruned_lookup=pruned_heads_lookup(services.run_root),
    )


@pytest.fixture
def world(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    yield services, tmp_path / "elsewhere"
    services.audit_logs.close()
    services.telemetry.close()


def test_a_pruned_run_is_pruned_not_truncated_when_the_tombstone_is_consulted(world):
    services, destination = world
    run_task(services, "run-a")
    first = checkpoint(services, destination)
    prune(services)
    pruned = verify(services, destination)
    assert pruned.verdict is CheckpointVerdict.PRUNED and pruned.ok
    assert pruned.checkpointed_telemetry_sequence == first.telemetry_sequence
    assert "tombstone 1" in pruned.detail and "same final heads" in pruned.detail
    blind = verify(services, destination, lookup=False)
    assert blind.verdict is CheckpointVerdict.TRUNCATED and not blind.ok


def test_a_run_that_grew_before_it_was_pruned_says_so(world):
    services, destination = world
    run_task(services, "run-a", calls=1)
    checkpoint(services, destination)
    run_task(services, "run-a", calls=3)
    prune(services)
    verdict = verify(services, destination)
    assert verdict.verdict is CheckpointVerdict.PRUNED and verdict.ok
    assert "after growing by" in verdict.detail and "no longer be checked" in verdict.detail


def test_a_run_cut_short_before_it_was_pruned_is_still_truncated(world):
    services, destination = world
    run_task(services, "run-a")
    first = checkpoint(services, destination)
    connection = sqlite3.connect(services.telemetry.database_path)
    connection.execute(
        "DELETE FROM events WHERE run_id = ? AND sequence > ?",
        ("run-a", first.telemetry_sequence - 2),
    )
    connection.commit()
    connection.close()
    prune(services)
    verdict = verify(services, destination)
    assert verdict.verdict is CheckpointVerdict.TRUNCATED
    assert "before retention pruned it" in verdict.detail and "tombstone 1" in verdict.detail


def test_a_run_rewritten_and_resealed_before_it_was_pruned_is_diverged(world):
    services, destination = world
    run_task(services, "run-a")
    checkpoint(services, destination)
    services.audit_logs.close()
    path = services.audit_logs._jsonl_path("run-a")
    previous = None
    sealed = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = AuditLogEntry.model_validate_json(line)
        payload = dict(entry.payload)
        if entry.sequence == 3:
            payload["forged"] = True
        draft = entry.model_copy(
            update={"payload": payload, "previous_hash": previous, "integrity_hash": ""}
        )
        done = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        previous = done.integrity_hash
        sealed.append(done.model_dump_json())
    path.write_text("".join(f"{line}\n" for line in sealed), encoding="utf-8")
    prune(services)
    verdict = verify(services, destination)
    assert verdict.verdict is CheckpointVerdict.DIVERGED
    assert "before retention pruned it" in verdict.detail and "final audit entry" in verdict.detail


def test_a_run_id_used_again_after_a_prune_gets_a_fresh_checkpoint(world):
    services, destination = world
    run_task(services, "run-a", calls=3)
    old = checkpoint(services, destination)
    prune(services)
    assert verify(services, destination).verdict is CheckpointVerdict.PRUNED
    run_task(services, "run-a", calls=0)
    uncovered = verify(services, destination)
    assert uncovered.verdict is CheckpointVerdict.NO_CHECKPOINT and not uncovered.ok
    assert "earlier run of that id" in uncovered.detail
    fresh = checkpoint(services, destination)
    assert fresh.telemetry_sequence < old.telemetry_sequence
    covered = verify(services, destination)
    assert covered.verdict is CheckpointVerdict.MATCHES and covered.checkpoints == 2
    assert len(read_checkpoints(checkpoint_path(destination, "run-a"))) == 2


def test_a_run_removed_without_a_tombstone_is_truncated_even_with_the_lookup(world):
    services, destination = world
    run_task(services, "run-a")
    checkpoint(services, destination)
    connection = sqlite3.connect(services.telemetry.database_path)
    connection.execute("DELETE FROM events WHERE run_id = ?", ("run-a",))
    connection.commit()
    connection.close()
    services.audit_logs.close()
    services.audit_logs._jsonl_path("run-a").unlink()
    verdict = verify(services, destination)
    assert verdict.verdict is CheckpointVerdict.TRUNCATED and "holds 0 of the" in verdict.detail


def test_a_tombstone_older_than_the_checkpoint_does_not_excuse_a_missing_run(world):
    services, destination = world
    run_task(services, "run-a")
    first = checkpoint(services, destination)
    connection = sqlite3.connect(services.telemetry.database_path)
    connection.execute("DELETE FROM events WHERE run_id = ?", ("run-a",))
    connection.commit()
    connection.close()
    services.audit_logs.close()
    services.audit_logs._jsonl_path("run-a").unlink()
    early = PrunedHeads(
        run_id="run-a",
        telemetry_sequence=first.telemetry_sequence + 5,
        telemetry_head="a" * 64,
        audit_sequence=first.audit_sequence + 5,
        audit_head="b" * 64,
        pruned_at_utc=(datetime.now(UTC) - timedelta(days=30)).isoformat(),
        tombstone_sequence=1,
    )
    verdict = verify_run_head_checkpoints(
        services.telemetry,
        services.audit_logs,
        "run-a",
        checkpoint_path(destination, "run-a"),
        SIGNER,
        pruned_lookup=lambda run_id: [early],
    )
    assert verdict.verdict is CheckpointVerdict.TRUNCATED


def test_a_tombstone_log_that_cannot_be_read_is_named_in_the_verdict(world):
    services, destination = world
    run_task(services, "run-a")
    checkpoint(services, destination)
    prune(services)

    def broken(run_id):
        raise AgentSdkError("RETENTION_TOMBSTONES_BROKEN", "the log is broken at entry 3")

    verdict = verify_run_head_checkpoints(
        services.telemetry,
        services.audit_logs,
        "run-a",
        checkpoint_path(destination, "run-a"),
        SIGNER,
        pruned_lookup=broken,
    )
    assert verdict.verdict is CheckpointVerdict.TRUNCATED
    assert (
        "could not be read" in verdict.detail and "the log is broken at entry 3" in verdict.detail
    )


def test_re_exporting_scans_each_chain_twice_not_five_times(world, monkeypatch):
    services, destination = world
    run_task(services, "run-a")
    checkpoint(services, destination)
    calls = {"telemetry": 0, "audit": 0}
    real_events, real_entries = TelemetryStore.iter_events, type(services.audit_logs).iter_entries

    def counted_events(self, *args, **kwargs):
        calls["telemetry"] += 1
        return real_events(self, *args, **kwargs)

    def counted_entries(self, *args, **kwargs):
        calls["audit"] += 1
        return real_entries(self, *args, **kwargs)

    monkeypatch.setattr(TelemetryStore, "iter_events", counted_events)
    monkeypatch.setattr(type(services.audit_logs), "iter_entries", counted_entries)
    checkpoint(services, destination)
    assert calls == {"telemetry": 2, "audit": 2}


def test_the_sink_can_be_any_store_that_appends_and_reads_back(world):
    services, destination = world
    run_task(services, "run-a")

    class MemorySink:
        def __init__(self):
            self.records = {}

        def location(self, run_id):
            return f"memory://{run_id}"

        def read_all(self, run_id):
            return list(self.records.get(run_id, []))

        def append(self, run_id, record):
            self.records.setdefault(run_id, []).append(record)

        def is_inside(self, root):
            return False

    sink = MemorySink()
    export_run_head_checkpoint(services.telemetry, services.audit_logs, "run-a", SIGNER, sink)
    export_run_head_checkpoint(services.telemetry, services.audit_logs, "run-a", SIGNER, sink)
    assert len(sink.records["run-a"]) == 2
    verified = verify_run_head_checkpoints(
        services.telemetry, services.audit_logs, "run-a", sink, SIGNER
    )
    assert verified.verdict is CheckpointVerdict.MATCHES and verified.checkpoints == 2
    assert not destination.exists()
    inside = FileCheckpointSink(services.run_root / "checkpoints")
    with pytest.raises(AgentSdkError) as refused:
        export_run_head_checkpoint(services.telemetry, services.audit_logs, "run-a", SIGNER, inside)
    assert refused.value.code == "RUN_CHECKPOINT_INSIDE_RUN_ROOT"


def test_the_developer_command_recognises_a_pruned_run_and_a_reused_id(world, tmp_path, capsys):
    services, destination = world
    key_file = tmp_path / "checkpoint.key"
    key_file.write_text("prune-key-0123456789abcdef-0123!\n", encoding="utf-8")
    base = [str(services.run_root), "run-a"]
    options = ["--key-file", str(key_file), "--signer-id", "ops-hmac"]
    run_task(services, "run-a")
    services.audit_logs.close()
    services.telemetry.close()
    assert main(["checkpoint-run", *base, str(destination), *options]) == 0
    capsys.readouterr()
    reopened = AgentRuntimeServices.open(services.run_root)
    prune(reopened)
    reopened.audit_logs.close()
    reopened.telemetry.close()
    assert main(["verify-checkpoints", *base, str(destination), *options]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verification"]["verdict"] == "pruned" and report["ok"] is True
    again = AgentRuntimeServices.open(services.run_root)
    run_task(again, "run-a", calls=0)
    again.audit_logs.close()
    again.telemetry.close()
    assert main(["verify-checkpoints", *base, str(destination), *options]) == 1
    assert json.loads(capsys.readouterr().out)["verification"]["verdict"] == "no-checkpoint"
    assert main(["checkpoint-run", *base, str(destination), *options]) == 0
    capsys.readouterr()
    assert main(["verify-checkpoints", *base, str(destination), *options]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["verdict"] == "matches"
