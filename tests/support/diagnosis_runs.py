import hashlib
import json
import sqlite3

from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import agent, arun, task

SQLITE_SIDE_FILES = {
    ".agent-telemetry/telemetry.sqlite3-wal",
    ".agent-telemetry/telemetry.sqlite3-shm",
}


def record(root, run_id, turns, **kwargs):
    telemetry = TelemetryStore(root)
    audit = AuditTranscriptStore(root)
    try:
        subject, _ = agent(turns, telemetry=telemetry, audit_logs=audit, **kwargs)
        return arun(subject.run(task(run_id)))
    finally:
        telemetry.close()
        audit.close()


def tamper_event(root, run_id, sequence):
    connection = sqlite3.connect(root / ".agent-telemetry" / "telemetry.sqlite3")
    try:
        row = connection.execute(
            "SELECT event_json FROM events WHERE run_id=? AND sequence=?", (run_id, sequence)
        ).fetchone()
        payload = json.loads(row[0])
        payload["status"] = "tampered"
        connection.execute(
            "UPDATE events SET event_json=? WHERE run_id=? AND sequence=?",
            (json.dumps(payload), run_id, sequence),
        )
        connection.commit()
    finally:
        connection.close()


def fingerprints(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
