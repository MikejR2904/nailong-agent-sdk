# Copyright (c) 2026 David Michael Indraputra

"""Retention, bounded reads, and run-report accuracy for the growing ledgers."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nailong_agent_sdk import (
    AuditTranscriptStore,
    RetentionPolicy,
    TelemetryStore,
    ToolCall,
    apply_retention,
)
from nailong_agent_sdk.foundations.contracts import ToolExecutionResult
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.memory.episode_models import EpisodeState
from nailong_agent_sdk.memory.episode_store import FileEpisodeStore
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)

_ACTOR = TelemetryActor(kind="system", identifier="test")


def _emit(store: TelemetryStore, run_id: str, count: int = 3) -> None:
    for index in range(count):
        store.emit(
            "test.event",
            TelemetryContext(run_id=run_id),
            actor=_ACTOR,
            authority=TelemetryAuthority.DETERMINISTIC,
            status="completed",
            payload={"index": index},
        )


def _age(path: Path, days: float) -> None:
    stamp = time.time() - days * 86_400
    os.utime(path, (stamp, stamp))


def test_telemetry_prune_keeps_latest_runs_whole_and_verifiable(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path)
    for run_id in ("run-a", "run-b", "run-c"):
        _emit(store, run_id)
        time.sleep(0.01)
    removed = store.prune(keep_latest_runs=2)
    assert removed == ["run-a"]
    assert store.list_events("run-a") == []
    assert store.verify_run_chain("run-b") and store.verify_run_chain("run-c")
    assert store.prune(older_than=datetime.now(UTC) + timedelta(days=1)) == ["run-c", "run-b"]
    store.close()


def test_audit_prune_by_age_and_count(tmp_path: Path) -> None:
    audit = AuditTranscriptStore(tmp_path)
    for run_id in ("old", "mid", "new"):
        audit.append(run_id, "step", {"run": run_id})
    root = tmp_path / ".agent-audit-logs"
    _age(root / "old.jsonl", 40)
    _age(root / "mid.jsonl", 20)
    assert audit.prune(older_than=datetime.now(UTC) - timedelta(days=30)) == ["old"]
    assert audit.prune(keep_latest_runs=1) == ["mid"]
    assert sorted(path.name for path in root.glob("*.jsonl")) == ["new.jsonl"]
    assert not list(root.glob("old*")) and not list(root.glob("mid*"))
    assert audit.chain_break("new") is None
    audit.close()


def test_audit_tail_cache_notices_another_writer(tmp_path: Path) -> None:
    first = AuditTranscriptStore(tmp_path)
    second = AuditTranscriptStore(tmp_path)
    first.append("run", "a", {})
    second.append("run", "b", {})
    entry = first.append("run", "c", {})  # must not reuse sequence 2
    assert entry.sequence == 3
    assert [item.sequence for item in first.list_entries("run")] == [1, 2, 3]
    assert first.chain_break("run") is None
    first.close()
    second.close()


def test_audit_list_entries_stops_at_the_page(tmp_path: Path) -> None:
    audit = AuditTranscriptStore(tmp_path)
    for index in range(20):
        audit.append("run", "step", {"index": index})
    # A torn trailing line past the page must not be parsed.
    with (tmp_path / ".agent-audit-logs" / "run.jsonl").open("ab") as stream:
        stream.write(b'{"torn":')
    assert [entry.sequence for entry in audit.list_entries("run", limit=5)] == [1, 2, 3, 4, 5]
    assert len(audit.list_entries("run", limit=50, through_sequence=7)) == 7
    audit.close()


def test_journal_prune_never_reuses_a_handle(tmp_path: Path) -> None:
    journal = FileToolResultJournal(tmp_path)
    call = ToolCall(id="c1", name="noop", arguments={})
    result = ToolExecutionResult(status="succeeded", output="x")
    handles = [journal.record(call, result).handle_id for _ in range(3)]
    for path in (tmp_path / ".agent-tool-results").glob("result-*.json"):
        _age(path, 10)
    assert journal.prune(older_than=datetime.now(UTC) - timedelta(days=1)) == 3
    reopened = FileToolResultJournal(tmp_path)
    assert reopened.record(call, result).handle_id == "result-4"
    try:
        reopened.read(handles[0])
    except ValueError as error:
        assert "retention" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a pruned handle must not resolve")


def _compacted_store(tmp_path: Path) -> FileEpisodeStore:
    store = FileEpisodeStore(tmp_path)
    ids = []
    for index in range(4):
        record = store.open_exploratory(f"owner-{index}")
        store.close(record.id, description=f"finding {index}")
        ids.append(record.id)
    action = store.open_action("owner-x", [ids[0]])
    for episode_id in ids:
        store.mark_accessed([episode_id])
        store._compact_record(episode_id)
    store.persist()
    assert action.id == "episode-5"
    return store


def test_episode_store_load_round_trips_counters(tmp_path: Path) -> None:
    _compacted_store(tmp_path)
    reopened = FileEpisodeStore(tmp_path)
    loaded = reopened.load()
    assert [record.id for record in loaded] == [f"episode-{n}" for n in (1, 2, 3, 4, 5)]
    assert reopened.open_exploratory("next").id == "episode-6"


def test_episode_store_loads_legacy_list_format(tmp_path: Path) -> None:
    store = _compacted_store(tmp_path)
    path = tmp_path / ".agent-memory" / "episodes.json"
    path.write_text(json.dumps([record.model_dump(mode="json") for record in store.list()]))
    reopened = FileEpisodeStore(tmp_path)
    assert len(reopened.load()) == 5
    assert reopened.open_exploratory("next").id == "episode-6"


def test_apply_retention_prunes_every_ledger(tmp_path: Path) -> None:
    telemetry = TelemetryStore(tmp_path)
    for run_id in ("r1", "r2", "r3"):
        _emit(telemetry, run_id, 2)
        time.sleep(0.01)
    telemetry.close()
    audit = AuditTranscriptStore(tmp_path)
    for run_id in ("r1", "r2", "r3"):
        audit.append(run_id, "step", {})
        time.sleep(0.01)
    audit.close()
    _compacted_store(tmp_path)

    report = apply_retention(
        tmp_path, RetentionPolicy(keep_latest_runs=1, keep_compacted_episodes=1)
    )
    assert sorted(report.telemetry_runs_removed) == ["r1", "r2"]
    assert sorted(report.audit_runs_removed) == ["r1", "r2"]
    # episode-4 is the most recently used and episode-1 has a live dependant: both stay.
    assert sorted(report.episodes_removed) == ["episode-2", "episode-3"]
    remaining = FileEpisodeStore(tmp_path)
    remaining.load()
    states = {record.id: record.state for record in remaining.list()}
    assert states == {
        "episode-1": EpisodeState.COMPACTED,
        "episode-4": EpisodeState.COMPACTED,
        "episode-5": EpisodeState.OPEN,
    }
    reopened = TelemetryStore(tmp_path)
    assert reopened.verify_run_chain("r3")
    reopened.close()


def test_run_report_counts_only_verified_events(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path)
    _emit(store, "run", 5)
    assert store.create_run_report("run")["verified_event_count"] == 5
    store.close()
    database = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    with sqlite3.connect(database) as connection:
        (raw,) = connection.execute(
            "SELECT event_json FROM events WHERE run_id = 'run' AND sequence = 3"
        ).fetchone()
        tampered = json.loads(raw)
        tampered["status"] = "tampered"
        connection.execute(
            "UPDATE events SET event_json = ? WHERE run_id = 'run' AND sequence = 3",
            (json.dumps(tampered),),
        )
    reopened = TelemetryStore(tmp_path)
    report = reopened.create_run_report("run")
    assert report["event_count"] == 5
    assert report["integrity_chain_valid"] is False
    assert report["verified_event_count"] == 2
    reopened.close()


def test_inspect_run_reads_past_one_thousand_events(tmp_path: Path) -> None:
    from nailong_agent_sdk.developer_tools.inspect import inspect_run

    store = TelemetryStore(tmp_path)
    _emit(store, "long", 1_205)
    store.close()
    assert inspect_run(tmp_path, "long").event_count == 1_205
