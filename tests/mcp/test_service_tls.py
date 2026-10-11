import asyncio
import subprocess
import sys

import pytest

from nailong_agent_sdk.mcp.client import McpClientManager
from nailong_agent_sdk.mcp.client_types import McpConnectionState, McpHttpServerConfig
from nailong_agent_sdk.mcp.security import ServerConfigurationError, load_http_service_settings
from tests.support.mcp_server import Server
from tests.support.processes import child_environment
from tests.support.tls import self_signed_certificate

TOKEN = "t" * 32
NON_LOOPBACK = {"AGENT_RUNTIME_HOST": "192.0.2.10", "AGENT_RUNTIME_ALLOWED_HOSTS": "192.0.2.10:*"}


def settings(**overrides):
    environ = {"AGENT_RUNTIME_AUTH_TOKEN": TOKEN, **overrides}
    return load_http_service_settings({k: v for k, v in environ.items() if v is not None})


def tls_environment(directory, password=None):
    certificate, key = self_signed_certificate(directory, password=password)
    environment = {
        "AGENT_RUNTIME_TLS_CERTFILE": str(certificate),
        "AGENT_RUNTIME_TLS_KEYFILE": str(key),
    }
    if password is not None:
        environment["AGENT_RUNTIME_TLS_KEY_PASSWORD"] = password
    return environment


def test_plain_http_is_the_default_scheme_and_adds_no_server_options():
    loaded = settings()
    assert loaded.scheme == "http" and loaded.ssl_options() == {}
    assert loaded.tls_certfile is None and loaded.tls_keyfile is None


def test_a_non_loopback_bind_without_tls_is_refused_unless_plain_http_is_allowed_explicitly():
    with pytest.raises(ServerConfigurationError) as refused:
        settings(**NON_LOOPBACK)
    message = str(refused.value)
    for needle in (
        'AGENT_RUNTIME_HOST "192.0.2.10"',
        "clear text",
        "AGENT_RUNTIME_TLS_CERTFILE",
        "AGENT_RUNTIME_TLS_KEYFILE",
        "AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1",
    ):
        assert needle in message, (needle, message)
    allowed = settings(**NON_LOOPBACK, AGENT_RUNTIME_ALLOW_PLAIN_HTTP="1")
    assert allowed.scheme == "http" and allowed.host == "192.0.2.10"
    for not_one in ("true", "0", "yes", ""):
        with pytest.raises(ServerConfigurationError, match="AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1"):
            settings(**NON_LOOPBACK, AGENT_RUNTIME_ALLOW_PLAIN_HTTP=not_one)


def test_the_allowed_host_list_is_still_asked_for_before_the_plain_http_refusal():
    with pytest.raises(ServerConfigurationError, match="AGENT_RUNTIME_ALLOWED_HOSTS"):
        settings(AGENT_RUNTIME_HOST="192.0.2.10")


def test_tls_makes_a_non_loopback_bind_acceptable_and_switches_the_scheme(tmp_path):
    environment = tls_environment(tmp_path)
    loaded = settings(**NON_LOOPBACK, **environment)
    assert loaded.scheme == "https"
    assert loaded.ssl_options() == {
        "ssl_certfile": environment["AGENT_RUNTIME_TLS_CERTFILE"],
        "ssl_keyfile": environment["AGENT_RUNTIME_TLS_KEYFILE"],
        "ssl_keyfile_password": None,
    }


def test_a_loopback_tls_bind_allows_https_origins_only(tmp_path):
    loaded = settings(**tls_environment(tmp_path))
    security = loaded.transport_security()
    assert "https://127.0.0.1:*" in security.allowed_origins
    assert not any(origin.startswith("http://") for origin in security.allowed_origins)


def test_the_certificate_and_the_key_must_be_set_together(tmp_path):
    environment = tls_environment(tmp_path)
    for dropped, kept in (
        ("AGENT_RUNTIME_TLS_KEYFILE", "AGENT_RUNTIME_TLS_CERTFILE"),
        ("AGENT_RUNTIME_TLS_CERTFILE", "AGENT_RUNTIME_TLS_KEYFILE"),
    ):
        partial = {name: value for name, value in environment.items() if name != dropped}
        with pytest.raises(ServerConfigurationError) as raised:
            settings(**partial)
        assert "must be set together" in str(raised.value)
        assert f"only {kept} is set" in str(raised.value)


