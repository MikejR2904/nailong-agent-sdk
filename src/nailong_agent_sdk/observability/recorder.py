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

import hmac
import json
import os
import queue
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.logging import get_logger
from .audit_log import AuditLogEntry, AuditTranscriptStore
from .ed25519 import Ed25519Signer
from .merkle import MerkleAccumulator
from .run_checkpoints import CheckpointSigner, CheckpointSink, FileCheckpointSink
from .telemetry_models import TelemetryEvent
from .telemetry_store import TelemetryStore
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
RECORDER_SCHEMA = "agent-sdk-recorder-v1"
MAX_LINE_BYTES = 1_048_576
APPEND_TOKEN_VARIABLE = "NAILONG_RECORDER_APPEND_TOKEN"
READ_TOKEN_VARIABLE = "NAILONG_RECORDER_READ_TOKEN"
KEY_FILE = "recorder.key"


class RecordedEntry(StrictModel):
    sequence: int = Field(ge=1)
    hash: str
    previous: str | None = None
    record: str | None = None


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
    def entries(self, run_id: str, store: str) -> list[RecordedEntry]: ...

    def head_sink(self) -> CheckpointSink: ...


def _fail(code: str, message: str, **details: Any) -> AgentSdkError:
    return AgentSdkError(code, message, details)


class _Stream:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[RecordedEntry] = []
        self.accumulator = MerkleAccumulator()


class RecorderCore:
    """The recorder's state and rules; the server and the tests both drive it."""

    def __init__(
        self,
        directory: Path,
        signer: CheckpointSigner,
        *,
        head_every: int = 50,
        durable: bool = True,
        extra_sinks: Sequence[CheckpointSink] = (),
    ) -> None:
        if head_every < 1:
            raise ValueError(f"head_every must be at least 1, got {head_every}.")
        self._directory = directory.resolve()
        self._records = self._directory / "records"
        self._records.mkdir(parents=True, exist_ok=True)
        self._signer = signer
        self._head_every = head_every
        self._durable = durable
        self._lock = threading.Lock()
        self._heads = FileCheckpointSink(self._directory / "heads")
        self._extra_sinks = tuple(extra_sinks)
        self._streams: dict[tuple[str, str], _Stream] = {}
        self._load()

    @property
    def sink(self) -> FileCheckpointSink:
        return self._heads

    def head_sink(self) -> CheckpointSink:
        return self._heads

    def entries(self, run_id: str, store: str) -> list[RecordedEntry]:
        self._require_store(store)
        with self._lock:
            stream = self._streams.get((run_id, store))
            return list(stream.entries) if stream is not None else []

    def run_ids(self) -> list[str]:
        with self._lock:
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
        try:
            leaf_data(record_hash)
        except ValueError as error:
            raise _fail("RECORDER_HASH_INVALID", str(error), run_id=run_id) from error
        with self._lock:
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
            entry = RecordedEntry(
                sequence=sequence, hash=record_hash, previous=previous_hash, record=record
            )
            self._persist(stream, entry)
            stream.entries.append(entry)
            stream.accumulator.append(leaf_data(record_hash))
            if len(stream.entries) % self._head_every == 0:
                self._publish(run_id, store, stream)
            return {"size": len(stream.entries), "duplicate": False}

    def seal(self, run_id: str) -> dict[str, MerkleTreeHead]:
        heads: dict[str, MerkleTreeHead] = {}
        with self._lock:
            for store in STORES:
                stream = self._streams.get((run_id, store))
                if stream is not None and stream.entries:
                    heads[store] = self._publish(run_id, store, stream)
        return heads

    def _require_store(self, store: str) -> None:
        if store not in STORES:
            raise _fail(
                "RECORDER_STORE_UNKNOWN",
                f'The ledger "{store}" is not one the recorder keeps; known: {list(STORES)}.',
                store=store,
            )

    def _stream(self, run_id: str, store: str) -> _Stream:
        key = (run_id, store)
        if key not in self._streams:
            name = f"{quote(run_id, safe='')}.{store}.jsonl"
            self._streams[key] = _Stream(self._records / name)
        return self._streams[key]

    def _persist(self, stream: _Stream, entry: RecordedEntry) -> None:
        with stream.path.open("ab") as handle:
            handle.write(entry.model_dump_json().encode("utf-8") + b"\n")
            handle.flush()
            if self._durable:
                os.fsync(handle.fileno())

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
        with (self._directory / "conflicts.jsonl").open("ab") as handle:
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
            for line in path.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                entry = RecordedEntry.model_validate_json(line)
                stream.entries.append(entry)
                stream.accumulator.append(leaf_data(entry.hash))
            self._streams[(run_id, store)] = stream


