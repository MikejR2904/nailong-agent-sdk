# Copyright (c) 2026 David Michael Indraputra

"""Host-owned connection data for the Anthropic Messages API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...observability.trace_context import TRACEPARENT_HEADER

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_API_VERSION = "2023-06-01"
_OWNED_HEADERS = frozenset({"x-api-key", "anthropic-version", "content-type"})


@dataclass(frozen=True)
class AnthropicEndpoint:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    api_version: str = DEFAULT_API_VERSION
    timeout_seconds: float = 60.0
    allow_insecure_http: bool = False
    propagate_trace_context: bool = False
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Anthropic base_url must use HTTP(S).")
        if self.base_url.startswith("http://") and not self.allow_insecure_http:
            raise ValueError(
                "Anthropic base_url must use HTTPS unless allow_insecure_http is explicit."
            )
        cleaned_key = self.api_key.strip()
        if not cleaned_key:
            raise ValueError("Anthropic api_key must be non-empty.")
        if any(character.isspace() or not character.isprintable() for character in cleaned_key):
            raise ValueError(
                "Anthropic api_key must not contain whitespace or control characters "
                "inside the key."
            )
        object.__setattr__(self, "api_key", cleaned_key)
        if not self.api_version.strip():
            raise ValueError("Anthropic api_version must be non-empty.")
        if self.timeout_seconds <= 0:
            raise ValueError("Anthropic timeout_seconds must be positive.")
        owned = sorted(name for name in self.extra_headers if name.lower() in _OWNED_HEADERS)
        if owned:
            raise ValueError(
                f"Anthropic extra_headers may not set {owned}: the endpoint sets the key, the "
                "version and the content type itself."
            )

    def url_for(self, suffix: str) -> str:
        return f"{self.base_url.rstrip('/')}{suffix}"

    def request_headers(self, trace_parent: str | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
            **self.extra_headers,
        }
        if self.propagate_trace_context and trace_parent:
            headers[TRACEPARENT_HEADER] = trace_parent
        return headers
