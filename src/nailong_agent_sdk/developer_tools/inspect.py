# Copyright (c) 2026 David Michael Indraputra

"""Read-only integrity and observability inspection for a durable SDK run root."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..agent.retention import TombstonedHandle, tombstone_for_run, tombstoned_handles
from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..memory.context_projection import journal_content_hash
from ..observability.audit_log import AuditTranscriptStore
from ..observability.telemetry_store import TelemetryStore
from ..state.project_state_store import FileProjectStateStore

TOOL_RESULT_EVIDENCE_KIND = "tool-result-handle"


class RunInspection(StrictModel):
    """Read-only summary of the durable evidence produced for one run."""

    schema_version: str = "agent-sdk-run-inspection-v1"
    run_id: str
    telemetry_chain_valid: bool
    audit_chain_valid: bool
    telemetry_chain_failure: dict[str, Any] | None = None
    audit_chain_failure: dict[str, Any] | None = None
    event_count: int
    metric_count: int
    event_types: dict[str, int]
    statuses: dict[str, int]
    metric_availability: dict[str, int]
    audit_entry_count: int
    report: dict[str, Any]


class EvidenceMismatch(StrictModel):
    evidence_id: str
    revision: int
    recorded_hash: str | None
    journal_hash: str | None
    reason: str


class EvidenceVerification(StrictModel):
    schema_version: str = "agent-sdk-evidence-verification-v1"
    project_id: str
    checked: int
    verified: bool
    mismatches: list[EvidenceMismatch]
    pruned: int = 0


def verify_project_evidence(run_root: Path, project_id: str) -> EvidenceVerification:
    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    events = FileProjectStateStore(run_root, read_only=True).events(project_id)
    journal_root = run_root.resolve() / ".agent-tool-results"
    mismatches: list[EvidenceMismatch] = []
    seen: set[tuple[str, str | None]] = set()
    pruned = 0
    tombstoned: dict[str, TombstonedHandle] | None = None
    tombstone_failure: str | None = None
    for event in events:
        for evidence in event.evidence:
            key = (evidence.evidence_id, evidence.content_hash)
            if evidence.kind != TOOL_RESULT_EVIDENCE_KIND or key in seen:
                continue
            seen.add(key)
            try:
                actual = journal_content_hash(journal_root, evidence.evidence_id)
            except ValueError as error:
                if tombstoned is None:
                    try:
                        tombstoned = tombstoned_handles(run_root)
                    except AgentSdkError as failure:
                        tombstoned, tombstone_failure = {}, str(failure)
                record = tombstoned.get(evidence.evidence_id)
                if record is not None and record.content_hash == evidence.content_hash:
                    pruned += 1
                    continue
                if record is not None:
                    reason = (
                        f'Evidence "{evidence.evidence_id}" of project "{project_id}" at '
                        f"revision {event.revision} records content hash {evidence.content_hash}, "
                        f"but retention tombstone {record.tombstone_sequence} for run "
                        f'"{record.run_id}" recorded the pruned journal file with hash '
                        f"{record.content_hash}."
                    )
                elif tombstone_failure is not None:
                    reason = (
                        f"{error} The retention tombstones cannot vouch for it: {tombstone_failure}"
                    )
                else:
                    reason = str(error)
                mismatches.append(
                    EvidenceMismatch(
                        evidence_id=evidence.evidence_id,
                        revision=event.revision,
                        recorded_hash=evidence.content_hash,
                        journal_hash=None,
                        reason=reason,
                    )
                )
                continue
            if actual != evidence.content_hash:
                mismatches.append(
                    EvidenceMismatch(
                        evidence_id=evidence.evidence_id,
                        revision=event.revision,
                        recorded_hash=evidence.content_hash,
                        journal_hash=actual,
                        reason=(
                            f'Evidence "{evidence.evidence_id}" of project "{project_id}" at '
                            f"revision {event.revision} records content hash "
                            f"{evidence.content_hash}, but its journal file hashes to {actual}."
                        ),
                    )
                )
    return EvidenceVerification(
        project_id=project_id,
        checked=len(seen),
        verified=not mismatches,
        mismatches=mismatches,
        pruned=pruned,
    )


def inspect_run(run_root: Path, run_id: str) -> RunInspection:
    """Verify and summarize one run without creating any reports or executing tools."""

    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    database = run_root.resolve() / ".agent-telemetry" / "telemetry.sqlite3"
    if not database.is_file():
        raise ValueError(f'Run root "{run_root}" has no telemetry database at "{database}".')
    telemetry = TelemetryStore(run_root, read_only=True)
    try:
        audit = AuditTranscriptStore(run_root, read_only=True)
        event_types: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        event_hashes: list[str] = []
        for event in telemetry.iter_events(run_id):
            event_types[event.event_type] += 1
            statuses[event.status] += 1
            event_hashes.append(event.integrity_hash)
        if not event_hashes:
            raise ValueError(_unknown_run_message(run_root, run_id))
        metrics = telemetry.list_metrics(run_id)
        metric_availability = _counts(metric.availability.value for metric in metrics)
        telemetry_failure = telemetry.chain_break(run_id)
        audit_failure = audit.chain_break(run_id)
        audit_entry_count = audit.entry_count(run_id)
    finally:
        telemetry.close()
    telemetry_chain_valid = telemetry_failure is None
    audit_chain_valid = audit_failure is None
    report: dict[str, Any] = {
        "schema_version": "agent-sdk-read-only-run-inspection-v1",
        "run_id": run_id,
        "event_count": len(event_hashes),
        "metric_count": len(metrics),
        "telemetry_chain_valid": telemetry_chain_valid,
        "audit_chain_valid": audit_chain_valid,
        "event_types": _sorted(event_types),
        "statuses": _sorted(statuses),
        "metric_availability": metric_availability,
        "evidence_event_hashes": event_hashes,
    }
    return RunInspection(
        run_id=run_id,
        telemetry_chain_valid=telemetry_chain_valid,
        audit_chain_valid=audit_chain_valid,
        telemetry_chain_failure=telemetry_failure.model_dump(mode="json")
        if telemetry_failure
        else None,
        audit_chain_failure=audit_failure.model_dump(mode="json") if audit_failure else None,
        event_count=len(event_hashes),
        metric_count=len(metrics),
        event_types=_sorted(event_types),
        statuses=_sorted(statuses),
        metric_availability=metric_availability,
        audit_entry_count=audit_entry_count,
        report=report,
    )


def _unknown_run_message(run_root: Path, run_id: str) -> str:
    try:
        tombstone = tombstone_for_run(run_root, run_id)
    except AgentSdkError:
        tombstone = None
    if tombstone is None:
        return f'Telemetry run "{run_id}" is unknown.'
    return (
        f'Telemetry run "{run_id}" was pruned at {tombstone.pruned_at_utc} by retention '
        f"tombstone {tombstone.sequence}; it held {tombstone.telemetry.event_count} events "
        f"ending at hash {tombstone.telemetry.head_hash}."
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
    return _sorted(Counter(values))


def _sorted(counts: Counter[str]) -> dict[str, int]:
    return dict(sorted(counts.items()))
