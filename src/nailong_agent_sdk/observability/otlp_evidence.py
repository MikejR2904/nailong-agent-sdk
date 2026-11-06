# Copyright (c) 2026 David Michael Indraputra

"""OTLP/HTTP JSON export of a run's audit transcript and integrity records as log records.

The telemetry exporter in ``otlp`` carries what the agent did. This module carries what proves it:
every hash-chained audit entry under the scope ``nailong.audit``, and under ``nailong.integrity``
each signed run-head checkpoint plus a verdict record that states whether the chain, the
checkpoints and the terminal link still hold. A collector stores the records; it does not verify
them, so each record carries the hashes and sequence numbers a reader needs to do that later.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..foundations.errors import AgentSdkError
from ..foundations.version import package_version
from .audit_log import AuditLogEntry, AuditTranscriptStore
from .otlp import (
    _ATTRIBUTE_PREFIX,
    _SEVERITY_NUMBERS,
    OtlpExportLedger,
    OtlpHttpExporter,
    RunCursor,
    _key_values,
    _unix_nano,
)
from .run_checkpoints import RunHeadCheckpoint, RunHeadReport, checkpoints_from_lines
from .sinks import CheckpointSink
from .telemetry_models import TelemetrySeverity
from .telemetry_store import TelemetryStore
from .terminal_link import TERMINAL_TELEMETRY_EVENT
from .trace_context import resource_attributes

AUDIT_SCOPE = "nailong.audit"
INTEGRITY_SCOPE = "nailong.integrity"
_PAYLOAD_CHARS = 8_192
_SIGNATURE_ID_CHARS = 24


class EvidenceCheckpointer(Protocol):
    """What the export needs from a run-head checkpointer: its sink and its verification."""

    sink: CheckpointSink

    def check(
        self, run_root: Path, telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
    ) -> RunHeadReport: ...


@dataclass(frozen=True)
class OtlpEvidencePayload:
    """The log request body for one run's audit and integrity records and what it carries."""

    run_id: str
    logs: dict[str, Any] | None
    audit_records: int
    checkpoint_records: int
    verdict_records: int
    audit_through: int
    checkpoint_ids: tuple[str, ...]
    verdict_key: str | None

    @property
    def records(self) -> int:
        return self.audit_records + self.checkpoint_records + self.verdict_records


@dataclass(frozen=True)
class OtlpEvidenceReport:
    runs: int
    requests: int
    audit_records: int
    checkpoint_records: int
    verdict_records: int


def checkpoint_id(checkpoint: RunHeadCheckpoint) -> str:
    return f"{checkpoint.created_at_utc}|{checkpoint.signature[:_SIGNATURE_ID_CHARS]}"


def _audit_record(entry: AuditLogEntry) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        f"{_ATTRIBUTE_PREFIX}audit.sequence": entry.sequence,
        f"{_ATTRIBUTE_PREFIX}audit.integrity_hash": entry.integrity_hash,
        f"{_ATTRIBUTE_PREFIX}audit.previous_hash": entry.previous_hash,
        f"{_ATTRIBUTE_PREFIX}audit.schema_version": entry.schema_version,
        f"{_ATTRIBUTE_PREFIX}run_id": entry.run_id,
        f"{_ATTRIBUTE_PREFIX}task_id": entry.task_id,
        f"{_ATTRIBUTE_PREFIX}iteration": entry.iteration,
    }
    if entry.payload:
        text = json.dumps(entry.payload, sort_keys=True, default=str)
        if len(text) > _PAYLOAD_CHARS:
            attributes[f"{_ATTRIBUTE_PREFIX}payload.truncated"] = True
            text = text[:_PAYLOAD_CHARS]
        attributes[f"{_ATTRIBUTE_PREFIX}payload"] = text
    moment = str(_unix_nano(entry.occurred_at_utc))
    return {
        "timeUnixNano": moment,
        "observedTimeUnixNano": moment,
        "severityNumber": _SEVERITY_NUMBERS[TelemetrySeverity.INFO],
        "severityText": "INFO",
        "body": {"stringValue": entry.event_type},
        "attributes": _key_values(attributes),
        "eventName": f"audit.{entry.event_type}",
    }


