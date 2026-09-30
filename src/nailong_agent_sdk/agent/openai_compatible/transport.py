# Copyright (c) 2026 David Michael Indraputra

"""JSON HTTP transports and endpoint configuration for OpenAI-compatible providers.

``UrlLibJsonTransport`` is the default, dependency-free blocking transport.
``HttpxStreamingJsonTransport`` is a separate, opt-in transport used only when
a caller wants incremental deltas from ``OpenAICompatibleAgentModel.stream_turn``;
it depends on ``httpx`` (already a project dependency for the MCP client)
because streaming server-sent events correctly over plain ``urllib`` requires
hand-rolling a blocking-iterator-to-async-loop bridge that a real async HTTP
client already does correctly.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import httpx

from ...foundations.errors import AgentSdkError, TransientProviderError

# 429 (rate limited) and 5xx (server-side) are conventionally safe to retry;
# other 4xx codes reflect a request the server has already rejected on its
# merits, so retrying it unchanged would only reproduce the same rejection.
_RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class JsonHttpTransport(Protocol):
    """A blocking JSON transport that a host may replace in tests or deployment."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class StreamingJsonHttpTransport(Protocol):
    """An async transport that yields one decoded JSON object per server-sent chunk."""

    def stream_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, Any]]: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    """Treat redirects as provider failures so bearer credentials stay endpoint-bound."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class UrlLibJsonTransport:
    """Minimal standard-library transport for an endpoint selected by the host."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, method="POST", headers=dict(headers))
        try:
            with build_opener(_NoRedirectHandler()).open(
                request, timeout=timeout_seconds
            ) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise _http_status_error(
                error.code, retry_after_seconds=_parse_retry_after(error.headers.get("Retry-After"))
            ) from error
        except URLError as error:
            raise TransientProviderError(
                "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
                "OpenAI-compatible provider could not be reached.",
            ) from error
        except TimeoutError as error:
            raise TransientProviderError(
                "OPENAI_COMPATIBLE_TRANSPORT_TIMEOUT",
                f"OpenAI-compatible provider did not respond within {timeout_seconds}s.",
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                "OpenAI-compatible provider returned malformed JSON.",
            ) from error
        if not isinstance(decoded, Mapping):
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                "OpenAI-compatible provider response must be a JSON object.",
            )
        return decoded


class HttpxJsonTransport:
    """Blocking JSON transport over one persistent, connection-pooled httpx.Client.

    UrlLibJsonTransport calls build_opener(...).open(...) fresh on every
    post_json call, so every turn pays a new TCP+TLS handshake to the same
    host instead of reusing one. This keeps one httpx.Client (and its
    keep-alive connection pool) alive across calls; a caller that constructs
    one instance and passes it to OpenAICompatibleAgentModel once reuses the
    same connection for that model's whole turn loop.
    """

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            response = self._client.post(
                url, headers=dict(headers), content=body, timeout=timeout_seconds
            )
            if response.status_code >= 300:
                raise _http_status_error(
                    response.status_code,
                    retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")),
                )
            decoded = response.json()
        except httpx.TimeoutException as error:
            raise TransientProviderError(
                "OPENAI_COMPATIBLE_TRANSPORT_TIMEOUT",
                f"OpenAI-compatible provider did not respond within {timeout_seconds}s.",
            ) from error
        except httpx.TransportError as error:
            raise TransientProviderError(
                "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
                "OpenAI-compatible provider could not be reached.",
            ) from error
        except json.JSONDecodeError as error:
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                "OpenAI-compatible provider returned malformed JSON.",
            ) from error
        if not isinstance(decoded, Mapping):
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                "OpenAI-compatible provider response must be a JSON object.",
            )
        return decoded

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class HttpxStreamingJsonTransport:
    """Stream server-sent JSON chunks over httpx's native async HTTP client.

    Kept separate from ``UrlLibJsonTransport`` rather than added to it: the
    non-streaming path stays dependency-free, and a caller that never streams
    never imports or constructs this class.
    """

    async def stream_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, Any]]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            async with (
                httpx.AsyncClient(timeout=timeout_seconds) as client,
                client.stream("POST", url, headers=dict(headers), content=body) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    raise _http_status_error(
                        response.status_code,
                        retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")),
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        yield parsed
        except httpx.TransportError as error:
            raise TransientProviderError(
                "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
                "OpenAI-compatible provider could not be reached.",
            ) from error


def _http_status_error(status_code: int, *, retry_after_seconds: float | None) -> AgentSdkError:
    """Classify one HTTP failure status as transient (retryable) or terminal."""

    message = f"OpenAI-compatible provider returned HTTP {status_code}."
    if status_code in _RETRYABLE_HTTP_STATUS_CODES:
        return TransientProviderError(
            "OPENAI_COMPATIBLE_HTTP_ERROR", message, retry_after_seconds=retry_after_seconds
        )
    return AgentSdkError("OPENAI_COMPATIBLE_HTTP_ERROR", message)


def _parse_retry_after(raw_value: str | None) -> float | None:
    """Return the server-declared retry delay in seconds, or ``None`` if absent/unparseable.

    ``Retry-After`` may also be an HTTP-date; that form is intentionally not
    parsed here, since a caller without a value falls back to its own bounded
    exponential backoff instead.
    """

    if raw_value is None:
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return None


@dataclass(frozen=True)
class OpenAICompatibleEndpoint:
    """Host-owned connection data for one OpenAI-compatible API endpoint."""

    base_url: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 30.0
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("OpenAI-compatible base_url must use HTTP(S).")
        if self.base_url.startswith("http://") and not self.allow_insecure_http:
            raise ValueError(
                "OpenAI-compatible base_url must use HTTPS unless allow_insecure_http is explicit."
            )
        if not self.api_key.strip():
            raise ValueError("OpenAI-compatible api_key must be non-empty.")
        if self.timeout_seconds <= 0:
            raise ValueError("OpenAI-compatible timeout_seconds must be positive.")

    def url_for(self, suffix: str) -> str:
        return f"{self.base_url.rstrip('/')}{suffix}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
