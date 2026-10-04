import asyncio
import json
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    def __init__(self, base: Path, host="127.0.0.1", extra_environment=None):
        self.run_root = base / "run"
        self.spec_root = base / "specs"
        self.run_root.mkdir()
        self.spec_root.mkdir()
        self.host = host
        self.port = free_port(host)
        self.url = f"http://{host}:{self.port}/mcp"
        self.token = secrets.token_urlsafe(32)
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        env = child_environment(
            {
                "AGENT_RUNTIME_HOST": host,
                "AGENT_RUNTIME_PORT": str(self.port),
                "AGENT_RUNTIME_AUTH_TOKEN": self.token,
                "AGENT_RUNTIME_RUN_ROOT": str(self.run_root),
                "AGENT_SPECIFICATION_ROOT": str(self.spec_root),
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


def raw_post(server, body, headers, authorized=True, limit=300):
    sent = {**server.auth_headers, **headers} if authorized else headers
    request = urllib.request.Request(
        f"http://{server.host}:{server.port}/mcp", data=body, method="POST", headers=sent
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()[:limit]
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:limit]
    except (ConnectionError, OSError) as error:
        return -1, type(error).__name__.encode()