def _checkpoint_record(checkpoint: RunHeadCheckpoint) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        f"{_ATTRIBUTE_PREFIX}run_id": checkpoint.run_id,
        f"{_ATTRIBUTE_PREFIX}checkpoint.schema_version": checkpoint.schema_version,
        f"{_ATTRIBUTE_PREFIX}checkpoint.telemetry_sequence": checkpoint.telemetry_sequence,
        f"{_ATTRIBUTE_PREFIX}checkpoint.telemetry_head": checkpoint.telemetry_head,
        f"{_ATTRIBUTE_PREFIX}checkpoint.audit_sequence": checkpoint.audit_sequence,
        f"{_ATTRIBUTE_PREFIX}checkpoint.audit_head": checkpoint.audit_head,
        f"{_ATTRIBUTE_PREFIX}checkpoint.signer_id": checkpoint.signer_id,
        f"{_ATTRIBUTE_PREFIX}checkpoint.signature": checkpoint.signature,
        f"{_ATTRIBUTE_PREFIX}checkpoint.timestamped": checkpoint.timestamp is not None,
    }
    moment = str(_unix_nano(checkpoint.created_at_utc))
    return {
        "timeUnixNano": moment,
        "observedTimeUnixNano": moment,
        "severityNumber": _SEVERITY_NUMBERS[TelemetrySeverity.INFO],
        "severityText": "INFO",
        "body": {"stringValue": "run-head-checkpoint"},
        "attributes": _key_values(attributes),
        "eventName": "integrity.checkpoint",
    }


def _verdict_record(
    run_id: str, report: RunHeadReport, chain_intact: bool, chain_detail: str | None, at: str
) -> dict[str, Any]:
    verification = report.verification
    healthy = report.ok and chain_intact
    severity = TelemetrySeverity.INFO if healthy else TelemetrySeverity.ERROR
    attributes: dict[str, Any] = {
        f"{_ATTRIBUTE_PREFIX}run_id": run_id,
        f"{_ATTRIBUTE_PREFIX}integrity.ok": healthy,
        f"{_ATTRIBUTE_PREFIX}integrity.checkpoint_verdict": verification.verdict.value,
        f"{_ATTRIBUTE_PREFIX}integrity.checkpoint_detail": verification.detail,
        f"{_ATTRIBUTE_PREFIX}integrity.checkpoints": verification.checkpoints,
        f"{_ATTRIBUTE_PREFIX}integrity.audit_chain_intact": chain_intact,
        f"{_ATTRIBUTE_PREFIX}integrity.audit_chain_detail": chain_detail,
        f"{_ATTRIBUTE_PREFIX}integrity.current_audit_sequence": (
            verification.current_audit_sequence
        ),
        f"{_ATTRIBUTE_PREFIX}integrity.current_telemetry_sequence": (
            verification.current_telemetry_sequence
        ),
        f"{_ATTRIBUTE_PREFIX}integrity.terminal_link": report.terminal_link.state.value,
        f"{_ATTRIBUTE_PREFIX}integrity.terminal_link_detail": report.terminal_link.detail,
    }
    moment = str(_unix_nano(at))
    return {
        "timeUnixNano": moment,
        "observedTimeUnixNano": moment,
        "severityNumber": _SEVERITY_NUMBERS[severity],
        "severityText": severity.value.upper(),
        "body": {"stringValue": "integrity-verdict"},
        "attributes": _key_values(attributes),
        "eventName": "integrity.verdict",
    }


def _scope_logs(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"scope": {"name": name, "version": package_version()}, "logRecords": records}


