# Copyright (c) 2026 David Michael Indraputra

"""A recorder that runs in another process and keeps its own signed copy of the ledgers.

The telemetry and audit ledgers live under the run root, where anything with file access can edit
or delete them, and an anchor taken at the end of a run covers whatever state the ledgers were in
by then. A recorder receives every record as it is written, over a socket, in a process the agent
has no handle on. It keeps its own copy, checks that each record chains to the one before, and
signs Merkle tree heads with a key that never leaves it. Later the local ledgers are compared with
its copy: an edit, a deletion or a truncation of anything the recorder already received is found
and the lost records can be read back.

What this separates is the agent from the evidence, not the host from it. The recorder is as
independent as the operating-system account, container or machine it runs in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import secrets
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex
from ..foundations.logging import get_logger
from .attestation import RunAttestation, attestation_name, sign_attestation
from .audit_log import AuditLogEntry, AuditTranscriptStore, audit_entry_hash
from .ed25519 import Ed25519Signer
from .merkle import MerkleAccumulator
from .run_checkpoints import CheckpointSigner, CheckpointSink, FileCheckpointSink
from .telemetry_models import TelemetryEvent
from .telemetry_store import TelemetryStore, event_hash
from .transparency import (
    STORES,
    MerkleTreeHead,
    TreeHeadVerdict,
    leaf_data,
    run_hashes,
    sign_tree_head,
    tree_head_name,
    verify_tree_heads,
)

_logger = get_logger("recorder")
RECORDER_SCHEMA = "agent-sdk-recorder-v2"
MAX_LINE_BYTES = 16_777_216
PAGE_BYTES = 4_194_304
MAX_PAGE_ENTRIES = 1_000
RUN_GATES = 64
DEFAULT_CONNECTIONS = 4
IDLE_TIMEOUT_SECONDS = 300.0
APPEND_TOKEN_VARIABLE = "NAILONG_RECORDER_APPEND_TOKEN"
READ_TOKEN_VARIABLE = "NAILONG_RECORDER_READ_TOKEN"
KEY_FILE = "recorder.key"
SCOPED_TOKEN_CONTEXT = b"nailong-recorder-append-v1:"


class RecordedEntry(StrictModel):
    sequence: int = Field(ge=1)
    hash: str
    previous: str | None = None
    record: str | None = None
    received_at_utc: str | None = None
    claimed_at_utc: str | None = None


def scoped_append_token(secret: str, run_id: str) -> str:
    message = SCOPED_TOKEN_CONTEXT + run_id.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class RecorderVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    NO_RECORD = "no-record"
    RECORDER_UNVERIFIED = "recorder-unverified"


class RecorderComparison(StrictModel):
    store: str
    verdict: RecorderVerdict
    recorded: int = Field(ge=0)
    local: int = Field(ge=0)
    first_difference: int | None = None
    removed_tail: int = Field(default=0, ge=0)
    differing: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {RecorderVerdict.MATCHES, RecorderVerdict.ADVANCED}


class RecorderSource(Protocol):
    def entries(
        self, run_id: str, store: str, include_records: bool = True
    ) -> list[RecordedEntry]: ...

    def head_sink(self) -> CheckpointSink: ...


def _fail(code: str, message: str, **details: Any) -> AgentSdkError:
    return AgentSdkError(code, message, details)


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
            raise _fail(
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
            raise _fail(
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
            raise _fail("RECORDER_HASH_INVALID", str(error), run_id=run_id) from error
        with self._gate(run_id):
            stream = self._stream(run_id, store)
            held = len(stream.entries)
            if sequence <= held:
                existing = stream.entries[sequence - 1]
                if existing.hash == record_hash:
                    return {"size": held, "duplicate": True}
                self._note_conflict(run_id, store, sequence, existing.hash, record_hash)
                raise _fail(
                    "RECORDER_CONFLICT",
                    f'The {store} ledger of run "{run_id}" offered record {sequence} with hash '
                    f"{record_hash[:12]} but the recorder already holds {existing.hash[:12]} "
                    "for it: the local ledger was rewritten after the record was sent.",
                    run_id=run_id,
                    store=store,
                    sequence=sequence,
                )
            if sequence != held + 1:
                raise _fail(
                    "RECORDER_GAP",
                    f'The {store} ledger of run "{run_id}" offered record {sequence} but the '
                    f"recorder holds {held}; record {held + 1} was never received.",
                    run_id=run_id,
                    store=store,
                    expected=held + 1,
                )
            expected_previous = stream.entries[-1].hash if stream.entries else None
            if previous_hash != expected_previous:
                raise _fail(
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
                raise _fail(
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
            raise _fail(
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
            raise _fail(
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
            raise _fail(
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
            raise _fail(
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
            raise _fail(
                "RECORDER_RECORDS_CORRUPT",
                f'Line {number} of the recorder file "{path}" is not a valid record '
                f"({type(error).__name__}): the file was edited or damaged, so the "
                "recorder will not start on it.",
                path=str(path),
                line=number,
            ) from error
        if entry.sequence != len(entries) + 1:
            raise _fail(
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


def server_ssl_context(
    certificate_file: Path, key_file: Path, *, client_ca_file: Path | None = None
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate_file), str(key_file))
    if client_ca_file is not None:
        context.load_verify_locations(str(client_ca_file))
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_ssl_context(
    ca_file: Path,
    *,
    certificate_file: Path | None = None,
    key_file: Path | None = None,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(str(ca_file))
    if certificate_file is not None:
        context.load_cert_chain(str(certificate_file), str(key_file) if key_file else None)
    return context


class _Handler(socketserver.StreamRequestHandler):
    server: RecorderServer
    timeout = IDLE_TIMEOUT_SECONDS

    def setup(self) -> None:
        if self.server.tls is not None:
            self.request.settimeout(self.timeout)
            try:
                self.request = self.server.tls.wrap_socket(self.request, server_side=True)
            except (ssl.SSLError, OSError) as error:
                _logger.warning("A TLS handshake with the recorder failed: %s", error)
                self.request.close()
                raise ConnectionError("TLS handshake failed") from error
        super().setup()

    def handle(self) -> None:
        while True:
            try:
                line = self.rfile.readline(MAX_LINE_BYTES + 1)
            except TimeoutError:
                return
            if not line:
                return
            if len(line) > MAX_LINE_BYTES:
                self._send({"ok": False, "code": "RECORDER_LINE_TOO_LONG"})
                return
            self._send(self.server.dispatch(line))

    def _send(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")
        self.wfile.flush()


class RecorderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        core: RecorderCore,
        *,
        append_token: str,
        read_token: str,
        host: str = "127.0.0.1",
        port: int = 0,
        scoped_append: bool = False,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        if not append_token or not read_token or append_token == read_token:
            raise ValueError("append_token and read_token are required and must differ.")
        self.core = core
        self.tls = tls
        self._scoped = scoped_append
        self._append_token = append_token
        self._read_token = read_token
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def dispatch(self, line: bytes) -> dict[str, Any]:
        try:
            request = json.loads(line)
            operation = request["op"]
            token = str(request.get("token", ""))
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "code": "RECORDER_REQUEST_INVALID", "detail": "not a request"}
        writes = operation in {"append", "seal"}
        if operation not in {"append", "seal", "read", "heads", "ping", "status", "attestations"}:
            return {"ok": False, "code": "RECORDER_OPERATION_UNKNOWN", "detail": str(operation)}
        if operation == "ping":
            return {"ok": True, "schema": RECORDER_SCHEMA}
        wanted = self._read_token
        scope = "read"
        if writes:
            wanted, scope = self._append_token, "append"
            if self._scoped:
                run = request.get("run_id")
                if not isinstance(run, str) or not run:
                    return {
                        "ok": False,
                        "code": "RECORDER_REQUEST_INVALID",
                        "detail": f"{operation} needs a run_id to pick the token that applies",
                    }
                wanted, scope = (
                    scoped_append_token(self._append_token, run),
                    "append token for this run",
                )
        if not hmac.compare_digest(token, wanted):
            return {
                "ok": False,
                "code": "RECORDER_TOKEN_REFUSED",
                "detail": f"the {scope} token is required for {operation}",
            }
        try:
            return {"ok": True, **self._run(operation, request)}
        except AgentSdkError as error:
            return {"ok": False, "code": error.code, "detail": str(error)}
        except (ValueError, KeyError, TypeError) as error:
            return {
                "ok": False,
                "code": "RECORDER_REQUEST_INVALID",
                "detail": f"{type(error).__name__}: {error}",
            }

    def _run(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        core = self.core
        if operation == "ping":
            return {"schema": RECORDER_SCHEMA}
        if operation == "append":
            return core.append(
                str(request["run_id"]),
                str(request["store"]),
                int(request["sequence"]),
                str(request["hash"]),
                request.get("previous"),
                request.get("record"),
            )
        if operation == "seal":
            heads, attestation = core.attest(
                str(request["run_id"]), final=bool(request.get("final", False))
            )
            return {
                "heads": {store: head.model_dump(mode="json") for store, head in heads.items()},
                "attestation": attestation.model_dump(mode="json"),
            }
        if operation == "status":
            return core.status(str(request["run_id"]))
        if operation == "attestations":
            return {"lines": core.attestation_lines(str(request["run_id"]))}
        if operation == "read":
            found, next_after = core.page(
                str(request["run_id"]),
                str(request["store"]),
                after=int(request.get("after", 0)),
                max_entries=min(int(request.get("limit", MAX_PAGE_ENTRIES)), MAX_PAGE_ENTRIES),
                max_bytes=PAGE_BYTES,
                include_records=bool(request.get("include_records", True)),
            )
            return {
                "entries": [item.model_dump(mode="json") for item in found],
                "next_after": next_after,
            }
        return {"lines": core.sink.read_all(str(request["name"]))}


class RecorderClient:
    """The agent-side handle: it adds records and asks for a seal; reading needs another token."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        append_token: str | Callable[[str], str],
        read_token: str | None = None,
        timeout_seconds: float = 5.0,
        tls: ssl.SSLContext | None = None,
        server_hostname: str | None = None,
        connections: int = DEFAULT_CONNECTIONS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}.")
        if connections < 1:
            raise ValueError(f"connections must be at least 1, got {connections}.")
        self._tls = tls
        self._server_hostname = server_hostname or host
        self.last_attestation: RunAttestation | None = None
        self._address = (host, port)
        self._append_token = append_token
        self._read_token = read_token
        self._timeout = timeout_seconds
        self._slots = threading.BoundedSemaphore(connections)
        self._idle: list[_Connection] = []
        self._idle_lock = threading.Lock()

    def append(
        self,
        run_id: str,
        store: str,
        sequence: int,
        record_hash: str,
        previous_hash: str | None,
        record: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            {
                "op": "append",
                "token": self._append_token_for(run_id),
                "run_id": run_id,
                "store": store,
                "sequence": sequence,
                "hash": record_hash,
                "previous": previous_hash,
                "record": record,
            }
        )

    def seal(self, run_id: str, final: bool = False) -> dict[str, MerkleTreeHead]:
        reply = self._call(
            {
                "op": "seal",
                "token": self._append_token_for(run_id),
                "run_id": run_id,
                "final": final,
            }
        )
        if "attestation" in reply:
            self.last_attestation = RunAttestation.model_validate(reply["attestation"])
        return {
            store: MerkleTreeHead.model_validate(head) for store, head in reply["heads"].items()
        }

    def entries(self, run_id: str, store: str, include_records: bool = True) -> list[RecordedEntry]:
        found: list[RecordedEntry] = []
        after: int | None = 0
        while after is not None:
            reply = self._call(
                {
                    "op": "read",
                    "token": self._need_read(),
                    "run_id": run_id,
                    "store": store,
                    "after": after,
                    "limit": MAX_PAGE_ENTRIES,
                    "include_records": include_records,
                }
            )
            found.extend(RecordedEntry.model_validate(item) for item in reply["entries"])
            after = reply.get("next_after")
        return found

    def head_sink(self) -> CheckpointSink:
        return _HeadSink(self)

    def status(self, run_id: str) -> dict[str, Any]:
        reply = self._call({"op": "status", "token": self._need_read(), "run_id": run_id})
        return {key: value for key, value in reply.items() if key != "ok"}

    def attestation_lines(self, run_id: str) -> list[str]:
        reply = self._call({"op": "attestations", "token": self._need_read(), "run_id": run_id})
        return [str(item) for item in reply["lines"]]

    def head_lines(self, name: str) -> list[str]:
        reply = self._call({"op": "heads", "token": self._need_read(), "name": name})
        return [str(item) for item in reply["lines"]]

    def close(self) -> None:
        with self._idle_lock:
            idle, self._idle = self._idle, []
        for connection in idle:
            connection.close()

    def _append_token_for(self, run_id: str) -> str:
        token = self._append_token
        return token(run_id) if callable(token) else token

    def _need_read(self) -> str:
        if self._read_token is None:
            raise _fail(
                "RECORDER_READ_TOKEN_MISSING",
                "This client holds only the append token, so it cannot read the recorder; the "
                "verifier builds its client with the read token.",
            )
        return self._read_token

    def _take(self) -> _Connection:
        with self._idle_lock:
            if self._idle:
                return self._idle.pop()
        return _Connection(self._address, self._timeout, self._tls, self._server_hostname)

    def _give_back(self, connection: _Connection) -> None:
        with self._idle_lock:
            self._idle.append(connection)

    def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(request).encode("utf-8") + b"\n"
        with self._slots:
            for attempt in (1, 2):
                connection = self._take()
                try:
                    line = connection.exchange(data)
                except (OSError, ConnectionError) as error:
                    connection.close()
                    if attempt == 2:
                        raise _fail(
                            "RECORDER_UNREACHABLE",
                            f"The recorder at {self._address[0]}:{self._address[1]} cannot be "
                            f"reached: {type(error).__name__}: {error}",
                            host=self._address[0],
                            port=self._address[1],
                        ) from error
                    continue
                except AgentSdkError:
                    connection.close()
                    raise
                self._give_back(connection)
                break
        reply = parse_reply(line, self._address)
        if not reply.get("ok"):
            raise _fail(
                str(reply.get("code", "RECORDER_ERROR")),
                str(reply.get("detail") or reply.get("code") or "the recorder refused the call"),
            )
        return reply


