# Copyright (c) 2026 David Michael Indraputra

"""The recorder's network face: connections, tokens, operations and TLS."""

from __future__ import annotations

import hmac
import json
import socketserver
import ssl
from typing import Any

from ...foundations.errors import AgentSdkError
from ...foundations.logging import get_logger
from .core import RecorderCore
from .protocol import (
    MAX_LINE_BYTES,
    MAX_PAGE_ENTRIES,
    PAGE_BYTES,
    RECORDER_SCHEMA,
    scoped_append_token,
)

_logger = get_logger("recorder")


IDLE_TIMEOUT_SECONDS = 300.0


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