class _Handler(socketserver.StreamRequestHandler):
    server: RecorderServer

    def handle(self) -> None:
        while True:
            line = self.rfile.readline(MAX_LINE_BYTES + 1)
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
    ) -> None:
        if not append_token or not read_token or append_token == read_token:
            raise ValueError("append_token and read_token are required and must differ.")
        self.core = core
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
        wanted = self._append_token if writes else self._read_token
        if operation not in {"append", "seal", "read", "heads", "ping"}:
            return {"ok": False, "code": "RECORDER_OPERATION_UNKNOWN", "detail": str(operation)}
        if not hmac.compare_digest(token, wanted):
            return {
                "ok": False,
                "code": "RECORDER_TOKEN_REFUSED",
                "detail": f"the {'append' if writes else 'read'} token is required for {operation}",
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
            heads = core.seal(str(request["run_id"]))
            return {"heads": {store: head.model_dump(mode="json") for store, head in heads.items()}}
        if operation == "read":
            found = core.entries(str(request["run_id"]), str(request["store"]))
            return {"entries": [item.model_dump(mode="json") for item in found]}
        return {"lines": core.sink.read_all(str(request["name"]))}


class RecorderClient:
    """The agent-side handle: it adds records and asks for a seal; reading needs another token."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        append_token: str,
        read_token: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}.")
        self._address = (host, port)
        self._append_token = append_token
        self._read_token = read_token
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._reader: Any = None

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
                "token": self._append_token,
                "run_id": run_id,
                "store": store,
                "sequence": sequence,
                "hash": record_hash,
                "previous": previous_hash,
                "record": record,
            }
        )

    def seal(self, run_id: str) -> dict[str, MerkleTreeHead]:
        reply = self._call({"op": "seal", "token": self._append_token, "run_id": run_id})
        return {
            store: MerkleTreeHead.model_validate(head) for store, head in reply["heads"].items()
        }

    def entries(self, run_id: str, store: str) -> list[RecordedEntry]:
        reply = self._call(
            {"op": "read", "token": self._need_read(), "run_id": run_id, "store": store}
        )
        return [RecordedEntry.model_validate(item) for item in reply["entries"]]

    def head_sink(self) -> CheckpointSink:
        return _HeadSink(self)

    def head_lines(self, name: str) -> list[str]:
        reply = self._call({"op": "heads", "token": self._need_read(), "name": name})
        return [str(item) for item in reply["lines"]]

    def close(self) -> None:
        with self._lock:
            self._drop()

    def _need_read(self) -> str:
        if self._read_token is None:
            raise _fail(
                "RECORDER_READ_TOKEN_MISSING",
                "This client holds only the append token, so it cannot read the recorder; the "
                "verifier builds its client with the read token.",
            )
        return self._read_token

    def _drop(self) -> None:
        if self._reader is not None:
            self._reader.close()
        if self._socket is not None:
            self._socket.close()
        self._reader = self._socket = None

    def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(request).encode("utf-8") + b"\n"
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._socket is None:
                        self._socket = socket.create_connection(self._address, self._timeout)
                        self._socket.settimeout(self._timeout)
                        self._reader = self._socket.makefile("rb")
                    self._socket.sendall(data)
                    line = self._reader.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        raise ConnectionError("the recorder closed the connection")
                    break
                except (OSError, ConnectionError) as error:
                    self._drop()
                    if attempt == 2:
                        raise _fail(
                            "RECORDER_UNREACHABLE",
                            f"The recorder at {self._address[0]}:{self._address[1]} cannot be "
                            f"reached: {type(error).__name__}: {error}",
                            host=self._address[0],
                            port=self._address[1],
                        ) from error
        reply = json.loads(line)
        if not reply.get("ok"):
            raise _fail(
                str(reply.get("code", "RECORDER_ERROR")),
                str(reply.get("detail") or reply.get("code") or "the recorder refused the call"),
            )
        return dict(reply)


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

    def finish(self, run_id: str, timeout_seconds: float = 10.0) -> dict[str, MerkleTreeHead]:
        self.flush(timeout_seconds)
        return self._client.seal(run_id)

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
    recorded = source.entries(run_id, store)
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


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nailong-recorder")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--head-every", type=int, default=50)
    parser.add_argument("--no-fsync", action="store_true")
    arguments = parser.parse_args(argv)
    append_token = os.environ.get(APPEND_TOKEN_VARIABLE, "")
    read_token = os.environ.get(READ_TOKEN_VARIABLE, "")
    if not append_token or not read_token:
        print(
            f"{APPEND_TOKEN_VARIABLE} and {READ_TOKEN_VARIABLE} must both be set in the "
            "environment of the recorder process.",
            file=sys.stderr,
        )
        return 2
    signer = _load_signer(arguments.directory)
    core = RecorderCore(
        arguments.directory,
        signer,
        head_every=arguments.head_every,
        durable=not arguments.no_fsync,
    )
    server = RecorderServer(
        core,
        append_token=append_token,
        read_token=read_token,
        host=arguments.host,
        port=arguments.port,
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