def parse_reply(line: bytes, address: tuple[str, int]) -> dict[str, Any]:
    try:
        reply = json.loads(line)
    except ValueError as error:
        raise _fail(
            "RECORDER_REPLY_INVALID",
            f"The recorder at {address[0]}:{address[1]} sent a reply that is not JSON "
            f"({type(error).__name__}: {error}).",
            host=address[0],
            port=address[1],
        ) from error
    return dict(reply)


class _Connection:
    """One socket to the recorder, opened on first use and checked against the protocol version."""

    def __init__(
        self,
        address: tuple[str, int],
        timeout: float,
        tls: ssl.SSLContext | None,
        server_hostname: str,
    ) -> None:
        self._address = address
        self._timeout = timeout
        self._tls = tls
        self._server_hostname = server_hostname
        self._socket: socket.socket | None = None
        self._reader: Any = None

    def exchange(self, data: bytes) -> bytes:
        if self._socket is None:
            self._open()
        return self._roundtrip(data)

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
        if self._socket is not None:
            self._socket.close()
        self._reader = self._socket = None

    def _open(self) -> None:
        opened = socket.create_connection(self._address, self._timeout)
        if self._tls is not None:
            opened = self._tls.wrap_socket(opened, server_hostname=self._server_hostname)
        opened.settimeout(self._timeout)
        self._socket = opened
        self._reader = opened.makefile("rb")
        greeting = parse_reply(self._roundtrip(b'{"op": "ping"}\n'), self._address)
        if greeting.get("schema") != RECORDER_SCHEMA:
            self.close()
            raise _fail(
                "RECORDER_PROTOCOL_MISMATCH",
                f"The recorder at {self._address[0]}:{self._address[1]} speaks protocol "
                f'"{greeting.get("schema")}" but this client speaks "{RECORDER_SCHEMA}"; '
                "upgrade the older side.",
                host=self._address[0],
                port=self._address[1],
            )

    def _roundtrip(self, data: bytes) -> bytes:
        assert self._socket is not None and self._reader is not None
        self._socket.sendall(data)
        line = self._reader.readline(MAX_LINE_BYTES + 1)
        if not line:
            raise ConnectionError("the recorder closed the connection")
        if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
            self.close()
            raise _fail(
                "RECORDER_REPLY_TOO_LARGE",
                f"The recorder at {self._address[0]}:{self._address[1]} sent a reply over "
                f"{MAX_LINE_BYTES} bytes, which this client does not read.",
                host=self._address[0],
                port=self._address[1],
            )
        return line


