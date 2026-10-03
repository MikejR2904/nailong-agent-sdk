# Copyright (c) 2026 David Michael Indraputra

"""Optional LangChain adapters with SDK-owned validation and tool authority.

LangChain middleware and tools are integration mechanisms, not the security
reference monitor.  Every model turn is parsed into the SDK's typed contract and
every framework tool facade delegates to a host-owned ``ToolExecutor`` after a
local preflight.  No framework callback holds an SDK capability or approval.

Reference: https://docs.langchain.com/oss/python/langchain/middleware/custom
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from pydantic import TypeAdapter

from ..agent.base_agent import BaseAgent
from ..agent.model import ModelContext, ModelTurnResponse
from ..foundations.contracts import AgentResult, AgentTurn, ScopedAgentTask, ToolDefinition
from ..foundations.errors import redact_secrets
from ..tools.tools import ToolExecutor, ToolInvocationContext
from ._utils import require_optional_module
from .contracts import assert_sanitized_interop_value

_AGENT_TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)


class AsyncLangChainRunnable(Protocol):
    """Small structural surface shared by LangChain Runnables and chat models."""

    async def ainvoke(
        self,
        input: Any,
        config: Mapping[str, Any] | None = None,
    ) -> Any: ...


LangChainPromptProjector = Callable[[ModelContext], Any]
LangChainTurnParser = Callable[[Any, ModelContext], AgentTurn | ModelTurnResponse]
LangChainAgentFactory = Callable[[ScopedAgentTask], BaseAgent]
LangChainToolPreflight = Callable[[ToolInvocationContext], Awaitable[Any | None]]
LangChainToolInvocationFactory = Callable[[dict[str, Any]], ToolInvocationContext]


class LangChainAgentModelAdapter:
    """Use an injected LangChain Runnable as an SDK model adapter.

    The host supplies both a least-privilege prompt projector and a typed output
    parser.  The adapter never infers a tool request from free-form text.
    """

    def __init__(
        self,
        runnable: AsyncLangChainRunnable,
        prompt_projector: LangChainPromptProjector,
        turn_parser: LangChainTurnParser,
        *,
        config_factory: (Callable[[ModelContext], Mapping[str, Any] | None] | None) = None,
    ) -> None:
        self._runnable = runnable
        self._prompt_projector = prompt_projector
        self._turn_parser = turn_parser
        self._config_factory = config_factory

    async def next_turn(self, context: ModelContext) -> AgentTurn | ModelTurnResponse:
        prompt = self._prompt_projector(context)
        _assert_safe_langchain_prompt(prompt)
        configuration = self._config_factory(context) if self._config_factory else None
        if configuration is not None:
            assert_sanitized_interop_value(dict(configuration))
        response = await self._runnable.ainvoke(prompt, configuration)
        parsed = self._turn_parser(response, context)
        if isinstance(parsed, ModelTurnResponse):
            return parsed
        return _AGENT_TURN_ADAPTER.validate_python(parsed)


class LangChainSdkRunnable:
    """Expose a host-constructed SDK run as a LangChain-compatible async Runnable.

    The framework receives the run outcome only (status, output, reason,
    failure, escalation), with secrets redacted. Prompt context, project state,
    episodes, lifecycle events and the profile stay with the host, which reads
    them from its own telemetry and audit stores. Set ``include_diagnostics`` to
    forward them too, still redacted.
    """

    def __init__(
        self, agent_factory: LangChainAgentFactory, *, include_diagnostics: bool = False
    ) -> None:
        self._agent_factory = agent_factory
        self._include_diagnostics = include_diagnostics

    async def ainvoke(
        self,
        input: ScopedAgentTask | Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del config  # Host construction, not framework config, owns runtime authority.
        task = (
            input if isinstance(input, ScopedAgentTask) else ScopedAgentTask.model_validate(input)
        )
        result = await self._agent_factory(task).run(task)
        return _agent_result_projection(result, include_diagnostics=self._include_diagnostics)

    def as_runnable(self) -> Any:
        """Return a real optional ``RunnableLambda`` without making it a base dependency."""

        module = require_optional_module("langchain_core.runnables", "langchain")
        return module.RunnableLambda(self.ainvoke)


class LangChainSdkToolFacade:
    """A schema-only LangChain tool that re-enters the SDK's governed tool boundary.

    An embedding application should use ``HarnessToolExecutor`` or another executor
    that performs policy, capability, approval, quota, and idempotency checks.  The
    optional preflight provides a second host-owned check before executor dispatch.
    """

    def __init__(
        self,
        tool: ToolDefinition,
        executor: ToolExecutor,
        invocation_factory: LangChainToolInvocationFactory,
        *,
        preflight: LangChainToolPreflight | None = None,
    ) -> None:
        self._tool = tool
        self._executor = executor
        self._invocation_factory = invocation_factory
        self._preflight = preflight

    async def ainvoke(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(arguments)
        invocation = self._invocation_factory(payload)
        self._validate_invocation(invocation, payload)
        if self._preflight is not None:
            preflight_result = self._preflight(invocation)
            if inspect.isawaitable(preflight_result):
                preflight_result = await preflight_result
            if preflight_result is not None:
                return _result_mapping(preflight_result)
        result = await self._executor.execute(self._tool, invocation)
        payload = result.model_dump(mode="json")
        payload.pop("failure", None)
        return payload

    def as_tool(self) -> Any:
        """Build a ``StructuredTool`` with the declared SDK JSON schema unchanged."""

        module = require_optional_module("langchain_core.tools", "langchain")

        async def call(**kwargs: Any) -> dict[str, Any]:
            """Dispatch only through the SDK-owned executor."""

            return await self.ainvoke(kwargs)

        return module.StructuredTool.from_function(
            coroutine=call,
            name=self._tool.name,
            description=self._tool.description,
            args_schema=self._tool.input_schema,
            infer_schema=False,
        )

    def _validate_invocation(
        self,
        invocation: ToolInvocationContext,
        payload: dict[str, Any],
    ) -> None:
        if invocation.call.name != self._tool.name:
            raise ValueError(
                "LangChain tool facade invocation name does not match its declared SDK tool."
            )
        if invocation.call.arguments != payload:
            raise ValueError(
                "LangChain tool facade invocation arguments differ from the dispatched payload."
            )


def parse_structured_sdk_turn(response: Any, _: ModelContext) -> AgentTurn:
    """Parse only a JSON-like structured SDK turn; prose/tool inference is disallowed."""

    if isinstance(response, Mapping):
        return _AGENT_TURN_ADAPTER.validate_python(dict(response))
    if hasattr(response, "model_dump"):
        return _AGENT_TURN_ADAPTER.validate_python(response.model_dump(mode="json"))
    raise TypeError("LangChain model response must be a structured SDK AgentTurn mapping.")


def _assert_safe_langchain_prompt(prompt: Any) -> None:
    """Allow one bounded system/user chat pair without accepting ambient history.

    Generic interop projections reject ``messages`` because external state hand-offs
    must never contain a transcript. A LangChain chat model, however, commonly
    requires a system/user pair. This adapter permits at most those two declared
    prompt messages and validates the remainder with the stricter generic rule.
    """

    if not isinstance(prompt, Mapping) or "messages" not in prompt:
        assert_sanitized_interop_value(prompt)
        return
    safe_prompt = dict(prompt)
    messages = safe_prompt.pop("messages")
    assert_sanitized_interop_value(safe_prompt)
    if not isinstance(messages, list) or not 1 <= len(messages) <= 2:
        raise ValueError("LangChain prompt messages must contain one or two declared messages.")
    seen_roles: set[str] = set()
    for message in messages:
        if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
            raise ValueError("LangChain prompt messages must contain only role and content fields.")
        role = message["role"]
        content = message["content"]
        if role not in {"system", "user"} or role in seen_roles:
            raise ValueError("LangChain prompt message roles must be unique system/user roles.")
        if not isinstance(content, str) or not content or len(content) > 16_384:
            raise ValueError("LangChain prompt message content must be a bounded non-empty string.")
        seen_roles.add(role)


_OUTCOME_FIELDS = frozenset(
    {"status", "task_id", "iterations", "output", "reason", "failure", "escalation"}
)


def _agent_result_projection(
    result: AgentResult, *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Return the redacted run outcome, plus diagnostics only when the host opts in."""

    payload = result.model_dump(
        mode="json", include=None if include_diagnostics else set(_OUTCOME_FIELDS)
    )
    return redact_secrets(payload)


def _result_mapping(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, Mapping):
        return dict(result)
    raise TypeError("LangChain tool preflight must return ToolExecutionResult, a mapping, or None.")
