import logging

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_models import (
    MetricAvailability,
    MetricObservation,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

ACTOR = TelemetryActor(kind="system", identifier="test")


def emit(store, run_id, event_type="agent.step"):
    return store.emit(
        event_type,
        TelemetryContext(run_id=run_id),
        actor=ACTOR,
        authority=TelemetryAuthority.DETERMINISTIC,
        status="ok",
    )


def metric(store, run_id, value=1.0):
    store.record_metric(
        MetricObservation(
            metric_id="m",
            run_id=run_id,
            value=value,
            unit="count",
            availability=MetricAvailability.AVAILABLE,
            observed_at_utc="2026-01-01T00:00:00+00:00",
        )
    )


def test_evicting_a_cached_handle_is_counted_and_reported_once_when_the_cache_is_cycled(
    tmp_path, caplog
):
    store = AuditTranscriptStore(tmp_path, max_open_handles=2)
    caplog.set_level(logging.WARNING, logger="nailong_agent_sdk.audit")
    assert store.handle_evictions == 0
    for run_id in ("r1", "r2"):
        store.append(run_id, "step", {})
    assert store.handle_evictions == 0 and not caplog.records
    store.append("r3", "step", {})
    assert store.handle_evictions == 1 and not caplog.records
    store.append("r1", "step", {})
    assert store.handle_evictions == 2
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    for needle in (
        "2 append handles",
        "max_open_handles=2",
        "AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES",
    ):
        assert needle in text, (needle, text)
    for run_id in ("r2", "r3", "r1", "r2"):
        store.append(run_id, "step", {})
    assert store.handle_evictions > 2
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert all(store.verify(run_id) for run_id in ("r1", "r2", "r3"))
    store.close()


def test_a_cache_big_enough_for_the_active_runs_never_evicts(tmp_path):
    store = AuditTranscriptStore(tmp_path, max_open_handles=4)
    for _ in range(3):
        for run_id in ("a", "b", "c", "d"):
            store.append(run_id, "step", {})
    assert store.handle_evictions == 0
    store.close()


def test_the_audit_footprint_reports_entries_bytes_and_the_chain_head(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    empty = store.footprint("nothing")
    assert (empty.entries, empty.bytes, empty.head_hash) == (0, 0, None)
    last = None
    for index in range(4):
        last = store.append("run-1", "step", {"i": index})
    store.render_markdown("run-1")
    footprint = store.footprint("run-1")
    assert footprint.entries == 4 and footprint.head_hash == last.integrity_hash
    transcript = tmp_path / ".agent-audit-logs" / "run-1.jsonl"
    rendered = tmp_path / ".agent-audit-logs" / "run-1.transcript.md"
    assert footprint.bytes == transcript.stat().st_size + rendered.stat().st_size
    assert footprint.last_at_utc == last.occurred_at_utc
    store.close()


def test_pruning_an_audit_run_removes_its_files_and_nothing_else(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    for run_id in ("old", "keep"):
        for index in range(3):
            store.append(run_id, "step", {"i": index})
    store.render_markdown("old")
    expected = store.footprint("old")
    dry = store.prune_run("old", dry_run=True)
    assert dry == expected and (tmp_path / ".agent-audit-logs" / "old.jsonl").is_file()
    removed = store.prune_run("old")
    assert removed == expected
    folder = tmp_path / ".agent-audit-logs"
    assert not (folder / "old.jsonl").exists() and not (folder / "old.transcript.md").exists()
    assert store.footprint("old").entries == 0
    assert store.entry_count("keep") == 3 and store.verify("keep")
    fresh = store.append("old", "step", {})
    assert fresh.sequence == 1 and fresh.previous_hash is None and store.verify("old")
    assert store.prune_run("never-existed").entries == 0
    store.close()


def test_a_read_only_audit_store_refuses_to_prune(tmp_path):
    AuditTranscriptStore(tmp_path).append("run-1", "step", {})
    reader = AuditTranscriptStore(tmp_path, read_only=True)
    with pytest.raises(RuntimeError, match='opened read-only; "prune_run" is not available'):
        reader.prune_run("run-1")
    assert reader.prune_run("run-1", dry_run=True).entries == 1


def test_run_activity_lists_every_run_with_its_times_and_whether_it_terminated(tmp_path):
    store = TelemetryStore(tmp_path)
    emit(store, "done")
    emit(store, "done", "agent.terminated")
    emit(store, "busy")
    first = emit(store, "empty-after")
    activity = {item.run_id: item for item in store.run_activity()}
    assert set(activity) == {"done", "busy", "empty-after"}
    assert activity["done"].event_count == 2 and activity["done"].last_sequence == 2
    assert activity["done"].terminal is False
    flagged = {
        item.run_id: item for item in store.run_activity(terminal_event_types={"agent.terminated"})
    }
    assert flagged["done"].terminal is True
    assert flagged["busy"].terminal is False and flagged["empty-after"].terminal is False
    assert flagged["empty-after"].first_event_at == first.occurred_at_utc
    assert flagged["done"].last_event_at >= flagged["done"].first_event_at
    store.close()


def test_a_run_that_terminated_and_continued_is_still_a_terminated_run(tmp_path):
    store = TelemetryStore(tmp_path)
    emit(store, "r", "agent.terminated")
    emit(store, "r", "agent.state-updated")
    activity = store.run_activity(terminal_event_types={"agent.terminated"})
    assert activity[0].terminal is True and activity[0].event_count == 2
    store.close()


def test_the_telemetry_footprint_reports_counts_bytes_and_the_chain_head(tmp_path):
    store = TelemetryStore(tmp_path)
    for _ in range(3):
        last = emit(store, "run-1")
    metric(store, "run-1")
    metric(store, "run-1", 2.0)
    footprint = store.footprint("run-1")
    assert (footprint.event_count, footprint.metric_count) == (3, 2)
    assert (footprint.first_sequence, footprint.last_sequence) == (1, 3)
    assert footprint.head_hash == last.integrity_hash and footprint.bytes > 0
    unknown = store.footprint("none")
    assert (unknown.event_count, unknown.metric_count, unknown.head_hash) == (0, 0, None)
    store.close()


def test_pruning_a_telemetry_run_deletes_its_events_and_metrics_only(tmp_path):
    store = TelemetryStore(tmp_path)
    for run_id in ("old", "keep"):
        for _ in range(3):
            emit(store, run_id)
        metric(store, run_id)
    expected = store.footprint("old")
    assert store.prune_run("old", dry_run=True) == expected
    assert store.footprint("old") == expected
    assert store.prune_run("old") == expected
    assert store.footprint("old").event_count == 0 and store.list_metrics("old") == []
    assert [item.run_id for item in store.run_activity()] == ["keep"]
    assert store.footprint("keep").event_count == 3 and store.chain_break("keep") is None
    store.close()


def test_pruning_refuses_when_the_run_gained_an_event_since_it_was_examined(tmp_path):
    store = TelemetryStore(tmp_path)
    emit(store, "busy")
    seen = store.footprint("busy")
    emit(store, "busy")
    with pytest.raises(AgentSdkError) as raised:
        store.prune_run("busy", expected_last_sequence=seen.last_sequence)
    assert raised.value.code == "TELEMETRY_RUN_CHANGED"
    assert 'run "busy"' in str(raised.value) and "sequence 1" in str(raised.value)
    assert store.footprint("busy").event_count == 2
    store.close()


def test_a_read_only_telemetry_store_refuses_to_prune(tmp_path):
    TelemetryStore(tmp_path).close()
    reader = TelemetryStore(tmp_path, read_only=True)
    with pytest.raises(RuntimeError, match='opened read-only; "prune_run" is not available'):
        reader.prune_run("run-1")
    reader.close()
