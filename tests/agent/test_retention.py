import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.retention import (
    RetentionPolicy,
    RunRetention,
    prune_run_root,
    read_tombstones,
    tombstone_chain_break,
    tombstone_path,
    tombstoned_handles,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.atomic_io import exclusive_file_lock
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.observability.telemetry_models import (
    MetricAvailability,
    MetricObservation,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)

ACTOR = TelemetryActor(kind="system", identifier="test")
POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    return datetime.now(UTC) + timedelta(days=2)


def emit(services, run_id, event_type):
    return services.telemetry.emit(
        event_type,
        TelemetryContext(run_id=run_id),
        actor=ACTOR,
        authority=TelemetryAuthority.DETERMINISTIC,
        status="ok",
    )


def finished_run(services, run_id, *, handles=2, entries=3, terminal=True, metrics=1):
    emit(services, run_id, "agent.run-started")
    for index in range(entries):
        services.audit_logs.append(run_id, "step", {"marker": f"{run_id}-{index}"})
    scoped = services.result_journal.for_run(run_id)
    recorded = [
        scoped.record(
            ToolCall(id=f"{run_id}-c{index}", name="echo", arguments={"i": index}),
            ToolExecutionResult(status="succeeded", output={"run": run_id, "i": index}),
        )
        for index in range(handles)
    ]
    for _ in range(metrics):
        services.telemetry.record_metric(
            MetricObservation(
                metric_id="m",
                run_id=run_id,
                value=1.0,
                unit="count",
                availability=MetricAvailability.AVAILABLE,
                observed_at_utc="2026-01-01T00:00:00+00:00",
            )
        )
    if terminal:
        emit(services, run_id, "agent.terminated")
    return recorded


@pytest.fixture
def services(tmp_path):
    opened = AgentRuntimeServices.open(tmp_path)
    yield opened
    opened.audit_logs.close()
    opened.telemetry.close()


def journal_files(services):
    return sorted(
        path.name for path in (services.run_root / ".agent-tool-results").glob("result-*.json")
    )


def test_a_policy_must_say_how_old_a_run_has_to_be():
    with pytest.raises(ValidationError, match="older_than_seconds"):
        RetentionPolicy()
    for bad in (0, -1, 3_153_600_001):
        with pytest.raises(ValidationError, match="older_than_seconds"):
            RetentionPolicy(older_than_seconds=bad)
    with pytest.raises(ValidationError, match="keep_most_recent"):
        RetentionPolicy(older_than_seconds=1, keep_most_recent=-1)
    with pytest.raises(ValidationError, match="max_runs"):
        RetentionPolicy(older_than_seconds=1, max_runs=0)
    with pytest.raises(ValidationError, match="surprise"):
        RetentionPolicy(older_than_seconds=1, surprise=True)


def test_a_dry_run_reports_what_would_go_and_writes_nothing(services):
    finished_run(services, "old", handles=3, entries=4, metrics=2)
    expected_bytes = (
        services.telemetry.footprint("old").bytes
        + services.audit_logs.footprint("old").bytes
        + services.result_journal.footprint("old").bytes
    )
    report = services.retention().prune(POLICY, now=later())
    assert report.dry_run is True and report.clean is True and report.runs_examined == 1
    (run,) = report.runs
    assert (run.run_id, run.metrics, run.audit_entries, run.journal_handles) == ("old", 2, 4, 3)
    assert run.events == 2 and run.bytes == expected_bytes == report.bytes
    assert run.tombstone_sequence is None and run.report_path is None
    assert services.telemetry.footprint("old").event_count == 2
    assert services.audit_logs.entry_count("old") == 4 and len(journal_files(services)) == 3
    assert not (services.run_root / ".agent-retention").exists()


def test_only_finished_runs_idle_for_long_enough_are_eligible(services):
    finished_run(services, "ended-long-ago")
    finished_run(services, "never-ended", terminal=False)
    time.sleep(0.05)
    boundary = datetime.now(UTC)
    time.sleep(0.05)
    finished_run(services, "ended-just-now")
    report = services.retention().prune(POLICY, now=boundary + timedelta(seconds=3_600))
    assert [run.run_id for run in report.runs] == ["ended-long-ago"]
    assert report.runs_examined == 3 and report.problems == []
    assert sum(report.kept.values()) == 2
    assert any('no "agent.terminated" event' in reason for reason in report.kept)
    assert any("last event within the past 3600s (older_than_seconds)" in r for r in report.kept)


def test_unfinished_runs_are_pruned_only_when_the_policy_says_so(services):
    finished_run(services, "crashed", terminal=False)
    keep = services.retention().prune(POLICY, now=later())
    assert keep.runs == [] and sum(keep.kept.values()) == 1
    opted_in = RetentionPolicy(older_than_seconds=3_600, include_unfinished=True)
    report = services.retention().prune(opted_in, now=later())
    assert [run.run_id for run in report.runs] == ["crashed"]


def test_the_most_recent_runs_are_always_kept(services):
    for run_id in ("a", "b", "c", "d"):
        finished_run(services, run_id)
    policy = RetentionPolicy(older_than_seconds=3_600, keep_most_recent=2)
    report = services.retention().prune(policy, now=later())
    assert [run.run_id for run in report.runs] == ["a", "b"]
    assert report.kept == {"among the 2 most recently active runs (keep_most_recent)": 2}


def test_one_call_prunes_at_most_max_runs_oldest_first(services):
    for run_id in ("a", "b", "c"):
        finished_run(services, run_id)
    policy = RetentionPolicy(older_than_seconds=3_600, max_runs=2)
    report = services.retention().prune(policy, now=later(), dry_run=False)
    assert [run.run_id for run in report.runs] == ["a", "b"]
    assert report.kept == {"beyond the 2 runs one call prunes (max_runs)": 1}
    assert [item.run_id for item in services.telemetry.run_activity()] == ["c"]


def test_applying_removes_whole_runs_and_leaves_every_other_run_intact(services):
    old = finished_run(services, "old", handles=3, entries=4)
    keep = finished_run(services, "keep", terminal=False)
    keep_footprint = (
        services.telemetry.footprint("keep"),
        services.audit_logs.footprint("keep"),
        services.result_journal.footprint("keep"),
    )
    dry = services.retention().prune(POLICY, now=later())
    applied = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert applied.dry_run is False and applied.bytes == dry.bytes
    assert [(run.run_id, run.events, run.bytes) for run in applied.runs] == [
        (run.run_id, run.events, run.bytes) for run in dry.runs
    ]
    assert applied.runs[0].tombstone_sequence == 1
    assert services.telemetry.footprint("old").event_count == 0
    assert services.telemetry.list_metrics("old") == []
    assert services.audit_logs.entry_count("old") == 0
    assert services.result_journal.handles_of("old") == []
    remaining = journal_files(services)
    assert not any(f"{handle.handle_id}.json" in remaining for handle in old)
    assert all(f"{handle.handle_id}.json" in remaining for handle in keep)
    assert keep_footprint == (
        services.telemetry.footprint("keep"),
        services.audit_logs.footprint("keep"),
        services.result_journal.footprint("keep"),
    )
    again = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert again.runs == [] and again.runs_examined == 1


def test_a_tombstone_records_the_final_chain_heads_and_every_handle_hash(services):
    recorded = finished_run(services, "old", handles=3, entries=4, metrics=2)
    heads = (
        services.telemetry.footprint("old"),
        services.audit_logs.footprint("old"),
        services.result_journal.footprint("old"),
    )
    services.retention().prune(POLICY, now=later(), dry_run=False)
    (tombstone,) = read_tombstones(services.run_root)
    assert (tombstone.sequence, tombstone.previous_hash, tombstone.run_id) == (1, None, "old")
    assert tombstone.policy == POLICY and tombstone.report_path is None
    assert tombstone.telemetry == heads[0] and tombstone.audit == heads[1]
    assert tombstone.journal == heads[2]
    assert [(h.handle_id, h.content_hash) for h in tombstone.journal.handles] == [
        (h.handle_id, h.content_hash) for h in recorded
    ]
    assert tombstone.telemetry.head_hash and tombstone.audit.head_hash
    assert tombstone.integrity_hash and tombstone_chain_break(services.run_root) is None
    handles = tombstoned_handles(services.run_root)
    assert set(handles) == {h.handle_id for h in recorded}
    assert handles[recorded[0].handle_id].run_id == "old"
    assert handles[recorded[0].handle_id].content_hash == recorded[0].content_hash


def test_tombstones_chain_and_any_edit_to_the_log_is_reported(services):
    for run_id in ("a", "b", "c"):
        finished_run(services, run_id)
    for _ in range(3):
        services.retention().prune(
            RetentionPolicy(older_than_seconds=3_600, max_runs=1), now=later(), dry_run=False
        )
    first, second, third = read_tombstones(services.run_root)
    assert [item.run_id for item in (first, second, third)] == ["a", "b", "c"]
    assert second.previous_hash == first.integrity_hash
    assert third.previous_hash == second.integrity_hash
    path = tombstone_path(services.run_root)
    original = path.read_text("utf-8")
    path.write_text(original.replace('"run_id": "b"', '"run_id": "z"', 1), "utf-8")
    failure = tombstone_chain_break(services.run_root)
    assert failure is not None and failure.sequence == 2 and failure.kind == "content-hash-mismatch"
    with pytest.raises(AgentSdkError) as raised:
        read_tombstones(services.run_root)
    assert raised.value.code == "RETENTION_TOMBSTONES_BROKEN"
    assert "sequence 2" in str(raised.value) and "content-hash-mismatch" in str(raised.value)
    lines = original.splitlines(keepends=True)
    path.write_text("".join([lines[0], lines[2]]), "utf-8")
    removed = tombstone_chain_break(services.run_root)
    assert removed is not None and removed.kind == "previous-hash-mismatch"
    path.write_text(original + "not json\n", "utf-8")
    unreadable = tombstone_chain_break(services.run_root)
    assert unreadable is not None and unreadable.kind == "unreadable-entry"
    assert unreadable.sequence == 4


def test_pruning_refuses_to_start_when_the_tombstone_log_is_broken(services):
    finished_run(services, "a")
    finished_run(services, "b")
    services.retention().prune(
        RetentionPolicy(older_than_seconds=3_600, max_runs=1), now=later(), dry_run=False
    )
    path = tombstone_path(services.run_root)
    path.write_text(path.read_text("utf-8").replace('"run_id": "a"', '"run_id": "q"'), "utf-8")
    with pytest.raises(AgentSdkError) as raised:
        services.retention().prune(POLICY, now=later(), dry_run=False)
    assert raised.value.code == "RETENTION_TOMBSTONES_BROKEN"
    assert services.telemetry.footprint("b").event_count == 2
    assert services.audit_logs.entry_count("b") == 3


def test_an_interrupted_prune_is_finished_by_the_next_one_with_a_single_tombstone(
    services, monkeypatch
):
    recorded = finished_run(services, "old", handles=2)
    real = FileToolResultJournal.prune_run

    def fail(self, run_id, *, dry_run=False):
        if dry_run:
            return real(self, run_id, dry_run=True)
        raise OSError("disk gone")

    monkeypatch.setattr(FileToolResultJournal, "prune_run", fail)
    broken = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert broken.runs == [] and len(broken.problems) == 1
    reason = broken.problems[0].reason
    assert broken.problems[0].run_id == "old"
    for needle in (
        'Pruning run "old"',
        "removing its tool results",
        "OSError: disk gone",
        "Tombstone 1",
    ):
        assert needle in reason, (needle, reason)
    assert len(read_tombstones(services.run_root)) == 1
    assert services.telemetry.footprint("old").event_count == 2
    assert len(journal_files(services)) == 2
    monkeypatch.setattr(FileToolResultJournal, "prune_run", real)
    done = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert [run.tombstone_sequence for run in done.runs] == [1]
    assert len(read_tombstones(services.run_root)) == 1
    assert journal_files(services) == []
    assert tombstoned_handles(services.run_root).keys() == {h.handle_id for h in recorded}


def test_a_half_finished_prune_that_already_removed_the_audit_log_still_completes(
    services, monkeypatch
):
    finished_run(services, "old")
    real = type(services.telemetry).prune_run
    calls = {"count": 0}

    def fail_at_the_end(self, run_id, *, dry_run=False, expected_last_sequence=None):
        if not dry_run:
            calls["count"] += 1
            if calls["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
        return real(self, run_id, dry_run=dry_run, expected_last_sequence=expected_last_sequence)

    monkeypatch.setattr(type(services.telemetry), "prune_run", fail_at_the_end)
    broken = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert "removing its telemetry" in broken.problems[0].reason
    assert "OperationalError: database is locked" in broken.problems[0].reason
    assert (
        services.audit_logs.entry_count("old") == 0
        and services.result_journal.handles_of("old") == []
    )
    done = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert [run.run_id for run in done.runs] == ["old"]
    assert done.runs[0].audit_entries == 0
    (tombstone,) = read_tombstones(services.run_root)
    assert tombstone.audit.entries == 3 and len(tombstone.journal.handles) == 2
    assert services.telemetry.footprint("old").event_count == 0


def test_a_run_with_a_broken_chain_is_left_alone_and_reported(services):
    finished_run(services, "tampered-telemetry")
    finished_run(services, "tampered-audit")
    finished_run(services, "fine")
    connection = sqlite3.connect(services.run_root / ".agent-telemetry" / "telemetry.sqlite3")
    row = connection.execute(
        "SELECT event_json FROM events WHERE run_id='tampered-telemetry' AND sequence=1"
    ).fetchone()
    payload = json.loads(row[0])
    payload["status"] = "tampered"
    connection.execute(
        "UPDATE events SET event_json=? WHERE run_id='tampered-telemetry' AND sequence=1",
        (json.dumps(payload),),
    )
    connection.commit()
    connection.close()
    transcript = services.run_root / ".agent-audit-logs" / "tampered-audit.jsonl"
    transcript.write_text(
        transcript.read_text("utf-8").replace("tampered-audit-1", "edited-after-the-fact"), "utf-8"
    )
    report = services.retention().prune(POLICY, now=later(), dry_run=False)
    assert [run.run_id for run in report.runs] == ["fine"]
    assert report.clean is False
    problems = {item.run_id: item.reason for item in report.problems}
    assert set(problems) == {"tampered-telemetry", "tampered-audit"}
    assert (
        "telemetry chain is broken at sequence 1 (content-hash-mismatch)"
        in problems["tampered-telemetry"]
    )
    assert "audit transcript chain is broken" in problems["tampered-audit"]
    assert all("left untouched as evidence" in reason for reason in problems.values())
    assert services.telemetry.footprint("tampered-telemetry").event_count == 2
    assert transcript.is_file()
    assert [tomb.run_id for tomb in read_tombstones(services.run_root)] == ["fine"]


def test_a_run_that_gains_an_event_after_it_was_examined_is_not_pruned(services, monkeypatch):
    finished_run(services, "old")
    real = type(services.audit_logs).footprint

    def revive_then_measure(self, run_id):
        emit(services, run_id, "agent.state-updated")
        return real(self, run_id)

    monkeypatch.setattr(type(services.audit_logs), "footprint", revive_then_measure)
    report = services.retention().prune(POLICY, now=later(), dry_run=False)
    monkeypatch.setattr(type(services.audit_logs), "footprint", real)
    assert report.runs == [] and len(report.problems) == 1
    reason = report.problems[0].reason
    for needle in (
        'Pruning run "old"',
        "confirming that it received no new event",
        "TELEMETRY_RUN_CHANGED",
        "so nothing was pruned",
    ):
        assert needle in reason, (needle, reason)
    assert "Tombstone" not in reason
    assert services.telemetry.footprint("old").event_count == 3
    assert services.audit_logs.entry_count("old") == 3 and len(journal_files(services)) == 2
    assert not tombstone_path(services.run_root).exists()


def test_a_run_pruned_and_later_run_again_under_the_same_id_gets_its_own_tombstone(services):
    finished_run(services, "reused")
    services.retention().prune(POLICY, now=later(), dry_run=False)
    finished_run(services, "reused", handles=1, entries=1)
    services.retention().prune(POLICY, now=later(), dry_run=False)
    first, second = read_tombstones(services.run_root)
    assert (first.run_id, second.run_id) == ("reused", "reused")
    assert first.telemetry.head_hash != second.telemetry.head_hash
    assert second.sequence == 2 and second.previous_hash == first.integrity_hash
    assert len(first.journal.handles) == 2 and len(second.journal.handles) == 1
    numbers = [int(h.handle_id.removeprefix("result-")) for h in first.journal.handles]
    assert all(
        int(h.handle_id.removeprefix("result-")) > max(numbers) for h in second.journal.handles
    )


def test_reports_can_be_exported_before_the_run_is_deleted(services):
    finished_run(services, "old")
    policy = RetentionPolicy(older_than_seconds=3_600, export_reports=True)
    report = services.retention().prune(policy, now=later(), dry_run=False)
    (run,) = report.runs
    assert run.report_path == ".agent-telemetry/reports/old.run-report.json"
    exported = json.loads((services.run_root / run.report_path).read_text("utf-8"))
    assert exported["run_id"] == "old" and exported["event_count"] == 2
    assert exported["integrity_chain_valid"] is True
    (tombstone,) = read_tombstones(services.run_root)
    assert tombstone.report_path == run.report_path
    assert exported["evidence_head_hash"] == tombstone.telemetry.head_hash
    assert services.telemetry.footprint("old").event_count == 0


def test_a_held_retention_lock_makes_a_second_apply_wait_and_then_fail_by_name(services):
    finished_run(services, "old")
    retention = RunRetention(
        services.run_root,
        telemetry=services.telemetry,
        audit_logs=services.audit_logs,
        result_journal=services.result_journal,
        lock_timeout_seconds=0.2,
    )
    with exclusive_file_lock(services.run_root / ".agent-retention" / "retention.lock"):
        with pytest.raises(AgentSdkError) as raised:
            retention.prune(POLICY, now=later(), dry_run=False)
        assert raised.value.code == "RETENTION_LOCK_TIMEOUT"
        assert '"retention.lock"' in str(raised.value)
        assert retention.prune(POLICY, now=later()).runs[0].run_id == "old"
    assert services.telemetry.footprint("old").event_count == 2


def test_a_torn_last_tombstone_line_is_dropped_so_the_next_prune_can_append(services):
    finished_run(services, "a")
    finished_run(services, "b")
    services.retention().prune(
        RetentionPolicy(older_than_seconds=3_600, max_runs=1), now=later(), dry_run=False
    )
    path = tombstone_path(services.run_root)
    with path.open("ab") as stream:
        stream.write(b'{"schema_version": "agent-sdk-run-tombstone-v1", "sequence": 2, "run_')
    assert [item.run_id for item in read_tombstones(services.run_root)] == ["a"]
    services.retention().prune(POLICY, now=later(), dry_run=False)
    assert [item.run_id for item in read_tombstones(services.run_root)] == ["a", "b"]
    assert tombstone_chain_break(services.run_root) is None


def test_the_clock_the_policy_is_judged_against_must_carry_a_time_zone(services):
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        services.retention().prune(POLICY, now=datetime(2030, 1, 1))


def test_pruning_a_run_root_by_path_never_writes_during_a_dry_run(tmp_path):
    seeded = AgentRuntimeServices.open(tmp_path)
    finished_run(seeded, "old")
    seeded.audit_logs.close()
    seeded.telemetry.close()

    def listing():
        return sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
            if not path.name.endswith(("-wal", "-shm"))
        )

    before = listing()
    report = prune_run_root(tmp_path, POLICY, now=later())
    assert report.dry_run is True and [run.run_id for run in report.runs] == ["old"]
    assert listing() == before
    applied = prune_run_root(tmp_path, POLICY, dry_run=False, now=later())
    assert applied.runs[0].tombstone_sequence == 1
    assert read_tombstones(tmp_path)[0].run_id == "old"
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        prune_run_root(tmp_path / "missing", POLICY)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="has no telemetry database"):
        prune_run_root(empty, POLICY)
    assert list(empty.iterdir()) == []


def test_a_dry_run_on_a_root_without_a_journal_directory_does_not_create_one(tmp_path):
    seeded = AgentRuntimeServices.open(tmp_path)
    emit(seeded, "old", "agent.terminated")
    seeded.audit_logs.close()
    seeded.telemetry.close()
    journal = tmp_path / ".agent-tool-results"
    journal.rmdir()
    report = prune_run_root(tmp_path, POLICY, now=later())
    assert [run.run_id for run in report.runs] == ["old"] and not journal.exists()
