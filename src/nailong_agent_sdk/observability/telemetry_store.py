# Copyright (c) 2026 David Michael Indraputra

"""Durable telemetry ledger: an append-only SQLite store with an integrity hash chain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import Any

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.errors import redact_secrets
from ..foundations.identifiers import file_safe_name
from .telemetry_helpers import first_chain_break
from .telemetry_models import (
    ChainBreak,
    MetricAvailability,
    MetricDefinition,
    MetricObservation,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetryEvent,
    TelemetryRunSummary,
    TelemetrySeverity,
)

_EVENT_TIME = "json_extract(event_json, '$.occurred_at_utc')"
_EVENT_STATUS = "json_extract(event_json, '$.status')"
_EVENT_TYPE = "json_extract(event_json, '$.event_type')"


class TelemetryStore:
    """Append-only SQLite ledger with an integrity hash chain and metric observations."""

    def __init__(self, root: Path, *, read_only: bool = False) -> None:
        self._root = root.resolve() / ".agent-telemetry"
        self._database_path = self._root / "telemetry.sqlite3"
        self._lock = threading.RLock()
        if read_only:
            if not self._database_path.is_file():
                raise FileNotFoundError(
                    f'Telemetry database "{self._database_path}" does not exist.'
                )
            self._connection = sqlite3.connect(
                f"{self._database_path.as_uri()}?mode=ro",
                uri=True,
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            )
            return
        self._root.mkdir(parents=True, exist_ok=True)
        # One connection is shared only while `_lock` is held. `check_same_thread=False`
        # permits that serialized use; it does not make unprotected access safe.
        self._connection = sqlite3.connect(
            self._database_path, timeout=10, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def close(self) -> None:
        """Close the persistent connection; repeated calls are safe.

        Call this before deterministic removal or renaming of the telemetry root
        on platforms that retain open database handles.
        """

        with self._lock:
            self._connection.close()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def database_path(self) -> Path:
        return self._database_path

    def emit(
        self,
        event_type: str,
        context: TelemetryContext,
        *,
        actor: TelemetryActor,
        authority: TelemetryAuthority,
        status: str,
        severity: TelemetrySeverity = TelemetrySeverity.INFO,
        links: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TelemetryEvent:
        event = TelemetryEvent(
            event_type=event_type,
            occurred_at_utc=datetime.now(UTC).isoformat(),
            occurred_at_monotonic_ns=monotonic_ns(),
            context=context,
            actor=actor,
            authority=authority,
            status=status,
            severity=severity,
            links=links or {},
            payload=payload or {},
        )
        return self.append(event)

    def append(self, event: TelemetryEvent) -> TelemetryEvent:
        """Append one event atomically after assigning its sequence and hash-chain predecessor.

        ``payload``/``links`` are redacted here, at the actual persistence
        boundary, rather than only in ``emit`` — this is the point every event
        passes through no matter how it was constructed.
        """

        with self._lock, self._connection as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT sequence, integrity_hash FROM events "
                "WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
                (event.context.run_id,),
            ).fetchone()
            sequence = (int(previous[0]) + 1) if previous else 1
            predecessor = str(previous[1]) if previous else None
            prepared = event.model_copy(
                update={
                    "sequence": sequence,
                    "previous_event_hash": predecessor,
                    "integrity_hash": "",
                    "payload": redact_secrets(event.payload),
                    "links": redact_secrets(event.links),
                }
            )
            digest = _canonical_hash(prepared.model_dump(mode="json"))
            prepared = prepared.model_copy(update={"integrity_hash": digest})
            connection.execute(
                """
                INSERT INTO events (
                    event_id, run_id, sequence, occurred_at_utc, event_type, status, severity,
                    integrity_hash, previous_event_hash, payload_json, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prepared.event_id,
                    prepared.context.run_id,
                    prepared.sequence,
                    prepared.occurred_at_utc,
                    prepared.event_type,
                    prepared.status,
                    prepared.severity.value,
                    prepared.integrity_hash,
                    prepared.previous_event_hash,
                    _canonical_json(prepared.payload),
                    prepared.model_dump_json(),
                ),
            )
            connection.commit()
            return prepared

    def record_metric(self, observation: MetricObservation) -> MetricObservation:
        if observation.availability is MetricAvailability.AVAILABLE and observation.value is None:
            raise ValueError("Available metric observations require a numeric value.")
        if (
            observation.availability is MetricAvailability.UNAVAILABLE
            and not observation.unavailable_reason
        ):
            raise ValueError("Unavailable metric observations require an unavailable_reason.")
        with self._lock, self._connection as connection:
            connection.execute(
                """
                INSERT INTO metric_observations (
                    observation_id, metric_id, run_id, value, unit, availability,
                    unavailable_reason, source_event_id, source_artifact_id, parser_version,
                    context_json, observed_at_utc, observation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.metric_id,
                    observation.run_id,
                    observation.value,
                    observation.unit,
                    observation.availability.value,
                    observation.unavailable_reason,
                    observation.source_event_id,
                    observation.source_artifact_id,
                    observation.parser_version,
                    _canonical_json(observation.context),
                    observation.observed_at_utc,
                    observation.model_dump_json(),
                ),
            )
            connection.commit()
        return observation

    def register_metric_definition(self, definition: MetricDefinition) -> MetricDefinition:
        with self._lock, self._connection as connection:
            connection.execute(
                """
                INSERT INTO metric_definitions (metric_id, definition_json)
                VALUES (?, ?)
                ON CONFLICT(metric_id) DO UPDATE SET definition_json = excluded.definition_json
                """,
                (definition.metric_id, definition.model_dump_json()),
            )
            connection.commit()
        return definition

    def list_metric_definitions(self) -> list[MetricDefinition]:
        with self._lock, self._connection as connection:
            rows = connection.execute(
                "SELECT definition_json FROM metric_definitions ORDER BY metric_id ASC"
            ).fetchall()
        return [MetricDefinition.model_validate_json(str(row[0])) for row in rows]

    def list_events(
        self,
        run_id: str,
        *,
        limit: int = 250,
        after_sequence: int = 0,
        through_sequence: int | None = None,
    ) -> list[TelemetryEvent]:
        if limit < 1 or limit > 1_000:
            raise ValueError("Telemetry event page size must be between 1 and 1000.")
        if through_sequence is not None and through_sequence < after_sequence:
            return []
        with self._lock, self._connection as connection:
            rows = connection.execute(
                """
                SELECT event_json FROM events
                WHERE run_id = ? AND sequence > ? AND (? IS NULL OR sequence <= ?)
                ORDER BY sequence ASC LIMIT ?
                """,
                (run_id, after_sequence, through_sequence, through_sequence, limit),
            ).fetchall()
        return [TelemetryEvent.model_validate_json(str(row[0])) for row in rows]

    def run_snapshot_sequence(self, run_id: str) -> int:
        """Return the highest persisted sequence for an explicit verification boundary."""

        with self._lock, self._connection as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row[0])

    def iter_events(
        self,
        run_id: str,
        *,
        page_size: int = 1_000,
        through_sequence: int | None = None,
        after_sequence: int = 0,
    ) -> Iterator[TelemetryEvent]:
        """Stream a complete ordered run sequence through one fixed sequence boundary."""

        boundary = (
            self.run_snapshot_sequence(run_id) if through_sequence is None else through_sequence
        )
        while page := self.list_events(
            run_id,
            limit=page_size,
            after_sequence=after_sequence,
            through_sequence=boundary,
        ):
            yield from page
            after_sequence = page[-1].sequence

    def list_metrics(self, run_id: str) -> list[MetricObservation]:
        with self._lock, self._connection as connection:
            rows = connection.execute(
                "SELECT observation_json FROM metric_observations "
                "WHERE run_id = ? ORDER BY observed_at_utc ASC",
                (run_id,),
            ).fetchall()
        return [MetricObservation.model_validate_json(str(row[0])) for row in rows]

    def list_runs(self, *, limit: int = 100) -> list[TelemetryRunSummary]:
        if limit < 1 or limit > 1_000:
            raise ValueError("Telemetry run page size must be between 1 and 1000.")
        with self._lock, self._connection as connection:
            rows = connection.execute(
                f"""
                SELECT run_id, COUNT(*), MIN({_EVENT_TIME}), MAX({_EVENT_TIME})
                FROM events GROUP BY run_id ORDER BY MAX({_EVENT_TIME}) DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            summaries: list[TelemetryRunSummary] = []
            for run_id, count, started, last in rows:
                status_rows = connection.execute(
                    f"SELECT {_EVENT_STATUS}, COUNT(*) FROM events WHERE run_id = ? GROUP BY 1",
                    (run_id,),
                ).fetchall()
                type_rows = connection.execute(
                    f"SELECT {_EVENT_TYPE}, COUNT(*) FROM events WHERE run_id = ? GROUP BY 1",
                    (run_id,),
                ).fetchall()
                summaries.append(
                    TelemetryRunSummary(
                        run_id=str(run_id),
                        event_count=int(count),
                        started_at=str(started),
                        last_event_at=str(last),
                        statuses={str(key): int(value) for key, value in status_rows},
                        event_types={str(key): int(value) for key, value in type_rows},
                    )
                )
        return summaries

    def verify_run_chain(self, run_id: str) -> bool:
        return self.chain_break(run_id) is None

    def chain_break(self, run_id: str) -> ChainBreak | None:
        boundary = self.run_snapshot_sequence(run_id)
        return _events_chain_break(self.iter_events(run_id, through_sequence=boundary))

    @staticmethod
    def _verify_events(events: Iterable[TelemetryEvent]) -> bool:
        return _events_chain_break(events) is None

    def create_run_report(self, run_id: str) -> dict[str, Any]:
        boundary = self.run_snapshot_sequence(run_id)
        events = list(self.iter_events(run_id, through_sequence=boundary))
        metrics = self.list_metrics(run_id)
        if not events:
            raise ValueError(f'Telemetry run "{run_id}" is unknown.')
        failure = _events_chain_break(events)
        report = {
            "schema_version": "run-report-v1",
            "run_id": run_id,
            "event_count": len(events),
            "verified_event_count": len(events),
            "verified_through_sequence": boundary,
            "integrity_chain_valid": failure is None,
            "integrity_failure": failure.model_dump(mode="json") if failure else None,
            "statuses": _count(event.status for event in events),
            "event_types": _count(event.event_type for event in events),
            "watchdog_interventions": sum(
                event.event_type.startswith("watchdog.") for event in events
            ),
            "metrics": [metric.model_dump(mode="json") for metric in metrics],
            "metric_availability": _count(metric.availability.value for metric in metrics),
            "metric_summary": _summarize_metrics(metrics, self.list_metric_definitions()),
            "evidence_event_hashes": [event.integrity_hash for event in events],
        }
        reports = self._root / "reports"
        reports.mkdir(exist_ok=True)
        target = reports / f"{file_safe_name(run_id)}.run-report.json"
        _atomic_write(target, json.dumps(report, indent=2, sort_keys=True).encode("utf-8"))
        return {**report, "report_path": str(target.relative_to(self._root))}

    def _initialize(self) -> None:
        with self._lock, self._connection as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    occurred_at_utc TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    integrity_hash TEXT NOT NULL,
                    previous_event_hash TEXT,
                    payload_json TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS events_run_sequence ON events(run_id, sequence);
                CREATE INDEX IF NOT EXISTS events_type ON events(event_type);
                CREATE TABLE IF NOT EXISTS metric_observations (
                    observation_id TEXT PRIMARY KEY,
                    metric_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    value REAL,
                    unit TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    unavailable_reason TEXT,
                    source_event_id TEXT,
                    source_artifact_id TEXT,
                    parser_version TEXT,
                    context_json TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    observation_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS metrics_run
                    ON metric_observations(run_id, observed_at_utc);
                CREATE TABLE IF NOT EXISTS metric_definitions (
                    metric_id TEXT PRIMARY KEY,
                    definition_json TEXT NOT NULL
                );
                """
            )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _events_chain_break(events: Iterable[TelemetryEvent]) -> ChainBreak | None:
    return first_chain_break(
        events,
        noun="event",
        previous_attribute="previous_event_hash",
        expected_hash=lambda event: _canonical_hash(
            event.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
        ),
    )


def _count(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _summarize_metrics(
    observations: Iterable[MetricObservation], definitions: Iterable[MetricDefinition]
) -> dict[str, dict[str, Any]]:
    """Aggregate values by their registered formula without turning missing values into zero."""

    by_definition = {definition.metric_id: definition for definition in definitions}
    grouped: dict[str, list[MetricObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.metric_id, []).append(observation)
    summary: dict[str, dict[str, Any]] = {}
    for metric_id, items in sorted(grouped.items()):
        available = [
            item.value for item in items if item.availability is MetricAvailability.AVAILABLE
        ]
        values = [value for value in available if value is not None]
        definition = by_definition.get(metric_id)
        aggregation = definition.aggregation if definition is not None else "unregistered"
        aggregate: float | None
        if not values:
            aggregate = None
        elif aggregation == "sum":
            aggregate = sum(values)
        elif aggregation == "mean":
            aggregate = sum(values) / len(values)
        elif aggregation == "min":
            aggregate = min(values)
        elif aggregation == "max":
            aggregate = max(values)
        elif aggregation == "last":
            aggregate = values[-1]
        else:
            aggregate = None
        summary[metric_id] = {
            "aggregation": aggregation,
            "value": aggregate,
            "available_observation_count": len(values),
            "unavailable_observation_count": len(items) - len(values),
            "unit": items[-1].unit,
            "missing_data_rule": definition.missing_data_rule
            if definition
            else "No definition registered.",
        }
    return summary


def _atomic_write(target: Path, content: bytes) -> None:
    temporary = unique_temporary_path(target)
    temporary.write_bytes(content)
    replace_atomic(temporary, target)
