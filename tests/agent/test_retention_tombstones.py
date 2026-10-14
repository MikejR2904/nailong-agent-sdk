import json
import sqlite3

import pytest

from nailong_agent_sdk.agent.retention import (
    RetentionPolicy,
    RunRetention,
    read_tombstones,
    tombstone_chain_break,
    tombstone_path,
    tombstoned_handles,
)
from nailong_agent_sdk.foundations.atomic_io import exclusive_file_lock
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from tests.support.retention_runs import POLICY, finished_run, journal_files, later


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
