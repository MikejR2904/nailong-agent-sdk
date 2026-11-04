# Copyright (c) 2026 David Michael Indraputra

"""Name a model once: the adapter and the binding the agent definition needs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..agent.anthropic_messages import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_TOKENS,
    AnthropicEndpoint,
    AnthropicMessagesAgentModel,
)
from ..agent.model import AgentModel
from ..agent.openai_compatible import OpenAICompatibleAgentModel, OpenAICompatibleEndpoint
from ..foundations.contracts import FallbackModelBinding

OPENAI_COMPATIBLE_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "lmstudio": "http://localhost:1234/v1",
    "llamacpp": "http://localhost:8080/v1",
}


@dataclass(frozen=True)
class ModelSetup:
    model: AgentModel
    provider: str
    name: str
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def binding(self) -> FallbackModelBinding:
        return FallbackModelBinding(
            provider=self.provider, model=self.name, parameters=dict(self.parameters)
        )


def default_text_answer(text: str) -> dict[str, Any]:
    return {"status": "complete", "answer": text}


def openai_chat(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    provider: str = "openai-compatible",
    timeout_seconds: float = 60.0,
    allow_insecure_http: bool = False,
    json_mode: bool = True,
    parameters: dict[str, Any] | None = None,
    text_answer: Callable[[str], dict[str, Any]] | None = default_text_answer,
    tool_mode: str = "auto",
) -> ModelSetup:
    endpoint = OpenAICompatibleEndpoint(
        base_url=base_url,
        api_key=api_key or "",
        timeout_seconds=timeout_seconds,
        allow_insecure_http=allow_insecure_http,
        require_api_key=api_key is not None,
    )
    adapter = OpenAICompatibleAgentModel(
        endpoint,
        provider=provider,
        model=model,
        json_mode=json_mode,
        text_answer=text_answer,
        tool_mode=tool_mode,
    )
    return ModelSetup(adapter, provider, model, dict(parameters or {}))


def anthropic_messages(
    *,
    api_key: str,
    model: str,
    provider: str = "anthropic",
    base_url: str = DEFAULT_BASE_URL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_seconds: float = 120.0,
    allow_insecure_http: bool = False,
    cache_stable_prefix: bool = True,
    extra_headers: dict[str, str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> ModelSetup:
    endpoint = AnthropicEndpoint(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        allow_insecure_http=allow_insecure_http,
        extra_headers=dict(extra_headers or {}),
    )
    adapter = AnthropicMessagesAgentModel(
        endpoint,
        provider=provider,
        model=model,
        default_max_tokens=max_tokens,
        cache_stable_prefix=cache_stable_prefix,
    )
    return ModelSetup(adapter, provider, model, dict(parameters or {}))
