import asyncio
import json
import subprocess
import sys

import pytest

from nailong_agent_sdk.mcp.security import (
    BearerTokenMiddleware,
    ServerConfigurationError,
    load_http_service_settings,
)
from tests.support.mcp_server import Server, raw_post
from tests.support.processes import child_environment

TOKEN = "t" * 32


def settings(**overrides):
    environ = {"AGENT_RUNTIME_AUTH_TOKEN": TOKEN, **overrides}
    return load_http_service_settings({k: v for k, v in environ.items() if v is not None})


def test_defaults_bind_the_loopback_address_on_8001():
    loaded = settings()
    assert (loaded.host, loaded.port, loaded.token) == ("127.0.0.1", 8001, TOKEN)
    assert loaded.allowed_hosts == ("127.0.0.1:*", "localhost:*", "[::1]:*")
    assert TOKEN not in repr(loaded)


@pytest.mark.parametrize("token", [None, "", "short", "t" * 31, "t" * 16 + " " + "t" * 16])
def test_a_missing_or_weak_token_refuses_to_start_naming_the_variable(token):
    with pytest.raises(ServerConfigurationError, match="AGENT_RUNTIME_AUTH_TOKEN") as excinfo:
        settings(AGENT_RUNTIME_AUTH_TOKEN=token)
    assert "at least 32 characters" in str(excinfo.value)
    if token:
        assert token not in str(excinfo.value)


@pytest.mark.parametrize("port", ["abc", "", "0", "-1", "65536", "80.5"])
def test_a_bad_port_is_refused_naming_the_variable_and_value(port):
    with pytest.raises(ServerConfigurationError) as excinfo:
        settings(AGENT_RUNTIME_PORT=port)
    assert 'AGENT_RUNTIME_PORT must be an integer from 1 to 65535, got "' + port + '"' in str(
        excinfo.value
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "LOCALHOST", "::1"])
def test_every_loopback_bind_gets_host_and_origin_protection(host):
    loaded = settings(AGENT_RUNTIME_HOST=host)
    security = loaded.transport_security()
    bracketed = f"[{host}]" if ":" in host else host
    assert security.enable_dns_rebinding_protection is True
    assert f"{bracketed}:*" in security.allowed_hosts
    assert f"http://{bracketed}:*" in security.allowed_origins
    assert "attacker.example.com" not in " ".join(security.allowed_hosts)


def test_a_non_loopback_bind_needs_an_explicit_host_allow_list_and_allows_no_origin():
    with pytest.raises(ServerConfigurationError) as excinfo:
        settings(AGENT_RUNTIME_HOST="192.0.2.10")
    assert 'AGENT_RUNTIME_HOST "192.0.2.10" is not a loopback address' in str(excinfo.value)
    assert "AGENT_RUNTIME_ALLOWED_HOSTS" in str(excinfo.value)
    loaded = settings(
        AGENT_RUNTIME_HOST="192.0.2.10",
        AGENT_RUNTIME_ALLOWED_HOSTS="runtime.internal:8001, 192.0.2.10:*",
    )
    security = loaded.transport_security()
    assert security.allowed_hosts == ["runtime.internal:8001", "192.0.2.10:*"]
    assert security.allowed_origins == []
    for entry in ("https://runtime.internal", "runtime.internal/mcp", "runtime internal"):
        with pytest.raises(ServerConfigurationError, match="bare host or host:port"):
            settings(AGENT_RUNTIME_HOST="192.0.2.10", AGENT_RUNTIME_ALLOWED_HOSTS=entry)


def test_an_empty_host_is_refused():
    with pytest.raises(ServerConfigurationError, match="AGENT_RUNTIME_HOST must not be empty"):
        settings(AGENT_RUNTIME_HOST="  ")


def call_asgi(app, headers=None, scope_type="http"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": scope_type,
        "method": "POST",
        "path": "/mcp",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body


async def inner_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"inner"})


def test_the_middleware_passes_only_the_exact_bearer_token():
    app = BearerTokenMiddleware(inner_app, token=TOKEN)
    assert call_asgi(app, {"Authorization": f"Bearer {TOKEN}"})[0] == 200
    assert call_asgi(app, {"Authorization": f"bearer {TOKEN}"})[0] == 200
    for header in (
        None,
        f"Bearer {TOKEN}x",
        f"Bearer {TOKEN[:-1]}",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer",
        "Bearer ",
    ):
        status, headers, body = call_asgi(app, {"Authorization": header} if header else {})
        assert status == 401 and headers[b"www-authenticate"] == b"Bearer", header
        payload = json.loads(body)
        assert payload["ok"] is False and payload["errors"][0]["type"] == "Unauthorized"
        assert TOKEN not in body.decode()


def test_the_middleware_leaves_non_http_scopes_alone():
    seen = []

    async def lifespan_app(scope, receive, send):
        seen.append(scope["type"])

    app = BearerTokenMiddleware(lifespan_app, token=TOKEN)
    asyncio.run(app({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


def test_the_middleware_closes_an_unauthenticated_websocket_without_reaching_the_app():
    reached = []
    sent = []

    async def socket_app(scope, receive, send):
        reached.append(scope["type"])

    async def send(message):
        sent.append(message)

    app = BearerTokenMiddleware(socket_app, token=TOKEN)
    anonymous = {"type": "websocket", "path": "/mcp", "headers": []}
    asyncio.run(app(anonymous, None, send))
    assert reached == [] and sent == [{"type": "websocket.close", "code": 1008}]
    wrong = {"type": "websocket", "headers": [(b"authorization", b"Bearer " + b"x" * 32)]}
    asyncio.run(app(wrong, None, send))
    assert reached == [] and len(sent) == 2
    right = {"type": "websocket", "headers": [(b"authorization", f"Bearer {TOKEN}".encode())]}
    asyncio.run(app(right, None, send))
    assert reached == ["websocket"] and len(sent) == 2


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, "AGENT_RUNTIME_AUTH_TOKEN"),
        ({"AGENT_RUNTIME_AUTH_TOKEN": TOKEN, "AGENT_RUNTIME_PORT": "eighty"}, "AGENT_RUNTIME_PORT"),
        (
            {"AGENT_RUNTIME_AUTH_TOKEN": TOKEN, "AGENT_RUNTIME_HOST": "192.0.2.10"},
            "AGENT_RUNTIME_ALLOWED_HOSTS",
        ),
    ],
)
def test_the_service_process_exits_with_status_2_and_a_clear_message(environment, expected):
    completed = subprocess.run(
        [sys.executable, "-m", "nailong_agent_sdk.mcp.server"],
        env=child_environment(environment),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 2, completed.stderr
    assert "nailong-agent-sdk MCP server cannot start:" in completed.stderr
    assert expected in completed.stderr
    assert "Traceback" not in completed.stderr


def test_host_and_origin_checks_apply_to_every_loopback_bind(tmp_path):
    try:
        server = Server(tmp_path, host="127.0.0.2")
    except OSError as error:
        pytest.skip(f"127.0.0.2 cannot be bound here: {error}")
    try:
        init = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            }
        ).encode()
        base = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        own = f"127.0.0.2:{server.port}"
        good = raw_post(server, init, {**base, "Host": own})
        rebound = raw_post(server, init, {**base, "Host": "attacker.example.com"})
        foreign = raw_post(
            server, init, {**base, "Host": own, "Origin": "https://attacker.example.com"}
        )
        assert (good[0], rebound[0], foreign[0]) == (200, 421, 403)
    finally:
        server.stop()
