"""Model resolution for host-run definitions, and per-adapter bindings in failover."""

from __future__ import annotations

import asyncio
import json

import pytest

from nailong_agent_sdk import (
    FailoverAgentModel,
    ModelBinding,
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.agent.model import ModelContext
from nailong_agent_sdk.agent.model_resolver import ModelResolver
from nailong_agent_sdk.foundations.contracts import (
    AgentPrompt,
    FallbackModelBinding,
    ScopedAgentTask,
    TaskScope,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from nailong_agent_sdk.state.project_state_models import ProjectStateView


class _Transport:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.payloads = reply, error, []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.reply


FINAL = {
    "choices": [
        {"message": {"content": json.dumps({"type": "final", "output": {"status": "complete"}})}}
    ]
}


def _context(binding: ModelBinding) -> ModelContext:
    task = ScopedAgentTask(
        id="t1",
        input={},
        scope=TaskScope(label="s"),
        locked_interface=None,
        instructions="do it",
        acceptance_criteria=["done"],
    )
    return ModelContext(
        task=task,
        prompt=AgentPrompt(sections=[]),
        iteration=1,
        project_state=ProjectStateView.model_construct(),
        observations=(),
        episodes=(),
        model_binding=binding,
        output_schema={"type": "object"},
    )


def _adapter(provider, model, transport):
    endpoint = OpenAICompatibleEndpoint(base_url="https://example.com/v1", api_key="k")
    return OpenAICompatibleAgentModel(endpoint, provider=provider, model=model, transport=transport)


def test_failover_hands_each_adapter_its_own_binding():
    primary = _Transport(error=TransientProviderError("MODEL_PROVIDER_TRANSIENT", "HTTP 503"))
    fallback = _Transport(reply=FINAL)
    binding = ModelBinding(
        provider="fireworks",
        model="primary-model",
        fallbacks=[FallbackModelBinding(provider="fireworks", model="fallback-model")],
    )
    model = FailoverAgentModel(
        [
            _adapter("fireworks", "primary-model", primary),
            _adapter("fireworks", "fallback-model", fallback),
        ],
        bindings=[
            ModelBinding(provider="fireworks", model="primary-model"),
            ModelBinding(provider="fireworks", model="fallback-model"),
        ],
        max_retries_per_model=0,
    )
    result = asyncio.run(model.next_turn(_context(binding)))
    assert result.turn.type == "final"
    assert fallback.payloads[0]["model"] == "fallback-model"


def test_failover_without_bindings_reproduces_the_old_mismatch():
    """Before the fix, a fallback adapter always saw the primary binding and refused it."""

    primary = _Transport(error=TransientProviderError("MODEL_PROVIDER_TRANSIENT", "HTTP 503"))
    fallback = _Transport(reply=FINAL)
    binding = ModelBinding(provider="fireworks", model="primary-model")
    model = FailoverAgentModel(
        [
            _adapter("fireworks", "primary-model", primary),
            _adapter("fireworks", "fallback-model", fallback),
        ],
        max_retries_per_model=0,
    )
    with pytest.raises(AgentSdkError, match="MODEL_BINDING_MISMATCH|does not match"):
        asyncio.run(model.next_turn(_context(binding)))
    assert fallback.payloads == []


def test_resolver_builds_failover_chain_with_bindings():
    endpoint = OpenAICompatibleEndpoint(base_url="https://example.com/v1", api_key="k")
    resolver = ModelResolver({"fireworks": endpoint})
    single = resolver.resolve(ModelBinding(provider="fireworks", model="a"))
    assert isinstance(single, OpenAICompatibleAgentModel)
    chained = resolver.resolve(
        ModelBinding(
            provider="fireworks",
            model="a",
            fallbacks=[FallbackModelBinding(provider="fireworks", model="b")],
        )
    )
    assert isinstance(chained, FailoverAgentModel)
    assert [b.model for b in chained._bindings] == ["a", "b"]


def test_unconfigured_provider_is_a_typed_error_naming_what_exists():
    resolver = ModelResolver(factories={"local": lambda binding: object()})
    with pytest.raises(AgentSdkError) as raised:
        resolver.resolve(ModelBinding(provider="fireworks", model="m"))
    assert raised.value.code == "MODEL_PROVIDER_NOT_CONFIGURED"
    assert "local" in raised.value.message


def test_from_environment_reads_key_by_reference():
    env = {
        "NAILONG_MODEL_PROVIDERS": "fireworks, my-local",
        "NAILONG_MODEL_PROVIDER_FIREWORKS_BASE_URL": "https://api.fireworks.ai/inference/v1",
        "NAILONG_MODEL_PROVIDER_FIREWORKS_API_KEY_ENV": "FIREWORKS_API_KEY",
        "FIREWORKS_API_KEY": "secret",
        "NAILONG_MODEL_PROVIDER_MY_LOCAL_BASE_URL": "https://local.example/v1",
        "NAILONG_MODEL_PROVIDER_MY_LOCAL_API_KEY_ENV": "LOCAL_KEY",
        "LOCAL_KEY": "k2",
        "NAILONG_MODEL_PROVIDER_MY_LOCAL_TIMEOUT_SECONDS": "15",
    }
    resolver = ModelResolver.from_environment(env)
    assert resolver.providers == ("fireworks", "my-local")
    assert resolver._endpoints["my-local"].timeout_seconds == 15
    with pytest.raises(AgentSdkError, match="FIREWORKS_API_KEY, which is not set"):
        ModelResolver.from_environment({**env, "FIREWORKS_API_KEY": ""})
    assert ModelResolver.from_environment({}).providers == ()