def build_evidence_payload(
    audit: AuditTranscriptStore,
    run_id: str,
    *,
    checkpoints: Sequence[RunHeadCheckpoint],
    report: RunHeadReport | None,
    since: RunCursor | None = None,
    verdict_at: str | None = None,
    resource: list[dict[str, Any]] | None = None,
    audit_entries: bool = True,
) -> OtlpEvidencePayload:
    """Build the log request for the audit entries, checkpoints and verdict not yet sent."""

    after = since or RunCursor()
    entries = (
        [entry for entry in audit.iter_entries(run_id) if entry.sequence > after.audit]
        if audit_entries
        else []
    )
    fresh = [item for item in checkpoints if checkpoint_id(item) not in after.checkpoints]
    audit_records = [_audit_record(entry) for entry in entries]
    integrity: list[dict[str, Any]] = [_checkpoint_record(item) for item in fresh]
    verdict_key = None
    verdicts = 0
    if report is not None and (entries or fresh or after.verdict is None):
        chain = audit.chain_break(run_id)
        verdict_key = (
            f"{report.verification.verdict.value}@{report.verification.current_audit_sequence}"
            f"{'' if chain is None else '!chain'}"
        )
        if verdict_key != after.verdict:
            newest = entries[-1].occurred_at_utc if entries else None
            when = verdict_at or newest or datetime.now(UTC).isoformat()
            detail = None if chain is None else f"sequence {chain.sequence}: {chain.message}"
            integrity.append(_verdict_record(run_id, report, chain is None, detail, when))
            verdicts = 1
        else:
            verdict_key = None
    scopes = []
    if audit_records:
        scopes.append(_scope_logs(AUDIT_SCOPE, audit_records))
    if integrity:
        scopes.append(_scope_logs(INTEGRITY_SCOPE, integrity))
    logs = None
    if scopes:
        logs = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": resource
                        if resource is not None
                        else _key_values(resource_attributes())
                    },
                    "scopeLogs": scopes,
                }
            ]
        }
    return OtlpEvidencePayload(
        run_id=run_id,
        logs=logs,
        audit_records=len(audit_records),
        checkpoint_records=len(fresh),
        verdict_records=verdicts,
        audit_through=entries[-1].sequence if entries else 0,
        checkpoint_ids=tuple(checkpoint_id(item) for item in fresh),
        verdict_key=verdict_key,
    )


def export_evidence(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_ids: Sequence[str],
    exporter: OtlpHttpExporter,
    checkpointer: EvidenceCheckpointer | None,
    run_root: Path,
    *,
    ledger: OtlpExportLedger | None = None,
    audit_entries: bool = True,
    judge_missing_checkpoints: bool = True,
) -> OtlpEvidenceReport:
    """Send each run's audit entries, signed checkpoints and integrity verdict as log records.

    ``checkpointer`` is the run-head checkpointer that signed the heads; without one only the
    audit entries are sent, because there is nothing signed to report and no verdict to give.
    With a ``ledger`` only what is new since the last successful export is sent. With
    ``audit_entries`` false the audit transcript itself is left out and only the integrity
    records are sent. A finished run with no checkpoint yet gets a verdict only when
    ``judge_missing_checkpoints`` is true, so an export made while a run is still being
    checkpointed does not report a gap that is about to close.
    """

    sent = audit_total = checkpoint_total = verdict_total = 0
    for run_id in run_ids:
        cursor = ledger.cursor(exporter.base_url, run_id) if ledger is not None else RunCursor()
        records: list[RunHeadCheckpoint] = []
        if checkpointer is not None:
            lines = checkpointer.sink.read_all(run_id)
            records = checkpoints_from_lines(lines, checkpointer.sink.location(run_id))
        something_new = (audit_entries and audit.snapshot_sequence(run_id) > cursor.audit) or any(
            checkpoint_id(item) not in cursor.checkpoints for item in records
        )
        finished = bool(records) or (
            judge_missing_checkpoints
            and bool(telemetry.list_events(run_id, limit=1, event_types=[TERMINAL_TELEMETRY_EVENT]))
        )
        if not something_new and (
            checkpointer is None or not finished or cursor.verdict is not None
        ):
            continue
        report = None
        if checkpointer is not None and finished:
            report = checkpointer.check(run_root, telemetry, audit, run_id)
        built = build_evidence_payload(
            audit,
            run_id,
            checkpoints=records,
            report=report,
            since=cursor,
            audit_entries=audit_entries,
        )
        if built.logs is None:
            continue
        try:
            exporter.send("logs", built.logs)
        except AgentSdkError as error:
            raise AgentSdkError(
                error.code,
                f'Evidence export of run "{run_id}" stopped after {sent} request(s): '
                f"{error.message}",
                {**(error.details or {}), "run_id": run_id, "requests_sent": sent},
            ) from error
        sent += 1
        audit_total += built.audit_records
        checkpoint_total += built.checkpoint_records
        verdict_total += built.verdict_records
        if ledger is not None:
            ledger.advance(
                exporter.base_url,
                run_id,
                audit=built.audit_through,
                checkpoints=built.checkpoint_ids,
                verdict=built.verdict_key,
            )
    return OtlpEvidenceReport(len(run_ids), sent, audit_total, checkpoint_total, verdict_total)