class _HeadSink:
    def __init__(self, client: RecorderClient) -> None:
        self._client = client

    def location(self, run_id: str) -> str:
        host, port = self._client._address
        return f"recorder {host}:{port} heads {run_id}"

    def read_all(self, run_id: str) -> list[str]:
        return self._client.head_lines(run_id)

    def append(self, run_id: str, record: str) -> None:
        raise _fail("RECORDER_HEADS_READ_ONLY", "Heads are written by the recorder alone.")

    def is_inside(self, root: Path) -> bool:
        return False


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
                    store, run_id, _fail("RECORDER_QUEUE_FULL", "The send queue is full.")
                )
            return
        self._deliver(item)

    def _deliver(self, item: tuple[str, str, int, str, str | None, str | None]) -> None:
        run_id, store, sequence, record_hash, previous, record = item
        if (run_id, store) in self.stats.degraded:
            if self._required and not self._asynchronous:
                raise _fail(
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


def compare_with_recorder(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    source: RecorderSource,
    verifier: CheckpointSigner,
) -> dict[str, RecorderComparison]:
    local = run_hashes(telemetry, audit, run_id)
    return {
        store: _compare_store(run_id, store, local[store], source, verifier) for store in STORES
    }


def _compare_store(
    run_id: str,
    store: str,
    local: list[str],
    source: RecorderSource,
    verifier: CheckpointSigner,
) -> RecorderComparison:
    recorded = source.entries(run_id, store, include_records=False)
    held = len(recorded)
    if not recorded:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.NO_RECORD,
            recorded=0,
            local=len(local),
            detail=f'The recorder holds no {store} record of run "{run_id}".',
        )
    heads = verify_tree_heads(
        [item.hash for item in recorded],
        source.head_sink(),
        tree_head_name(run_id, store),
        verifier,
    )
    if heads.verdict not in {TreeHeadVerdict.MATCHES, TreeHeadVerdict.ADVANCED}:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.RECORDER_UNVERIFIED,
            recorded=held,
            local=len(local),
            detail=f"The recorder's own signed heads do not vouch for its copy: {heads.detail}",
        )
    shared = min(held, len(local))
    differing = [index for index in range(shared) if recorded[index].hash != local[index]]
    if differing:
        first = recorded[differing[0]].sequence
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.DIVERGED,
            recorded=held,
            local=len(local),
            first_difference=first,
            differing=len(differing),
            detail=f"{len(differing)} of the first {shared} local {store} records differ from "
            f"the recorder's copy, the first at sequence {first}: the ledger was edited, "
            "had a record removed from the middle, or was resealed after the records were sent.",
        )
    if len(local) < held:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.TRUNCATED,
            recorded=held,
            local=len(local),
            first_difference=len(local) + 1,
            removed_tail=held - len(local),
            detail=f"The local {store} ledger holds {len(local)} records but the recorder "
            f"received {held}: the newest {held - len(local)} were removed.",
        )
    if len(local) > held:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.ADVANCED,
            recorded=held,
            local=len(local),
            detail=f"The local {store} ledger extends the recorder's {held} records by "
            f"{len(local) - held} that it has not received.",
        )
    return RecorderComparison(
        store=store,
        verdict=RecorderVerdict.MATCHES,
        recorded=held,
        local=len(local),
        detail=f"The local {store} ledger and the recorder's copy agree on all {held} records.",
    )


