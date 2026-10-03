# Copyright (c) 2026 David Michael Indraputra

"""Resolve a declared ``ModelBinding`` into a live ``AgentModel``.

An ``AgentDefinition`` names its model only as data (provider, model, parameters,
fallbacks). A host that runs definitions it did not construct itself, such as
the MCP server, needs to turn that data into adapters. ``ModelResolver`` does so
from provider endpoints the host configured explicitly, and never invents a
provider: a binding whose provider is not configured is a typed error.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from ..foundations.contracts import FallbackModelBinding, ModelBinding
from ..foundations.errors import AgentSdkError
from .model import AgentModel, FailoverAgentModel
from .openai_compatible import OpenAICompatibleAgentModel, OpenAICompatibleEndpoint

AdapterFactory = Callable[[FallbackModelBinding], AgentModel]

ENV_PROVIDERS = "NAILONG_MODEL_PROVIDERS"
ENV_PREFIX = "NAILONG_MODEL_PROVIDER_"


class ModelResolver:
    """Build model adapters for bindings from host-configured providers.

    ``endpoints`` maps a provider name to an OpenAI-compatible endpoint.
    ``factories`` maps a provider name to a callable that builds any other
    ``AgentModel`` (for example a native SDK adapter); a factory wins over an
    endpoint of the same name.
    """

    def __init__(
        self,
        endpoints: Mapping[str, OpenAICompatibleEndpoint] | None = None,
        *,
        factories: Mapping[str, AdapterFactory] | None = None,
    ) -> None:
        self._endpoints = dict(endpoints or {})
        self._factories = dict(factories or {})

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._endpoints) | set(self._factories)))

    def resolve(self, binding: ModelBinding) -> AgentModel:
        """Return one adapter, or a ``FailoverAgentModel`` over primary + fallbacks."""

        chain: list[FallbackModelBinding] = [binding, *binding.fallbacks]
        adapters = [self._adapter(item) for item in chain]
        if len(adapters) == 1:
            return adapters[0]
        return FailoverAgentModel(
            adapters,
            bindings=[
                ModelBinding(provider=item.provider, model=item.model, parameters=item.parameters)
                for item in chain
            ],
        )

    def _adapter(self, binding: FallbackModelBinding) -> AgentModel:
        if factory := self._factories.get(binding.provider):
            return factory(binding)
        if endpoint := self._endpoints.get(binding.provider):
            return OpenAICompatibleAgentModel(
                endpoint, provider=binding.provider, model=binding.model
            )
        configured = ", ".join(self.providers) or "none"
        raise AgentSdkError(
            "MODEL_PROVIDER_NOT_CONFIGURED",
            f'No model provider named "{binding.provider}" is configured for this host '
            f"(configured: {configured}). Configure it with ModelResolver(endpoints=...) or, "
            f"for the MCP server, the {ENV_PROVIDERS} environment variables.",
            {"provider": binding.provider, "configured_providers": list(self.providers)},
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> ModelResolver:
        """Read providers a server operator declared in its environment.

        ``NAILONG_MODEL_PROVIDERS`` lists provider names, comma separated. For each
        name ``X`` (upper-cased, ``-`` as ``_``):

        - ``NAILONG_MODEL_PROVIDER_X_BASE_URL`` (required)
        - ``NAILONG_MODEL_PROVIDER_X_API_KEY_ENV``: the *name* of the variable that
          holds the key (required; the key itself is never put in this table)
        - ``NAILONG_MODEL_PROVIDER_X_TIMEOUT_SECONDS`` (optional, default 120)
        """

        env = os.environ if environ is None else environ
        endpoints: dict[str, OpenAICompatibleEndpoint] = {}
        for raw_name in env.get(ENV_PROVIDERS, "").split(","):
            name = raw_name.strip()
            if not name:
                continue
            key = ENV_PREFIX + name.upper().replace("-", "_")
            base_url = env.get(f"{key}_BASE_URL", "").strip()
            key_variable = env.get(f"{key}_API_KEY_ENV", "").strip()
            if not base_url or not key_variable:
                raise AgentSdkError(
                    "MODEL_PROVIDER_CONFIG_INVALID",
                    f'Provider "{name}" needs {key}_BASE_URL and {key}_API_KEY_ENV.',
                )
            api_key = env.get(key_variable, "").strip()
            if not api_key:
                raise AgentSdkError(
                    "MODEL_PROVIDER_CONFIG_INVALID",
                    f'Provider "{name}" reads its key from {key_variable}, which is not set.',
                )
            timeout = float(env.get(f"{key}_TIMEOUT_SECONDS", "120"))
            endpoints[name] = OpenAICompatibleEndpoint(
                base_url=base_url, api_key=api_key, timeout_seconds=timeout
            )
        return cls(endpoints)
