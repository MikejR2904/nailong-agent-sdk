import asyncio
import http.client
import json
import secrets
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from tests.support.processes import child_environment


def free_port(host="127.0.0.1"):
    sock = socket.socket()
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Server:
    def __init__(self, base: Path, host="127.0.0.1", extra_environment=None, tls_files=None):
        self.run_root = base / "run"
        self.spec_root = base / "specs"
        self.run_root.mkdir()
        self.spec_root.mkdir()
        self.host = host
        self.port = free_port(host)
        self.url = f"{'https' if tls_files else 'http'}://{host}:{self.port}/mcp"
        self.token = secrets.token_urlsafe(32)
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        env = child_environment(
            {
                "AGENT_RUNTIME_HOST": host,
                "AGENT_RUNTIME_PORT": str(self.port),
                "AGENT_RUNTIME_AUTH_TOKEN": self.token,
                "AGENT_RUNTIME_RUN_ROOT": str(self.run_root),
                "AGENT_SPECIFICATION_ROOT": str(self.spec_root),
                **_tls_environment(tls_files),
                **(extra_environment or {}),
            }
        )
        self.log_path = base / "server.log"
        self._log = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "nailong_agent_sdk.mcp.server"],
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(150):
            try:
                socket.create_connection((host, self.port), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("MCP server did not start")

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except Exception:
            self.process.kill()
        self._log.close()

    @asynccontextmanager
    async def session(self, headers="authorized"):
        sent = self.auth_headers if headers == "authorized" else headers
        async with create_mcp_http_client(headers=sent) as client:
            async with streamable_http_client(self.url, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def session_calls(self, calls, timeout=120):
        results = []
        async with self.session() as session:
            for name, arguments in calls:
                result = await asyncio.wait_for(session.call_tool(name, arguments), timeout)
                results.append(result)
        return results

    def call(self, name, arguments=None, timeout=120):
        result = asyncio.run(self.session_calls([(name, arguments or {})], timeout))[0]
        return self.structured(result)

    @staticmethod
    def structured(result):
        if result.structured_content is not None:
            return result.structured_content
        text = "".join(getattr(item, "text", "") for item in result.content)
        try:
            return json.loads(text)
        except ValueError:
            return {"raw_error": text, "is_error": result.is_error}


def _tls_environment(tls_files):
    if tls_files is None:
        return {}
    certificate, key = tls_files
    return {"AGENT_RUNTIME_TLS_CERTFILE": str(certificate), "AGENT_RUNTIME_TLS_KEYFILE": str(key)}


def _request_head(server, headers, body_length):
    sent = dict(headers)
    if "host" not in {name.lower() for name in sent}:
        sent["Host"] = f"{server.host}:{server.port}"
    sent["Content-Length"] = str(body_length)
    sent["Connection"] = "close"
    lines = "".join(f"{name}: {value}\r\n" for name, value in sent.items())
    return f"POST /mcp HTTP/1.1\r\n{lines}\r\n".encode("latin-1")


def raw_post(server, body, headers, authorized=True, limit=300, body_delay=0.0):
    sent = {**server.auth_headers, **headers} if authorized else headers
    head = _request_head(server, sent, len(body))
    try:
        with socket.create_connection((server.host, server.port), timeout=30) as connection:
            if body_delay:
                connection.sendall(head)
                time.sleep(body_delay)
                connection.sendall(body)
            else:
                connection.sendall(head + body)
            response = http.client.HTTPResponse(connection, method="POST")
            response.begin()
            return response.status, response.read()[:limit]
    except (OSError, http.client.HTTPException) as error:
        return -1, f"{type(error).__name__}: {error}".encode()
