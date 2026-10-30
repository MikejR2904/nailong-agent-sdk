import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.retention import RetentionPolicy, prune_run_root, read_tombstones
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from tests.support.retention_runs import POLICY, emit, finished_run, journal_files, later


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