@dataclass
class RecorderProcess:
    directory: Path
    host: str
    port: int
    append_token: str
    read_token: str
    public_key_hex: str
    signer_id: str
    process: subprocess.Popen[str]

    def client(self, *, with_read: bool = False, timeout_seconds: float = 5.0) -> RecorderClient:
        return RecorderClient(
            self.host,
            self.port,
            append_token=self.append_token,
            read_token=self.read_token if with_read else None,
            timeout_seconds=timeout_seconds,
        )

    def verifier(self) -> Ed25519Signer:
        return Ed25519Signer(public_key=self.public_key_hex, signer_id=self.signer_id)

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def spawn_recorder(
    directory: Path,
    *,
    head_every: int = 50,
    durable: bool = True,
    startup_timeout_seconds: float = 30.0,
) -> RecorderProcess:
    append_token, read_token = secrets.token_hex(16), secrets.token_hex(16)
    environment = {
        **os.environ,
        APPEND_TOKEN_VARIABLE: append_token,
        READ_TOKEN_VARIABLE: read_token,
    }
    command = [
        sys.executable,
        "-m",
        "nailong_agent_sdk.observability.recorder",
        "--directory",
        str(directory),
        "--head-every",
        str(head_every),
        *([] if durable else ["--no-fsync"]),
    ]
    process = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=lambda: ready.put(process.stdout.readline() if process.stdout else ""),
        daemon=True,
    ).start()
    try:
        line = ready.get(timeout=startup_timeout_seconds)
        info = json.loads(line)
    except (queue.Empty, ValueError) as error:
        process.kill()
        stderr = process.stderr.read() if process.stderr else ""
        raise _fail(
            "RECORDER_START_FAILED",
            f"The recorder process did not report that it was listening within "
            f"{startup_timeout_seconds}s: {type(error).__name__}: {error}. {stderr[-300:]}",
        ) from error
    return RecorderProcess(
        directory=directory,
        host=str(info["host"]),
        port=int(info["port"]),
        append_token=append_token,
        read_token=read_token,
        public_key_hex=str(info["public_key"]),
        signer_id=str(info["signer_id"]),
        process=process,
    )


