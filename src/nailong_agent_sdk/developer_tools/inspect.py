# Copyright (c) 2026 David Michael Indraputra

"""Read-only integrity and observability inspection for a durable SDK run root."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..foundations.contracts import StrictModel
from ..observability.audit_log import AuditTranscriptStore
from ..observability.telemetry_store import TelemetryStore


class RunInspection(StrictModel):
    """Read-only summary of the durable evidence produced for one run."""

    schema_version: str = "agent-sdk-run-inspection-v1"
    run_id: str
    telemetry_chain_valid: bool
    audit_chain_valid: bool
    event_count: int
    metric_count: int
    event_types: dict[str, int]
    statuses: dict[str, int]
    metric_availability: dict[str, int]
    audit_entry_count: int
    report: dict[str, Any]


def inspect_run(run_root: Path, run_id: str) -> RunInspection:
    """Verify and summarize one run without creating any reports or executing tools."""

    telemetry = TelemetryStore(run_root)
    audit = AuditTranscriptStore(run_root)
    events = telemetry.list_events(run_id, limit=1_000)
    metrics = telemetry.list_metrics(run_id)
    entries = audit.list_entries(run_id, limit=10_000)
    if not events:
        raise ValueError(f'Telemetry run "{run_id}" is unknown.')

    event_types = _counts(event.event_type for event in events)
    statuses = _counts(event.status for event in events)
    metric_availability = _counts(metric.availability.value for metric in metrics)
    telemetry_chain_valid = telemetry.verify_run_chain(run_id)
    audit_chain_valid = audit.verify(run_id)
    report: dict[str, Any] = {
        "schema_version": "agent-sdk-read-only-run-inspection-v1",
        "run_id": run_id,
        "event_count": len(events),
        "metric_count": len(metrics),
        "telemetry_chain_valid": telemetry_chain_valid,
        "audit_chain_valid": audit_chain_valid,
        "event_types": event_types,
        "statuses": statuses,
        "metric_availability": metric_availability,
        "evidence_event_hashes": [event.integrity_hash for event in events],
    }
    return RunInspection(
        run_id=run_id,
        telemetry_chain_valid=telemetry_chain_valid,
        audit_chain_valid=audit_chain_valid,
        event_count=len(events),
        metric_count=len(metrics),
        event_types=event_types,
        statuses=statuses,
        metric_availability=metric_availability,
        audit_entry_count=len(entries),
        report=report,
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