def test_a_certificate_or_key_that_is_not_a_file_is_named_with_its_path(tmp_path):
    environment = tls_environment(tmp_path)
    for variable in ("AGENT_RUNTIME_TLS_CERTFILE", "AGENT_RUNTIME_TLS_KEYFILE"):
        missing = str(tmp_path / "nowhere" / "file.pem")
        with pytest.raises(ServerConfigurationError) as raised:
            settings(**{**environment, variable: missing})
        assert f'{variable} "{missing}" is not a file' in str(raised.value)


def test_a_certificate_that_cannot_be_loaded_is_refused_at_startup(tmp_path):
    certificate, key = self_signed_certificate(tmp_path)
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a certificate", "utf-8")
    with pytest.raises(ServerConfigurationError) as raised:
        settings(AGENT_RUNTIME_TLS_CERTFILE=str(garbage), AGENT_RUNTIME_TLS_KEYFILE=str(key))
    message = str(raised.value)
    assert "cannot be loaded" in message and str(garbage) in message and str(key) in message


def test_an_encrypted_key_needs_its_password_and_the_password_is_never_echoed(tmp_path):
    environment = tls_environment(tmp_path, password="correct horse battery staple")
    loaded = settings(**environment)
    assert loaded.ssl_options()["ssl_keyfile_password"] == "correct horse battery staple"
    assert "correct horse" not in repr(loaded)
    without = {k: v for k, v in environment.items() if k != "AGENT_RUNTIME_TLS_KEY_PASSWORD"}
    with pytest.raises(ServerConfigurationError, match="AGENT_RUNTIME_TLS_KEY_PASSWORD"):
        settings(**without)
    with pytest.raises(ServerConfigurationError) as wrong:
        settings(**{**environment, "AGENT_RUNTIME_TLS_KEY_PASSWORD": "wrong guess"})
    assert "AGENT_RUNTIME_TLS_KEY_PASSWORD" in str(wrong.value)
    assert "wrong guess" not in str(wrong.value)


@pytest.mark.parametrize("case", ["half-configured", "non-loopback-plain"])
def test_the_service_process_exits_with_status_2_for_these_tls_mistakes(tmp_path, case):
    environment = {"AGENT_RUNTIME_AUTH_TOKEN": TOKEN}
    if case == "half-configured":
        environment["AGENT_RUNTIME_TLS_CERTFILE"] = str(tmp_path / "service.pem")
        expected = "must be set together"
    else:
        environment.update(NON_LOOPBACK)
        expected = "AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1"
    completed = subprocess.run(
        [sys.executable, "-m", "nailong_agent_sdk.mcp.server"],
        env=child_environment(environment),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 2, completed.stderr
    assert expected in completed.stderr and "Traceback" not in completed.stderr


@pytest.fixture
def trusted_certificate(tmp_path, monkeypatch):
    certificate, key = self_signed_certificate(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    return certificate, key


def test_a_service_started_with_tls_answers_tool_calls_over_https(tmp_path, trusted_certificate):
    base = tmp_path / "service"
    base.mkdir()
    server = Server(base, tls_files=trusted_certificate)
    try:
        assert server.url.startswith("https://127.0.0.1:")
        reply = server.call("list_metric_definitions")
        assert reply["ok"] is True
    finally:
        server.stop()


def test_the_outbound_client_connects_to_a_tls_service_and_a_plain_client_cannot(
    tmp_path, trusted_certificate
):
    base = tmp_path / "service"
    base.mkdir()
    server = Server(base, tls_files=trusted_certificate)
    try:

        async def scenario():
            secure = McpHttpServerConfig(name="secure", url=server.url, headers=server.auth_headers)
            plain = McpHttpServerConfig(
                name="plain",
                url=server.url.replace("https://", "http://"),
                headers=server.auth_headers,
                connect_timeout_seconds=10,
            )
            manager = McpClientManager([secure, plain])
            try:
                await manager.connect_all()
                return {status.name: status for status in manager.list_statuses()}
            finally:
                await manager.close()

        statuses = asyncio.run(asyncio.wait_for(scenario(), 120))
        assert statuses["secure"].state is McpConnectionState.CONNECTED, statuses["secure"].detail
        assert len(statuses["secure"].tools) >= 40
        assert statuses["plain"].state is McpConnectionState.FAILED
        assert server.token not in (statuses["plain"].detail or "")
    finally:
        server.stop()