def _load_signer(directory: Path) -> Ed25519Signer:
    path = directory / KEY_FILE
    if path.is_file():
        return Ed25519Signer(path.read_text("utf-8").strip(), signer_id="recorder")
    directory.mkdir(parents=True, exist_ok=True)
    signer = Ed25519Signer.generate("recorder")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(signer.private_key_hex())
    return signer


def _secret(path: Path | None, variable: str) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get(variable, "")


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nailong-recorder")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--head-every", type=int, default=50)
    parser.add_argument("--no-fsync", action="store_true")
    parser.add_argument("--append-token-file", type=Path)
    parser.add_argument("--read-token-file", type=Path)
    parser.add_argument("--scoped-append", action="store_true")
    parser.add_argument("--verify-records", action="store_true")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--tls-client-ca", type=Path)
    arguments = parser.parse_args(argv)
    append_token = _secret(arguments.append_token_file, APPEND_TOKEN_VARIABLE)
    read_token = _secret(arguments.read_token_file, READ_TOKEN_VARIABLE)
    if not append_token or not read_token:
        print(
            f"The append and read tokens must both be given: in {APPEND_TOKEN_VARIABLE} and "
            f"{READ_TOKEN_VARIABLE} in the recorder's environment, or in the files named by "
            "--append-token-file and --read-token-file (files keep them out of child processes).",
            file=sys.stderr,
        )
        return 2
    if bool(arguments.tls_cert) != bool(arguments.tls_key):
        print("--tls-cert and --tls-key must be given together.", file=sys.stderr)
        return 2
    tls = (
        server_ssl_context(
            arguments.tls_cert, arguments.tls_key, client_ca_file=arguments.tls_client_ca
        )
        if arguments.tls_cert
        else None
    )
    signer = _load_signer(arguments.directory)
    core = RecorderCore(
        arguments.directory,
        signer,
        head_every=arguments.head_every,
        durable=not arguments.no_fsync,
        verify_records=arguments.verify_records,
    )
    server = RecorderServer(
        core,
        append_token=append_token,
        read_token=read_token,
        host=arguments.host,
        port=arguments.port,
        scoped_append=arguments.scoped_append,
        tls=tls,
    )
    print(
        json.dumps(
            {
                "host": arguments.host,
                "port": server.port,
                "public_key": signer.public_key_hex,
                "signer_id": signer.signer_id,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
