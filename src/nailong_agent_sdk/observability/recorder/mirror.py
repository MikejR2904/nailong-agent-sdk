# Copyright (c) 2026 David Michael Indraputra

"""Feeds each record a ledger persists to a recorder, and what happens when it cannot."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

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
        outbox: Path | None = None,
    ) -> None:
        self._client = client
        self._required = required
        self._include = include_records
        self._asynchronous = asynchronous
        self.stats = MirrorStats()
        self._lock = threading.Lock()
        self._outbox = Path(outbox) if outbox is not None else None
        self._outbox_ids = 0
        if self._outbox is not None:
            self._outbox.parent.mkdir(parents=True, exist_ok=True)
            self._outbox_ids = max((entry[0] for entry in self._outbox_entries()), default=0)
        self._queue: queue.Queue[tuple | None] = queue.Queue(maxsize=queue_size)
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
        return self._queue.unfinished_tasks

    def flush(self, timeout_seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0

    def health(self) -> dict[str, object]:
        with self._lock:
            degraded = sorted(f"{run}:{store}" for run, store in self.stats.degraded)
            sent, failed, error = self.stats.sent, self.stats.failed, self.stats.last_error
        waiting = self.unacknowledged() if self._outbox is not None else None
        state = "degraded" if degraded else "behind" if (waiting or self.pending()) else "ok"
        return {
            "state": state,
            "sent": sent,
            "failed": failed,
            "queued": self.pending(),
            "unacknowledged": waiting,
            "degraded": degraded,
            "last_error": error,
            "durable": self._outbox is not None,
        }

    def unacknowledged(self) -> int:
        return len(self._unacknowledged())

    def recover(self) -> dict[str, int]:
        sent = failed = 0
        for ident, item in self._unacknowledged():
            run_id, store, sequence, record_hash, previous, record = item
            try:
                self._client.append(run_id, store, sequence, record_hash, previous, record)
            except AgentSdkError as error:
                failed += 1
                with self._lock:
                    self.stats.last_error = f"{error.code}: {error}"
                continue
            self._acknowledge(ident)
            sent += 1
        if failed == 0:
            self._compact_outbox()
        return {"sent": sent, "failed": failed}

    def _outbox_entries(self) -> list[tuple[int, tuple | None]]:
        if self._outbox is None or not self._outbox.is_file():
            return []
        found: list[tuple[int, tuple | None]] = []
        for number, line in enumerate(self._outbox.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                if number == len(self._outbox.read_text(encoding="utf-8").splitlines()):
                    continue
                raise recorder_error(
                    "RECORDER_OUTBOX_CORRUPT",
                    f"Line {number} of the outbox {self._outbox} is not JSON; it is not the "
                    "last line, so it was not cut off by a crash.",
                ) from None
            if "ack" in data:
                found.append((int(data["ack"]), None))
            else:
                found.append(
                    (
                        int(data["id"]),
                        (
                            data["run"],
                            data["store"],
                            int(data["seq"]),
                            data["hash"],
                            data["prev"],
                            data["rec"],
                        ),
                    )
                )
        return found

    def _unacknowledged(self) -> list[tuple[int, tuple]]:
        waiting: dict[int, tuple] = {}
        for ident, item in self._outbox_entries():
            if item is None:
                waiting.pop(ident, None)
            else:
                waiting[ident] = item
        return sorted(waiting.items())

    def _outbox_write(self, data: dict) -> None:
        assert self._outbox is not None
        with self._lock, self._outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _acknowledge(self, ident: int | None) -> None:
        if self._outbox is not None and ident is not None:
            self._outbox_write({"ack": ident})

    def _compact_outbox(self) -> None:
        if self._outbox is None or self._unacknowledged():
            return
        with self._lock:
            self._outbox.write_text("", encoding="utf-8")

    def backfill(
        self, telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
    ) -> dict[str, int]:
        self.flush()
        sent = {"telemetry": 0, "audit": 0}
        outstanding = [ident for ident, item in self._unacknowledged() if item[0] == run_id]
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
        for ident in outstanding:
            self._acknowledge(ident)
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
        ident = None
        if self._outbox is not None:
            with self._lock:
                self._outbox_ids += 1
                ident = self._outbox_ids
            self._outbox_write(
                {
                    "id": ident,
                    "run": run_id,
                    "store": store,
                    "seq": sequence,
                    "hash": record_hash,
                    "prev": previous,
                    "rec": record,
                }
            )
        if self._asynchronous:
            try:
                self._queue.put_nowait((ident, item))
            except queue.Full:
                self._note_failure(
                    store, run_id, recorder_error("RECORDER_QUEUE_FULL", "The send queue is full.")
                )
            return
        self._deliver(item, ident)

    def _deliver(
        self, item: tuple[str, str, int, str, str | None, str | None], ident: int | None = None
    ) -> None:
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
        self._acknowledge(ident)
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
            try:
                if item is None:
                    return
                self._deliver(item[1], item[0])
            finally:
                self._queue.task_done()
