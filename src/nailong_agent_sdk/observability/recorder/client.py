# Copyright (c) 2026 David Michael Indraputra

"""The agent-side handle on a recorder: a small pool of connections and the calls over them."""

from __future__ import annotations

import json
import socket
import ssl
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...foundations.errors import AgentSdkError
from ..attestation import RunAttestation
from ..run_checkpoints import CheckpointSink
from ..transparency import (
    MerkleTreeHead,
)
from .models import RecordedEntry
from .protocol import MAX_LINE_BYTES, MAX_PAGE_ENTRIES, RECORDER_SCHEMA, recorder_error

DEFAULT_CONNECTIONS = 4


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
            raise recorder_error(
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
                        raise recorder_error(
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
            raise recorder_error(
                str(reply.get("code", "RECORDER_ERROR")),
                str(reply.get("detail") or reply.get("code") or "the recorder refused the call"),
            )
        return reply


def parse_reply(line: bytes, address: tuple[str, int]) -> dict[str, Any]:
    try:
        reply = json.loads(line)
    except ValueError as error:
        raise recorder_error(
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
            raise recorder_error(
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
            raise recorder_error(
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
        raise recorder_error("RECORDER_HEADS_READ_ONLY", "Heads are written by the recorder alone.")

    def is_inside(self, root: Path) -> bool:
        return False
