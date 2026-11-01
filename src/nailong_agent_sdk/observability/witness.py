# Copyright (c) 2026 David Michael Indraputra

"""A remote witness for checkpoints: an HTTP sink that can append, and a reference server."""

from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote, unquote, urlparse

from ..foundations.errors import AgentSdkError

RECORDS_PREFIX = "/v1/records/"
MAX_RECORD_CHARS = 65_536
MAX_BODY_BYTES = MAX_RECORD_CHARS + 1_024
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, *arguments: Any, **keywords: Any) -> None:
        return None


_OPENER = urlrequest.build_opener(_NoRedirect)


class HttpWitnessSink:
    """Appends each checkpoint to a witness the writer can add to but cannot read or rewrite."""

    def __init__(
        self,
        base_url: str,
        *,
        append_token: str,
        read_token: str | None = None,
        timeout_seconds: float = 10.0,
        allow_insecure_http: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f'A witness base_url must be an http(s) URL, got "{base_url}".')
        if parsed.scheme == "http" and not (
            allow_insecure_http and parsed.hostname in LOOPBACK_HOSTS
        ):
            raise ValueError(
                f'The witness URL "{base_url}" is plain http; use https, or set '
                "allow_insecure_http for a loopback address only."
            )
        if not append_token:
            raise ValueError("append_token is required to add records to a witness.")
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds}.")
        self._base = base_url.rstrip("/")
        self._append_token = append_token
        self._read_token = read_token
        self._timeout = timeout_seconds

    def location(self, run_id: str) -> str:
        return f"{self._base}{RECORDS_PREFIX}{quote(run_id, safe='')}"

    def append(self, run_id: str, record: str) -> None:
        url = self.location(run_id)
        body = json.dumps({"record": record}).encode("utf-8")
        status, text = self._call("POST", url, self._append_token, body)
        if status not in {200, 201}:
            raise AgentSdkError(
                "WITNESS_WRITE_FAILED",
                f"The witness at {url} did not accept the checkpoint: HTTP {status}: {text[:200]}",
                {"url": url, "status": status},
            )

    def read_all(self, run_id: str) -> list[str]:
        url = self.location(run_id)
        if self._read_token is None:
            raise AgentSdkError(
                "WITNESS_READ_TOKEN_MISSING",
                f"This sink holds only the append token, so it cannot read {url}; the verifier "
                "builds its sink with the read token.",
                {"url": url},
            )
        status, text = self._call("GET", url, self._read_token, None)
        if status == 404:
            return []
        if status != 200:
            raise AgentSdkError(
                "WITNESS_READ_FAILED",
                f"The witness at {url} refused the read: HTTP {status}: {text[:200]}",
                {"url": url, "status": status},
            )
        try:
            records = json.loads(text)["records"]
        except (ValueError, KeyError, TypeError) as error:
            raise AgentSdkError(
                "WITNESS_RESPONSE_INVALID",
                f"The witness at {url} answered with a body that is not a record list: "
                f"{type(error).__name__}: {error}",
                {"url": url},
            ) from error
        if not isinstance(records, list) or not all(isinstance(item, str) for item in records):
            raise AgentSdkError(
                "WITNESS_RESPONSE_INVALID",
                f"The witness at {url} answered with records that are not all strings.",
                {"url": url},
            )
        return records

    def is_inside(self, root: Path) -> bool:
        return False

    def _call(self, method: str, url: str, token: str, body: bytes | None) -> tuple[int, str]:
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        call = urlrequest.Request(url, data=body, method=method, headers=headers)
        try:
            with _OPENER.open(call, timeout=self._timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as failure:
            return failure.code, failure.read().decode("utf-8", errors="replace")
        except (urlerror.URLError, OSError) as failure:
            raise AgentSdkError(
                "WITNESS_UNREACHABLE",
                f"The witness at {url} cannot be reached: {type(failure).__name__}: {failure}",
                {"url": url},
            ) from failure


class WitnessServer:
    """Reference append-only witness: it can add a record and list them, nothing else."""

    def __init__(
        self,
        *,
        append_token: str,
        read_token: str,
        directory: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if not append_token or not read_token or append_token == read_token:
            raise ValueError("append_token and read_token are required and must differ.")
        self._append_token = append_token
        self._read_token = read_token
        self._directory = directory
        self._lock = threading.Lock()
        self._records: dict[str, list[str]] = {}
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            for path in sorted(directory.glob("*.jsonl")):
                lines = [line for line in path.read_text("utf-8").splitlines() if line]
                self._records[unquote(path.stem)] = [json.loads(line)["record"] for line in lines]
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def records(self, run_id: str) -> list[str]:
        with self._lock:
            return list(self._records.get(run_id, []))

    def _store(self, run_id: str, record: str) -> None:
        with self._lock:
            self._records.setdefault(run_id, []).append(record)
            if self._directory is not None:
                path = self._directory / f"{quote(run_id, safe='')}.jsonl"
                with path.open("ab") as stream:
                    stream.write(json.dumps({"record": record}).encode("utf-8") + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _run_id(self) -> str | None:
                path = urlparse(self.path).path
                if not path.startswith(RECORDS_PREFIX) or len(path) == len(RECORDS_PREFIX):
                    return None
                return unquote(path[len(RECORDS_PREFIX) :])

            def _authorised(self, token: str) -> bool:
                header = self.headers.get("Authorization", "")
                return header.startswith("Bearer ") and hmac.compare_digest(
                    header[len("Bearer ") :], token
                )

            def _body(self) -> bytes | None:
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_BODY_BYTES:
                    self.close_connection = True
                    return None
                return self.rfile.read(length)

            def do_POST(self) -> None:
                body = self._body()
                if body is None:
                    return self._reply(413, {"error": f"body above {MAX_BODY_BYTES} bytes"})
                run_id = self._run_id()
                if run_id is None:
                    return self._reply(404, {"error": "unknown path"})
                if not self._authorised(owner._append_token):
                    return self._reply(403, {"error": "the append token is required"})
                try:
                    record = json.loads(body)["record"]
                except (ValueError, KeyError, TypeError):
                    return self._reply(400, {"error": 'body must be {"record": "<string>"}'})
                if not isinstance(record, str) or not record or len(record) > MAX_RECORD_CHARS:
                    return self._reply(400, {"error": "record must be a non-empty string"})
                owner._store(run_id, record)
                return self._reply(201, {"stored": True})

            def do_GET(self) -> None:
                self._body()
                run_id = self._run_id()
                if run_id is None:
                    return self._reply(404, {"error": "unknown path"})
                if not self._authorised(owner._read_token):
                    return self._reply(403, {"error": "the read token is required"})
                records = owner.records(run_id)
                if not records:
                    return self._reply(404, {"error": "no records"})
                return self._reply(200, {"records": records})

            def _refuse(self) -> None:
                self._body()
                self._reply(405, {"error": "the witness is append-only"})

            do_PUT = do_PATCH = do_DELETE = _refuse

        return Handler
