# Copyright (c) 2026 David Michael Indraputra

"""Authentication and request-origin checks for the HTTP form of the runtime server."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
MIN_TOKEN_LENGTH = 32
MAX_DISCARDED_BODY_BYTES = 65_536
DISCARD_BODY_SECONDS = 1.0
LOOPBACK_HOST_PATTERNS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
AUDIT_MAX_OPEN_HANDLES_VARIABLE = "AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES"


class ServerConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class HttpServiceSettings:
    host: str
    port: int
    token: str = field(repr=False)
    allowed_hosts: tuple[str, ...]
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_key_password: str | None = field(default=None, repr=False)
    audit_max_open_handles: int | None = None

    @property
    def scheme(self) -> str:
        return "https" if self.tls_certfile is not None else "http"

    def ssl_options(self) -> dict[str, str | None]:
        if self.tls_certfile is None:
            return {}
        return {
            "ssl_certfile": self.tls_certfile,
            "ssl_keyfile": self.tls_keyfile,
            "ssl_keyfile_password": self.tls_key_password,
        }

    def transport_security(self) -> TransportSecuritySettings:
        origins = [f"{self.scheme}://{pattern}" for pattern in self.allowed_hosts]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(self.allowed_hosts),
            allowed_origins=origins if is_loopback_host(self.host) else [],
        )


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_http_service_settings(environ: Mapping[str, str]) -> HttpServiceSettings:
    host = environ.get("AGENT_RUNTIME_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ServerConfigurationError("AGENT_RUNTIME_HOST must not be empty.")
    port = _parse_port(environ.get("AGENT_RUNTIME_PORT", str(DEFAULT_PORT)))
    token = environ.get("AGENT_RUNTIME_AUTH_TOKEN", "")
    if len(token) < MIN_TOKEN_LENGTH or any(character.isspace() for character in token):
        raise ServerConfigurationError(
            f"AGENT_RUNTIME_AUTH_TOKEN must be set to a secret of at least {MIN_TOKEN_LENGTH} "
            "characters without whitespace; the runtime serves approval and decision tools "
            "and does not start unauthenticated. Generate one with "
            'python -c "import secrets; print(secrets.token_urlsafe(32))".'
        )
    if is_loopback_host(host):
        bracketed = f"[{host}]" if ":" in host else host
        allowed = tuple(dict.fromkeys((*LOOPBACK_HOST_PATTERNS, f"{bracketed}:*")))
    else:
        allowed = _parse_allowed_hosts(host, environ.get("AGENT_RUNTIME_ALLOWED_HOSTS", ""))
    certificate, key, password = _parse_tls(environ)
    if (
        certificate is None
        and not is_loopback_host(host)
        and environ.get("AGENT_RUNTIME_ALLOW_PLAIN_HTTP") != "1"
    ):
        raise ServerConfigurationError(
            f'AGENT_RUNTIME_HOST "{host}" is not a loopback address and no TLS certificate is '
            "configured, so the bearer token and every approval decision would cross the "
            "network in clear text; set AGENT_RUNTIME_TLS_CERTFILE and "
            "AGENT_RUNTIME_TLS_KEYFILE, or set AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1 if a proxy that "
            "terminates TLS in front of this service carries the traffic."
        )
    return HttpServiceSettings(
        host=host,
        port=port,
        token=token,
        allowed_hosts=allowed,
        tls_certfile=certificate,
        tls_keyfile=key,
        tls_key_password=password,
        audit_max_open_handles=_parse_audit_max_open_handles(environ),
    )


def _parse_audit_max_open_handles(environ: Mapping[str, str]) -> int | None:
    raw = environ.get(AUDIT_MAX_OPEN_HANDLES_VARIABLE, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ServerConfigurationError(
            f'{AUDIT_MAX_OPEN_HANDLES_VARIABLE} must be a whole number of at least 1, got "{raw}".'
        )
    return value


def _parse_tls(environ: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
    certificate = environ.get("AGENT_RUNTIME_TLS_CERTFILE", "").strip()
    key = environ.get("AGENT_RUNTIME_TLS_KEYFILE", "").strip()
    password = environ.get("AGENT_RUNTIME_TLS_KEY_PASSWORD") or None
    if not certificate and not key:
        return None, None, None
    if not certificate or not key:
        present = "AGENT_RUNTIME_TLS_CERTFILE" if certificate else "AGENT_RUNTIME_TLS_KEYFILE"
        raise ServerConfigurationError(
            "AGENT_RUNTIME_TLS_CERTFILE and AGENT_RUNTIME_TLS_KEYFILE must be set together; "
            f"only {present} is set."
        )
    for variable, path in (
        ("AGENT_RUNTIME_TLS_CERTFILE", certificate),
        ("AGENT_RUNTIME_TLS_KEYFILE", key),
    ):
        if not Path(path).is_file():
            raise ServerConfigurationError(f'{variable} "{path}" is not a file.')
    try:
        ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(
            certificate, key, password if password is not None else (lambda: b"")
        )
    except (ssl.SSLError, OSError, ValueError) as error:
        hint = (
            " If the key is encrypted, set AGENT_RUNTIME_TLS_KEY_PASSWORD to its password."
            if "password" in str(error).lower() or password is not None or _is_encrypted(key)
            else ""
        )
        raise ServerConfigurationError(
            f'The TLS certificate "{certificate}" and key "{key}" cannot be loaded '
            f"({type(error).__name__}: {error}).{hint}"
        ) from error
    return certificate, key, password


def _is_encrypted(key_file: str) -> bool:
    try:
        return b"ENCRYPTED" in Path(key_file).read_bytes()[:200]
    except OSError:
        return False


def _parse_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65_535:
        raise ServerConfigurationError(
            f'AGENT_RUNTIME_PORT must be an integer from 1 to 65535, got "{raw}".'
        )
    return port


def _parse_allowed_hosts(host: str, raw: str) -> tuple[str, ...]:
    entries = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not entries:
        raise ServerConfigurationError(
            f'AGENT_RUNTIME_HOST "{host}" is not a loopback address; list the Host header '
            "values clients will send in AGENT_RUNTIME_ALLOWED_HOSTS, comma separated "
            '(for example "runtime.internal:8001").'
        )
    for entry in entries:
        if "://" in entry or "/" in entry or any(character.isspace() for character in entry):
            raise ServerConfigurationError(
                f'AGENT_RUNTIME_ALLOWED_HOSTS entry "{entry}" must be a bare host or host:port '
                "without a scheme or path."
            )
    return entries


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "websocket" and not self._authorized(scope):
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind == "http" and not self._authorized(scope):
            await _discard_request_body(receive)
            response = JSONResponse(
                {
                    "ok": False,
                    "errors": [
                        {
                            "message": 'missing or invalid bearer token; send "Authorization: '
                            'Bearer <AGENT_RUNTIME_AUTH_TOKEN>".',
                            "type": "Unauthorized",
                        }
                    ],
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        scheme, _, presented = Headers(scope=scope).get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(presented.strip().encode("utf-8"), self._token)


async def _discard_request_body(receive: Receive) -> None:
    remaining = MAX_DISCARDED_BODY_BYTES
    try:
        async with asyncio.timeout(DISCARD_BODY_SECONDS):
            while remaining > 0:
                message = await receive()
                if message["type"] != "http.request":
                    return
                remaining -= len(message.get("body", b""))
                if not message.get("more_body", False):
                    return
    except TimeoutError:
        return
