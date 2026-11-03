# Copyright (c) 2026 David Michael Indraputra

"""Optional host-injected adapter for the Anthropic Messages API.

Like the OpenAI-compatible adapter it has no ambient credential lookup and no default model:
the host supplies the key and the exact model identifier. Responses are untrusted data
validated against the SDK's typed contracts before a caller can use them.
"""

from __future__ import annotations

from .endpoint import DEFAULT_API_VERSION, DEFAULT_BASE_URL, AnthropicEndpoint
from .messages import DEFAULT_MAX_TOKENS, AnthropicMessagesAgentModel

__all__ = [
    "DEFAULT_API_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_TOKENS",
    "AnthropicEndpoint",
    "AnthropicMessagesAgentModel",
]
