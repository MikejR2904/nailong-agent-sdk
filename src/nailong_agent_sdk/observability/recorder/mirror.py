# Copyright (c) 2026 David Michael Indraputra

"""Feeds each record a ledger persists to a recorder, and what happens when it cannot."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

from ...foundations.errors import AgentSdkError
from ...foundations.logging import get_logger
from ..audit_log import AuditLogEntry, AuditTranscriptStore
from ..telemetry_models import TelemetryEvent
from ..telemetry_store import TelemetryStore
from ..transparency import (
    MerkleTreeHead,
)
from .client import RecorderClient
from .protocol import recorder_error

_logger = get_logger("recorder")


@dataclass
class MirrorStats:
    sent: int = 0
    failed: int = 0
    last_error: str | None = None
    degraded: set[tuple[str, str]] = field(default_factory=set)


class RecorderMirror:
    """Copies each record to a recorder as the ledgers persist it."""

    def __init__(
        self,
        client: RecorderClient,
        *,
        required: bool = False,
        include_records: bool = True,
        asynchronous: bool = False,
        queue_size: int = 10_000,
    ) -> None:
        self._client = client
        self._required = required
        self._include = include_records
        self._asynchronous = asynchronous
        self.stats = MirrorStats()
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, str, int, str, str | None, str | None] | None] = (
            queue.Queue(maxsize=queue_size)
        )
        self._worker: threading.Thread | None = None
        if asynchronous:
            self._worker = threading.Thread(target=self._drain, daemon=True)
            self._worker.start()

    def attach(
        self, telemetry: TelemetryStore | None = None, audit: AuditTranscriptStore | None = None
    ) -> None:
        required = self._required and not self._asynchronous
        if telemetry is not None:
            telemetry.add_append_listener(self._on_event, required=required)
        if audit is not None:
            audit.add_append_listener(self._on_entry, required=required)

    def pending(self) -> int:
        return self._queue.qsize()

    def flush(self, timeout_seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while self._queue.qsize() and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._queue.qsize() == 0

    def backfill(
        self, telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
    ) -> dict[str, int]:
        self.flush()
        sent = {"telemetry": 0, "audit": 0}
        for event in telemetry.iter_events(run_id):
            self._client.append(
                run_id,
                "telemetry",
                event.sequence,
                event.integrity_hash,
                event.previous_event_hash,
                event.model_dump_json() if self._include else None,
            )
            sent["telemetry"] += 1
        for entry in audit.iter_entries(run_id):
            self._client.append(
                run_id,
                "audit",
                entry.sequence,
                entry.integrity_hash,
                entry.previous_hash,
                entry.model_dump_json() if self._include else None,
            )
            sent["audit"] += 1
        with self._lock:
            self.stats.degraded -= {(run_id, "telemetry"), (run_id, "audit")}
        return sent

    def finish(
        self, run_id: str, timeout_seconds: float = 10.0, final: bool = False
    ) -> dict[str, MerkleTreeHead]:
        self.flush(timeout_seconds)
        return self._client.seal(run_id, final)

    def close(self) -> None:
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=5)
        self._client.close()

    def _on_event(self, event: TelemetryEvent) -> None:
        self._send(
            event.context.run_id,
            "telemetry",
            event.sequence,
            event.integrity_hash,
            event.previous_event_hash,
            event.model_dump_json() if self._include else None,
        )

    def _on_entry(self, entry: AuditLogEntry) -> None:
        self._send(
            entry.run_id,
            "audit",
            entry.sequence,
            entry.integrity_hash,
            entry.previous_hash,
            entry.model_dump_json() if self._include else None,
        )

    def _send(
        self,
        run_id: str,
        store: str,
        sequence: int,
        record_hash: str,
        previous: str | None,
        record: str | None,
    ) -> None:
        item = (run_id, store, sequence, record_hash, previous, record)
        if self._asynchronous:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._note_failure(
                    store, run_id, recorder_error("RECORDER_QUEUE_FULL", "The send queue is full.")
                )
            return
        self._deliver(item)

    def _deliver(self, item: tuple[str, str, int, str, str | None, str | None]) -> None:
        run_id, store, sequence, record_hash, previous, record = item
        if (run_id, store) in self.stats.degraded:
            if self._required and not self._asynchronous:
                raise recorder_error(
                    "RECORDER_DEGRADED",
                    f'The {store} ledger of run "{run_id}" is no longer mirrored since an '
                    f"earlier failure ({self.stats.last_error}); a required recorder refuses "
                    "further records until backfill() has sent the missing ones.",
                    run_id=run_id,
                    store=store,
                )
            return
        try:
            self._client.append(run_id, store, sequence, record_hash, previous, record)
        except AgentSdkError as error:
            self._note_failure(store, run_id, error)
            if self._required and not self._asynchronous:
                raise
            return
        with self._lock:
            self.stats.sent += 1

    def _note_failure(self, store: str, run_id: str, error: AgentSdkError) -> None:
        with self._lock:
            self.stats.failed += 1
            self.stats.last_error = f"{error.code}: {error}"
            self.stats.degraded.add((run_id, store))
        _logger.warning(
            "The recorder did not accept a %s record of run %s; this run is no longer mirrored "
            "for that ledger: %s",
            store,
            run_id,
            error,
        )

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            self._deliver(item)
