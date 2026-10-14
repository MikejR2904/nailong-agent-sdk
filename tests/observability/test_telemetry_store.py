import json
import sqlite3
import threading
import time

import pytest

from nailong_agent_sdk.observability import telemetry_store
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.observability_cases import emit
from tests.support.processes import run_workers


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


class LockedWhileSwitchingMode:
    def __init__(self, connection, failures, message="database is locked"):
        self.connection = connection
        self.failures = failures
        self.message = message
        self.attempts = 0

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA journal_mode"):
            self.attempts += 1
            if self.failures > 0:
                self.failures -= 1
                raise sqlite3.OperationalError(self.message)
        return self.connection.execute(sql, *args)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *exception):
        return self.connection.__exit__(*exception)

    def __getattr__(self, name):
        return getattr(self.connection, name)


def test_a_database_locked_by_another_process_is_retried_while_the_store_opens(
    tmp_path, monkeypatch
):
    real_connect = sqlite3.connect
    wrapped = []

    def connect(*args, **kwargs):
        wrapped.append(LockedWhileSwitchingMode(real_connect(*args, **kwargs), failures=3))
        return wrapped[-1]

    monkeypatch.setattr(sqlite3, "connect", connect)
    store = TelemetryStore(tmp_path)
    try:
        assert wrapped[0].attempts == 4
        assert emit(store).sequence == 1
    finally:
        store.close()


def test_a_database_that_stays_locked_fails_with_the_sqlite_message(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    monkeypatch.setattr(telemetry_store, "_LOCK_RETRY_SECONDS", 0.1)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *a, **k: LockedWhileSwitchingMode(real_connect(*a, **k), failures=10**9),
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        TelemetryStore(tmp_path)


def test_an_error_that_is_not_a_lock_is_not_retried(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    wrapped = []

    def connect(*args, **kwargs):
        wrapped.append(
            LockedWhileSwitchingMode(real_connect(*args, **kwargs), 5, "unable to open database")
        )
        return wrapped[-1]

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        TelemetryStore(tmp_path)
    assert wrapped[0].attempts == 1


def test_stores_opened_by_many_processes_at_once_on_a_new_root_all_start(tmp_path):
    script = """
    import sys
    import time
    from pathlib import Path
    from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
    while time.time() < float(sys.argv[3]):
        time.sleep(0.001)
    store = TelemetryStore(Path(sys.argv[2]))
    store.close()
    """
    outputs = run_workers(script, 8, str(tmp_path), str(time.time() + 3))
    assert all(code == 0 for code, _, _ in outputs), outputs
    reopened = TelemetryStore(tmp_path)
    reopened.close()


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


def test_the_run_report_counts_only_the_events_before_a_chain_break_as_verified(tmp_path):
    store = TelemetryStore(tmp_path)
    for i in range(6):
        emit(store, payload={"i": i})
    intact = store.create_run_report("run-1")
    assert (intact["event_count"], intact["verified_event_count"]) == (6, 6)
    assert intact["integrity_chain_valid"] is True
    store.close()
    connection = sqlite3.connect(tmp_path / ".agent-telemetry" / "telemetry.sqlite3")
    row = connection.execute("SELECT event_json FROM events WHERE sequence = 4").fetchone()[0]
    changed = json.loads(row)
    changed["status"] = "tampered"
    connection.execute(
        "UPDATE events SET event_json = ? WHERE sequence = 4", (json.dumps(changed),)
    )
    connection.commit()
    connection.close()
    reopened = TelemetryStore(tmp_path)
    broken = reopened.create_run_report("run-1")
    assert broken["integrity_chain_valid"] is False
    assert broken["integrity_failure"]["sequence"] == 4
    assert (broken["event_count"], broken["verified_event_count"]) == (6, 3)
    assert (intact["verified_through_sequence"], broken["verified_through_sequence"]) == (6, 3)
    reopened.close()


def test_telemetry_lone_surrogate_in_payload(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        emit(store, payload={"t": "broken \ud83d pair"})
    except Exception as error:
        pytest.fail(f"lone surrogate in a telemetry payload raised {type(error).__name__}: {error}")
    assert store.verify_run_chain("run-1")
    store.close()
