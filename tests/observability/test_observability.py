import json
import os
import sqlite3
import threading
import time

import pytest

from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.metric_definitions import STANDARD_METRIC_DEFINITIONS
from nailong_agent_sdk.observability.metrics import (
    record_metric_unavailable,
    record_metric_value,
    register_standard_metric_definitions,
)
from nailong_agent_sdk.observability.profiler import (
    AgentRunProfiler,
    ProfileSpanKind,
    ProfileSpanStatus,
)
from nailong_agent_sdk.observability.telemetry_models import (
    MetricAvailability,
    MetricDefinition,
    MetricObservation,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.processes import run_workers

ACTOR = TelemetryActor(kind="agent", identifier="tester")


def emit(store, run_id="run-1", event_type="agent.test", payload=None, status="ok"):
    return store.emit(
        event_type,
        TelemetryContext(run_id=run_id),
        actor=ACTOR,
        authority=TelemetryAuthority.DETERMINISTIC,
        status=status,
        payload=payload or {},
    )


def test_telemetry_chain_sequence_and_persistence(tmp_path):
    store = TelemetryStore(tmp_path)
    events = [emit(store, payload={"i": i}) for i in range(25)]
    assert [e.sequence for e in events] == list(range(1, 26))
    assert (
        events[0].previous_event_hash is None
        and events[1].previous_event_hash == events[0].integrity_hash
    )
    assert store.verify_run_chain("run-1") and store.chain_break("run-1") is None
    store.close()
    reopened = TelemetryStore(tmp_path)
    follow_up = emit(reopened, payload={"i": 25})
    assert follow_up.sequence == 26 and follow_up.previous_event_hash == events[-1].integrity_hash
    assert reopened.verify_run_chain("run-1")
    assert len(list(reopened.iter_events("run-1", page_size=7))) == 26
    reopened.close()


def test_telemetry_thread_concurrency_keeps_chain_valid(tmp_path):
    store = TelemetryStore(tmp_path)
    errors = []

    def worker(tag):
        try:
            for i in range(60):
                emit(store, payload={"tag": tag, "i": i})
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    events = list(store.iter_events("run-1"))
    assert [e.sequence for e in events] == list(range(1, 481))
    assert store.verify_run_chain("run-1")
    store.close()


def test_telemetry_multiprocess_concurrency_keeps_chain_valid(tmp_path):
    script = """
    import sys
    from pathlib import Path
    from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
    from nailong_agent_sdk.observability.telemetry_models import (
        TelemetryActor,
        TelemetryAuthority,
        TelemetryContext,
    )
    store = TelemetryStore(Path(sys.argv[2]))
    actor = TelemetryActor(kind="a", identifier=sys.argv[1])
    for i in range(40):
        store.emit(
            "agent.mp",
            TelemetryContext(run_id="shared"),
            actor=actor,
            authority=TelemetryAuthority.DETERMINISTIC,
            status="ok",
            payload={"w": sys.argv[1], "i": i},
        )
    store.close()
    """
    outputs = run_workers(script, 4, str(tmp_path))
    assert all(code == 0 for code, _, _ in outputs), outputs
    store = TelemetryStore(tmp_path)
    events = list(store.iter_events("shared"))
    assert [e.sequence for e in events] == list(range(1, 161))
    assert store.verify_run_chain("shared")
    store.close()


def test_telemetry_redacts_secrets_at_the_persistence_boundary(tmp_path):
    store = TelemetryStore(tmp_path)
    event = emit(
        store,
        payload={"api_key": "sk-" + "A" * 30, "note": "token sk-" + "B" * 30, "input_tokens": 12},
    )
    stored = list(store.iter_events("run-1"))[0]
    blob = json.dumps(stored.model_dump(mode="json"))
    assert "sk-AAAA" not in blob and "sk-BBBB" not in blob
    assert stored.payload["input_tokens"] == 12 and event.integrity_hash == stored.integrity_hash
    store.close()


def test_telemetry_rejects_hidden_reasoning_keys(tmp_path):
    store = TelemetryStore(tmp_path)
    with pytest.raises(ValueError, match="hidden"):
        emit(store, payload={"scratchpad": "x"})
    store.close()


def test_telemetry_paging_bounds(tmp_path):
    store = TelemetryStore(tmp_path)
    for bad in (0, 1001):
        with pytest.raises(ValueError):
            store.list_events("run-1", limit=bad)
    with pytest.raises(ValueError):
        store.list_runs(limit=0)
    assert store.list_events("never-seen") == []
    assert store.verify_run_chain("never-seen") is True
    store.close()


def test_metrics_registry_recording_and_report(tmp_path):
    store = TelemetryStore(tmp_path)
    register_standard_metric_definitions(store)
    register_standard_metric_definitions(store)
    assert len(store.list_metric_definitions()) == len(
        {d.metric_id for d in STANDARD_METRIC_DEFINITIONS}
    )
    ctx = TelemetryContext(run_id="r")
    emit(store, run_id="r")
    record_metric_value(store, ctx, "agent.wall_duration_ms", 12.5, "milliseconds")
    record_metric_unavailable(store, ctx, "model.provider_input_tokens", "tokens", "not reported")
    metrics = store.list_metrics("r")
    assert {m.availability for m in metrics} == {
        MetricAvailability.AVAILABLE,
        MetricAvailability.UNAVAILABLE,
    }
    with pytest.raises(ValueError):
        store.record_metric(
            MetricObservation(
                metric_id="x",
                run_id="r",
                value=None,
                unit="u",
                availability=MetricAvailability.AVAILABLE,
                observed_at_utc="t",
            )
        )
    with pytest.raises(ValueError):
        store.record_metric(
            MetricObservation(
                metric_id="x",
                run_id="r",
                value=None,
                unit="u",
                availability=MetricAvailability.UNAVAILABLE,
                observed_at_utc="t",
            )
        )
    report = store.create_run_report("r")
    assert report["integrity_chain_valid"] is True and report["metric_availability"]
    assert (store.root / report["report_path"]).is_file()
    with pytest.raises(ValueError, match="unknown"):
        store.create_run_report("nope")
    store.close()


def test_standard_metric_definitions_are_unique_and_consistent():
    ids = [d.metric_id for d in STANDARD_METRIC_DEFINITIONS]
    assert len(ids) == len(set(ids))
    for definition in STANDARD_METRIC_DEFINITIONS:
        MetricDefinition.model_validate(definition.model_dump())


def test_telemetry_one_megabyte_payload_round_trips_quickly(tmp_path):
    store = TelemetryStore(tmp_path)
    blob = "plain text " * 100_000
    start = time.perf_counter()
    emit(store, payload={"blob": blob})
    elapsed = time.perf_counter() - start
    assert elapsed < 5
    assert list(store.iter_events("run-1"))[0].payload["blob"] == blob
    store.close()


def test_telemetry_tamper_detection_variants(tmp_path):
    store = TelemetryStore(tmp_path)
    for i in range(6):
        emit(store, payload={"i": i})
    store.close()
    db = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    connection = sqlite3.connect(db)
    row = connection.execute("SELECT event_json FROM events WHERE sequence = 3").fetchone()[0]
    changed = json.loads(row)
    changed["status"] = "tampered"
    connection.execute(
        "UPDATE events SET event_json = ? WHERE sequence = 3", (json.dumps(changed),)
    )
    connection.commit()
    connection.close()
    store = TelemetryStore(tmp_path)
    failure = store.chain_break("run-1")
    assert failure is not None and failure.sequence == 3 and failure.kind == "content-hash-mismatch"
    store.close()


def test_audit_chain_paging_render_and_verify(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    entries = [store.append("run-a", "evt", {"i": i}, task_id="t", iteration=i) for i in range(12)]
    assert [e.sequence for e in entries] == list(range(1, 13))
    assert store.verify("run-a") and store.snapshot_sequence("run-a") == 12
    assert len(store.list_entries("run-a", limit=5)) == 5
    with pytest.raises(ValueError):
        store.list_entries("run-a", limit=0)
    path = store.render_markdown("run-a")
    text = path.read_text(encoding="utf-8")
    assert "Integrity chain valid: `True`" in text and "Verified transcript entries: `12`" in text
    store.close()
    again = AuditTranscriptStore(tmp_path)
    assert again.append("run-a", "evt", {"i": 12}).sequence == 13 and again.verify("run-a")
    again.close()


def test_audit_multiprocess_append_is_serialized(tmp_path):
    script = """
    import sys
    from pathlib import Path
    from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
    store = AuditTranscriptStore(Path(sys.argv[2]))
    for i in range(30):
        store.append("shared", "evt", {"w": sys.argv[1], "i": i})
    store.close()
    """
    outputs = run_workers(script, 5, str(tmp_path))
    assert all(code == 0 for code, _, _ in outputs), outputs
    store = AuditTranscriptStore(tmp_path)
    entries = store.list_entries("shared", limit=1000)
    assert [e.sequence for e in entries] == list(range(1, 151)) and store.verify("shared")
    store.close()


def test_audit_more_runs_than_handle_cache_keeps_every_chain_valid(tmp_path):
    store = AuditTranscriptStore(tmp_path, max_open_handles=4)
    for round_ in range(3):
        for run in range(12):
            store.append(f"run-{run}", "evt", {"round": round_})
    assert all(
        store.verify(f"run-{run}") and store.snapshot_sequence(f"run-{run}") == 3
        for run in range(12)
    )
    store.close()


def test_audit_distinct_run_ids_never_share_a_transcript(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    ids = ["a/b", "a:b", "a b", "a_b"]
    for run_id in ids:
        store.append(run_id, "evt", {"who": run_id})
    for run_id in ids:
        entries = store.list_entries(run_id)
        assert [e.run_id for e in entries] == [run_id], (
            f"run id {run_id!r} shares a transcript: {[e.run_id for e in entries]}"
        )
    store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows reserved device names")
@pytest.mark.parametrize("run_id", ["nul", "con", "aux", "com1"])
def test_audit_reserved_windows_device_names_as_run_ids(tmp_path, run_id):
    store = AuditTranscriptStore(tmp_path)
    store.append(run_id, "evt", {"x": 1})
    assert store.snapshot_sequence(run_id) == 1 and store.verify(run_id)
    store.close()


def test_audit_entry_larger_than_tail_window_does_not_break_later_appends(tmp_path):
    store = AuditTranscriptStore(tmp_path, max_payload_chars=300_000)
    store.append("big", "evt", {"blob": "x" * 120_000})
    second = store.append("big", "evt", {"blob": "y"})
    assert second.sequence == 2 and store.verify("big")
    store.close()


def test_audit_hidden_reasoning_and_secrets(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    with pytest.raises(ValueError):
        store.append("r", "evt", {"scratchpad": "plan"})
    entry = store.append(
        "r", "evt", {"password": "hunter2hunter2", "text": "Bearer abcdefghijklmnopqrstuv"}
    )
    assert (
        entry.payload["password"] == "[REDACTED]"
        and "abcdefghijklmnopqr" not in entry.payload["text"]
    )
    store.close()


def test_audit_oversized_payload_is_bounded_with_hash(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    entry = store.append("r", "evt", {"blob": "z" * 50_000})
    assert entry.payload["truncated"] is True and entry.payload["original_chars"] > 50_000
    assert len(entry.payload["preview"]) == 8192
    store.close()


def test_audit_unicode_edge_cases_round_trip(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    for text in ["emoji 😀 ok", "nul\x00byte", "line\u2028sep", "zero​width", "ñandú"]:
        store.append("u", "evt", {"t": text})
    assert store.verify("u")
    assert [e.payload["t"] for e in store.list_entries("u")][0] == "emoji 😀 ok"
    store.close()


def test_audit_lone_surrogate_in_payload(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    try:
        store.append("s", "evt", {"t": "broken \ud83d pair"})
    except Exception as error:
        pytest.fail(f"lone surrogate in a payload raised {type(error).__name__}: {error}")
    assert store.verify("s")
    store.close()


def test_telemetry_lone_surrogate_in_payload(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        emit(store, payload={"t": "broken \ud83d pair"})
    except Exception as error:
        pytest.fail(f"lone surrogate in a telemetry payload raised {type(error).__name__}: {error}")
    assert store.verify_run_chain("run-1")
    store.close()


def test_profiler_lifecycle_summary_and_integrity(tmp_path):
    profiler = AgentRunProfiler()
    root = profiler.begin_run("r", "t", "agent")
    span = profiler.start_span(
        ProfileSpanKind.TOOL, "x", parent_span_id=root.span_id, attributes={"a": 1}
    )
    profiler.finish_span(span, ProfileSpanStatus.FAILED)
    dangling = profiler.start_span(ProfileSpanKind.MODEL_TURN, "m")
    profile = profiler.finish_run(ProfileSpanStatus.COMPLETED)
    assert profile.finished_at_utc and profile.integrity_hash
    kinds = {s.kind.value: s for s in profile.summaries}
    assert kinds["tool"].failed_count == 1 and kinds["model-turn"].cancelled_count == 1
    assert profiler.finish_run(ProfileSpanStatus.FAILED) == profile
    with pytest.raises(RuntimeError):
        profiler.start_span(ProfileSpanKind.TOOL, "late")
    with pytest.raises(RuntimeError, match="only one run"):
        profiler.begin_run("r2", "t", "a")
    path = profiler.write_json(tmp_path / "p" / "profile.json")
    assert json.loads(path.read_text(encoding="utf-8"))["integrity_hash"] == profile.integrity_hash
    hidden = AgentRunProfiler()
    hidden.begin_run("r", "t", "a")
    with pytest.raises(ValueError):
        hidden.start_span(ProfileSpanKind.TOOL, "x", attributes={"scratchpad": 1})
    assert dangling.span_id
