import json
import sqlite3
import weakref

from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

ACTOR = TelemetryActor(kind="system", identifier="test")


def emit(store, event_type="agent.test", status="ok", payload=None):
    return store.emit(
        event_type,
        TelemetryContext(run_id="run-1"),
        actor=ACTOR,
        authority=TelemetryAuthority.DETERMINISTIC,
        status=status,
        payload=payload or {},
    )


def rewrite_event(tmp_path, sequence, change):
    connection = sqlite3.connect(tmp_path / ".agent-telemetry" / "telemetry.sqlite3")
    row = connection.execute(
        "SELECT event_json FROM events WHERE sequence = ?", (sequence,)
    ).fetchone()[0]
    connection.execute(
        "UPDATE events SET event_json = ? WHERE sequence = ?", (change(row), sequence)
    )
    connection.commit()
    connection.close()


def test_a_run_report_holds_a_few_events_at_a_time_not_the_whole_run(tmp_path, monkeypatch):
    store = TelemetryStore(tmp_path)
    emitted = [emit(store, payload={"i": i}) for i in range(260)]
    monkeypatch.setitem(TelemetryStore.iter_events.__kwdefaults__, "page_size", 40)
    alive, peak = [], []
    real = TelemetryStore.iter_events

    def spying(self, *args, **kwargs):
        for event in real(self, *args, **kwargs):
            alive.append(weakref.ref(event))
            peak.append(sum(reference() is not None for reference in alive))
            yield event

    monkeypatch.setattr(TelemetryStore, "iter_events", spying)
    report = store.create_run_report("run-1")
    assert report["event_count"] == len(emitted) == len(alive)
    assert report["integrity_chain_valid"] is True
    assert max(peak) <= 3
    store.close()


def test_the_report_names_the_chain_head_and_lists_every_hash_only_on_request(tmp_path):
    store = TelemetryStore(tmp_path)
    events = [emit(store, status="ok" if i % 3 else "failed") for i in range(8)]
    plain = store.create_run_report("run-1")
    assert plain["schema_version"] == "run-report-v2"
    assert plain["evidence_head_hash"] == events[-1].integrity_hash
    assert "evidence_event_hashes" not in plain
    assert "evidence_event_hashes" not in json.loads(
        (store.root / plain["report_path"]).read_text("utf-8")
    )
    listed = store.create_run_report("run-1", include_event_hashes=True)
    assert listed["evidence_event_hashes"] == [event.integrity_hash for event in events]
    assert {key: value for key, value in listed.items() if key != "evidence_event_hashes"} == plain
    store.close()


def test_the_head_hash_of_a_broken_chain_is_the_last_event_that_still_verifies(tmp_path):
    store = TelemetryStore(tmp_path)
    events = [emit(store, payload={"i": i}) for i in range(6)]
    store.close()
    rewrite_event(tmp_path, 4, lambda row: json.dumps({**json.loads(row), "status": "tampered"}))
    reopened = TelemetryStore(tmp_path)
    broken = reopened.create_run_report("run-1", include_event_hashes=True)
    assert broken["integrity_failure"]["sequence"] == 4
    assert broken["evidence_head_hash"] == events[2].integrity_hash
    assert (broken["event_count"], broken["verified_event_count"]) == (6, 3)
    assert broken["statuses"] == {"ok": 5, "tampered": 1}
    assert len(broken["evidence_event_hashes"]) == 6
    reopened.close()


def test_an_event_that_cannot_be_read_is_reported_as_the_integrity_failure(tmp_path):
    store = TelemetryStore(tmp_path)
    events = [emit(store, payload={"i": i}) for i in range(5)]
    store.close()
    rewrite_event(tmp_path, 3, lambda row: row[: len(row) // 2])
    reopened = TelemetryStore(tmp_path)
    report = reopened.create_run_report("run-1")
    assert report["integrity_chain_valid"] is False
    assert report["integrity_failure"]["kind"] == "unreadable-entry"
    assert report["integrity_failure"]["sequence"] == 3
    assert (report["event_count"], report["verified_event_count"]) == (2, 2)
    assert report["verified_through_sequence"] == 2
    assert report["evidence_head_hash"] == events[1].integrity_hash
    reopened.close()
