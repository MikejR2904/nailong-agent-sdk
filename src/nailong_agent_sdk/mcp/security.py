# Copyright (c) 2026 David Michael Indraputra

"""Authentication and request-origin checks for the HTTP form of the runtime server."""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
MIN_TOKEN_LENGTH = 32
LOOPBACK_HOST_PATTERNS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


class ServerConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class HttpServiceSettings:
    host: str
    port: int
    token: str = field(repr=False)
    allowed_hosts: tuple[str, ...]

    def transport_security(self) -> TransportSecuritySettings:
        origins = [f"http://{pattern}" for pattern in self.allowed_hosts]
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
    return HttpServiceSettings(host=host, port=port, token=token, allowed_hosts=allowed)


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
        if scope["type"] == "http" and not self._authorized(scope):
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
