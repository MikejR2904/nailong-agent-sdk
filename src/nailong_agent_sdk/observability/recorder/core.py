# Copyright (c) 2026 David Michael Indraputra

"""The recorder's storage rules: sequencing, the chain, record checks, attestation and finality."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from ...foundations.errors import AgentSdkError
from ...foundations.hashing import sha256_hex
from ...foundations.logging import get_logger
from ..attestation import RunAttestation, attestation_name, sign_attestation
from ..audit_log import AuditLogEntry, audit_entry_hash
from ..merkle import MerkleAccumulator
from ..run_checkpoints import CheckpointSigner, CheckpointSink, FileCheckpointSink
from ..telemetry_models import TelemetryEvent
from ..telemetry_store import event_hash
from ..transparency import (
    STORES,
    MerkleTreeHead,
    leaf_data,
    sign_tree_head,
    tree_head_name,
)
from .models import RecordedEntry
from .protocol import MAX_PAGE_ENTRIES, recorder_error

_logger = get_logger("recorder")


RUN_GATES = 64


class _Stream:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[RecordedEntry] = []
        self.offsets: list[int] = []
        self.accumulator = MerkleAccumulator()


class RecorderCore:
    """The recorder's state and rules; the server and the tests both drive it.

    Records of one run are serialized by that run's gate (one of ``RUN_GATES`` striped locks),
    so runs do not wait for each other's writes to disk. The registry lock is held only to look
    up or create a stream and is always taken after a gate, never before.
    """

    def __init__(
        self,
        directory: Path,
        signer: CheckpointSigner,
        *,
        head_every: int = 50,
        durable: bool = True,
        extra_sinks: Sequence[CheckpointSink] = (),
        verify_records: bool = False,
    ) -> None:
        if head_every < 1:
            raise ValueError(f"head_every must be at least 1, got {head_every}.")
        self._directory = directory.resolve()
        self._records = self._directory / "records"
        self._records.mkdir(parents=True, exist_ok=True)
        self._signer = signer
        self._head_every = head_every
        self._durable = durable
        self._registry = threading.Lock()
        self._conflicts = threading.Lock()
        self._gates = [threading.RLock() for _ in range(RUN_GATES)]
        self._heads = FileCheckpointSink(self._directory / "heads")
        self._attestations = FileCheckpointSink(self._directory / "attestations")
        self._extra_sinks = tuple(extra_sinks)
        self._verify_records = verify_records
        self._final: set[str] = set()
        self._streams: dict[tuple[str, str], _Stream] = {}
        self._load()

    @property
    def sink(self) -> FileCheckpointSink:
        return self._heads

    def head_sink(self) -> CheckpointSink:
        return self._heads

    def attestation_sink(self) -> CheckpointSink:
        return self._attestations

    def is_final(self, run_id: str) -> bool:
        with self._registry:
            return run_id in self._final

    def entries(self, run_id: str, store: str, include_records: bool = True) -> list[RecordedEntry]:
        found: list[RecordedEntry] = []
        after = 0
        while after is not None:
            page, after = self.page(
                run_id,
                store,
                after=after,
                max_entries=MAX_PAGE_ENTRIES,
                include_records=include_records,
            )
            found.extend(page)
        return found

    def page(
        self,
        run_id: str,
        store: str,
        *,
        after: int = 0,
        max_entries: int = MAX_PAGE_ENTRIES,
        max_bytes: int | None = None,
        include_records: bool = True,
    ) -> tuple[list[RecordedEntry], int | None]:
        self._require_store(store)
        if after < 0 or max_entries < 1:
            raise recorder_error(
                "RECORDER_REQUEST_INVALID",
                f"A page starts after a sequence of 0 or more and holds at least one entry, "
                f"got after={after} and limit={max_entries}.",
            )
        with self._gate(run_id):
            stream = self._streams.get((run_id, store))
            if stream is None:
                return [], None
            chosen = list(stream.entries[after : after + max_entries])
            offsets = stream.offsets[after : after + max_entries]
            path = stream.path
            more = after + max_entries < len(stream.entries)
        if not include_records:
            return self._within_budget(chosen, max_bytes, more)
        with path.open("rb") as handle:
            filled = []
            for entry, offset in zip(chosen, offsets, strict=True):
                handle.seek(offset)
                text = RecordedEntry.model_validate_json(handle.readline()).record
                filled.append(entry.model_copy(update={"record": text}))
        return self._within_budget(filled, max_bytes, more)

    @staticmethod
    def _within_budget(
        entries: list[RecordedEntry], max_bytes: int | None, more: bool
    ) -> tuple[list[RecordedEntry], int | None]:
        if max_bytes is not None:
            used = 0
            for index, entry in enumerate(entries):
                used += len(entry.model_dump_json())
                if used > max_bytes and index > 0:
                    entries, more = entries[:index], True
                    break
        return entries, (entries[-1].sequence if entries and more else None)

    def run_ids(self) -> list[str]:
        with self._registry:
            return sorted({run for run, _ in self._streams})

    def append(
        self,
        run_id: str,
        store: str,
        sequence: int,
        record_hash: str,
        previous_hash: str | None,
        record: str | None,
    ) -> dict[str, Any]:
        self._require_store(store)
        if sequence < 1:
            raise recorder_error(
                "RECORDER_SEQUENCE_INVALID",
                f'The {store} ledger of run "{run_id}" offered sequence {sequence}; '
                "sequences start at 1.",
                run_id=run_id,
                store=store,
                sequence=sequence,
            )
        try:
            leaf_data(record_hash)
        except ValueError as error:
            raise recorder_error("RECORDER_HASH_INVALID", str(error), run_id=run_id) from error
        with self._gate(run_id):
            stream = self._stream(run_id, store)
            held = len(stream.entries)
            if sequence <= held:
                existing = stream.entries[sequence - 1]
                if existing.hash == record_hash:
                    return {"size": held, "duplicate": True}
                self._note_conflict(run_id, store, sequence, existing.hash, record_hash)
                raise recorder_error(
                    "RECORDER_CONFLICT",
                    f'The {store} ledger of run "{run_id}" offered record {sequence} with hash '
                    f"{record_hash[:12]} but the recorder already holds {existing.hash[:12]} "
                    "for it: the local ledger was rewritten after the record was sent.",
                    run_id=run_id,
                    store=store,
                    sequence=sequence,
                )
            if sequence != held + 1:
                raise recorder_error(
                    "RECORDER_GAP",
                    f'The {store} ledger of run "{run_id}" offered record {sequence} but the '
                    f"recorder holds {held}; record {held + 1} was never received.",
                    run_id=run_id,
                    store=store,
                    expected=held + 1,
                )
            expected_previous = stream.entries[-1].hash if stream.entries else None
            if previous_hash != expected_previous:
                raise recorder_error(
                    "RECORDER_CHAIN_BROKEN",
                    f'Record {sequence} of the {store} ledger of run "{run_id}" names '
                    f"{(previous_hash or 'nothing')[:12]} as its predecessor but the recorder "
                    f"holds {(expected_previous or 'nothing')[:12]}.",
                    run_id=run_id,
                    store=store,
                    sequence=sequence,
                )
            claimed = self._check_record(
                run_id, store, sequence, record_hash, previous_hash, record
            )
            entry = RecordedEntry(
                sequence=sequence,
                hash=record_hash,
                previous=previous_hash,
                record=record,
                received_at_utc=datetime.now(UTC).isoformat(),
                claimed_at_utc=claimed,
            )
            offset = self._persist(stream, entry)
            stream.entries.append(entry.model_copy(update={"record": None}))
            stream.offsets.append(offset)
            stream.accumulator.append(leaf_data(record_hash))
            if len(stream.entries) % self._head_every == 0:
                self._publish(run_id, store, stream)
            return {"size": len(stream.entries), "duplicate": False}

    def seal(self, run_id: str) -> dict[str, MerkleTreeHead]:
        return self.attest(run_id, final=False)[0]

    def attest(
        self, run_id: str, *, final: bool = False
    ) -> tuple[dict[str, MerkleTreeHead], RunAttestation]:
        heads: dict[str, MerkleTreeHead] = {}
        with self._gate(run_id):
            state: dict[str, tuple[int, str | None]] = {}
            for store in STORES:
                stream = self._streams.get((run_id, store))
                if stream is not None and stream.entries:
                    heads[store] = self._publish(run_id, store, stream)
                    state[store] = (len(stream.entries), stream.entries[-1].hash)
                else:
                    state[store] = (0, None)
            attestation = sign_attestation(
                run_id, state["telemetry"], state["audit"], self._signer, final=final
            )
            name = attestation_name(run_id)
            self._attestations.append(run_id, attestation.model_dump_json())
            for sink in self._extra_sinks:
                try:
                    sink.append(name, attestation.model_dump_json())
                except AgentSdkError as error:
                    _logger.warning(
                        "Could not copy an attestation to %s: %s", sink.location(name), error
                    )
            if final:
                with self._registry:
                    self._final.add(run_id)
        return heads, attestation

    def attestation_lines(self, run_id: str) -> list[str]:
        return self._attestations.read_all(run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        with self._gate(run_id):
            streams = {}
            for store in STORES:
                stream = self._streams.get((run_id, store))
                held = stream.entries if stream is not None else []
                streams[store] = {
                    "held": len(held),
                    "first_received_at_utc": held[0].received_at_utc if held else None,
                    "last_received_at_utc": held[-1].received_at_utc if held else None,
                }
            return {"run_id": run_id, "final": run_id in self._final, "streams": streams}

    def _check_record(
        self,
        run_id: str,
        store: str,
        sequence: int,
        record_hash: str,
        previous: str | None,
        record: str | None,
    ) -> str | None:
        if not self._verify_records:
            return _claimed_time(record)
        if record is None:
            if self._verify_records:
                raise recorder_error(
                    "RECORDER_RECORD_REQUIRED",
                    f'The {store} ledger of run "{run_id}" sent record {sequence} without its '
                    "text, but this recorder verifies the records it accepts.",
                    run_id=run_id,
                    store=store,
                    sequence=sequence,
                )
            return None
        try:
            if store == "audit":
                audit = AuditLogEntry.model_validate_json(record)
                facts = (audit.run_id, audit.sequence, audit.previous_hash, audit_entry_hash(audit))
                claimed = audit.occurred_at_utc
            else:
                event = TelemetryEvent.parse_stored(record)
                facts = (
                    event.context.run_id,
                    event.sequence,
                    event.previous_event_hash,
                    event_hash(event),
                )
                claimed = event.occurred_at_utc
        except ValueError as error:
            raise recorder_error(
                "RECORDER_RECORD_INVALID",
                f'Record {sequence} of the {store} ledger of run "{run_id}" is not a '
                f"readable record ({type(error).__name__}): {str(error)[:200]}",
                run_id=run_id,
                store=store,
                sequence=sequence,
            ) from error
        mismatches = [
            label
            for label, found, wanted in (
                ("run id", facts[0], run_id),
                ("sequence", facts[1], sequence),
                ("previous hash", facts[2], previous),
                ("integrity hash", facts[3], record_hash),
            )
            if found != wanted
        ]
        if mismatches:
            raise recorder_error(
                "RECORDER_RECORD_INVALID",
                f'Record {sequence} of the {store} ledger of run "{run_id}" disagrees with '
                f"its text on: {', '.join(mismatches)}. The text hashes to "
                f"{facts[3][:12]} but {record_hash[:12]} was announced.",
                run_id=run_id,
                store=store,
                sequence=sequence,
            )
        return claimed

    def _require_store(self, store: str) -> None:
        if store not in STORES:
            raise recorder_error(
                "RECORDER_STORE_UNKNOWN",
                f'The ledger "{store}" is not one the recorder keeps; known: {list(STORES)}.',
                store=store,
            )

    def _stream(self, run_id: str, store: str) -> _Stream:
        key = (run_id, store)
        with self._registry:
            if key not in self._streams:
                name = f"{quote(run_id, safe='')}.{store}.jsonl"
                self._streams[key] = _Stream(self._records / name)
            return self._streams[key]

    def _gate(self, run_id: str) -> threading.RLock:
        return self._gates[hash(run_id) % RUN_GATES]

    def _persist(self, stream: _Stream, entry: RecordedEntry) -> int:
        try:
            with stream.path.open("ab") as handle:
                offset = handle.tell()
                handle.write(entry.model_dump_json().encode("utf-8") + b"\n")
                handle.flush()
                if self._durable:
                    os.fsync(handle.fileno())
            return offset
        except OSError as error:
            raise recorder_error(
                "RECORDER_STORAGE_FAILED",
                f'The recorder could not write record {entry.sequence} to "{stream.path}": '
                f"{type(error).__name__}: {error}",
                path=str(stream.path),
            ) from error

    def _note_conflict(
        self, run_id: str, store: str, sequence: int, held: str, offered: str
    ) -> None:
        line = json.dumps(
            {
                "run_id": run_id,
                "store": store,
                "sequence": sequence,
                "held": held,
                "offered": offered,
            }
        )
        with self._conflicts, (self._directory / "conflicts.jsonl").open("ab") as handle:
            handle.write(line.encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _publish(self, run_id: str, store: str, stream: _Stream) -> MerkleTreeHead:
        name = tree_head_name(run_id, store)
        head = sign_tree_head(
            stream.accumulator.size, stream.accumulator.root(), self._heads, name, self._signer
        )
        for sink in self._extra_sinks:
            try:
                sink.append(name, head.model_dump_json())
            except AgentSdkError as error:
                _logger.warning("Could not copy a head to %s: %s", sink.location(name), error)
        return head

    def _load(self) -> None:
        for path in sorted(self._records.glob("*.jsonl")):
            stem, _, store = path.stem.rpartition(".")
            if store not in STORES:
                continue
            run_id = unquote(stem)
            stream = _Stream(path)
            for entry, offset in _read_records(path):
                stream.entries.append(entry.model_copy(update={"record": None}))
                stream.offsets.append(offset)
                stream.accumulator.append(leaf_data(entry.hash))
            self._streams[(run_id, store)] = stream
        for run_id in {run for run, _ in self._streams}:
            lines = self._attestations.read_all(run_id)
            if lines and RunAttestation.model_validate_json(lines[-1]).final:
                self._final.add(run_id)


def _read_records(path: Path) -> list[tuple[RecordedEntry, int]]:
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        keep = data.rfind(b"\n") + 1
        tail = data[keep:]
        try:
            RecordedEntry.model_validate_json(tail)
        except ValueError:
            _logger.warning(
                'The recorder file "%s" ended in %d unterminated bytes (sha256 %s), a '
                "write cut off by a crash; they were discarded.",
                path,
                len(tail),
                sha256_hex(tail),
            )
            data = data[:keep]
        else:
            data += b"\n"
        path.write_bytes(data)
    entries: list[tuple[RecordedEntry, int]] = []
    offset = 0
    for number, line in enumerate(data.splitlines(keepends=True), start=1):
        start, offset = offset, offset + len(line)
        if not line.strip():
            continue
        try:
            entry = RecordedEntry.model_validate_json(line)
        except ValueError as error:
            raise recorder_error(
                "RECORDER_RECORDS_CORRUPT",
                f'Line {number} of the recorder file "{path}" is not a valid record '
                f"({type(error).__name__}): the file was edited or damaged, so the "
                "recorder will not start on it.",
                path=str(path),
                line=number,
            ) from error
        if entry.sequence != len(entries) + 1:
            raise recorder_error(
                "RECORDER_RECORDS_CORRUPT",
                f'Line {number} of the recorder file "{path}" holds sequence '
                f"{entry.sequence} but sequence {len(entries) + 1} was expected.",
                path=str(path),
                line=number,
            )
        entries.append((entry, start))
    return entries


def _claimed_time(record: str | None) -> str | None:
    if record is None:
        return None
    try:
        claimed = json.loads(record).get("occurred_at_utc")
    except (ValueError, AttributeError):
        return None
    return claimed if isinstance(claimed, str) else None
