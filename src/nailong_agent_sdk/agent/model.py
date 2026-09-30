# Copyright (c) 2026 David Michael Indraputra

"""Provider-neutral model interfaces and deterministic test adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import TypeAdapter

from ..foundations.contracts import (
    AgentPrompt,
    AgentTurn,
    ContextProjectionMetadata,
    EpisodeSummary,
    ModelBinding,
    ModelObservation,
    ScopedAgentTask,
)
from ..foundations.errors import AgentSdkError, TransientProviderError
from ..foundations.logging import get_logger
from ..state.project_state_models import ProjectStateView

_AGENT_TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)
_logger = get_logger("agent.model")


@dataclass(frozen=True)
class ProviderUsage:
    """Provider-reported token counters for one response, never estimated by the SDK."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    context_window_tokens: int | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "context_window_tokens",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative when reported.")


@dataclass(frozen=True)
class ProviderContinuation:
    """Opaque provider-owned conversation state required for a later model turn.

    The BaseAgent persists and forwards this state without interpreting it. A
    selected provider adapter owns validation and serialization semantics.
    """

    provider: str
    state: dict[str, Any]


@dataclass(frozen=True)
class ModelTurnResponse:
    """Untrusted provider response with optional opaque continuation material."""

    turn: AgentTurn
    continuation: ProviderContinuation | None = None
    usage: ProviderUsage | None = None


@dataclass(frozen=True)
class ModelTextDelta:
    """One incremental fragment of assistant text as it is generated."""

    text: str


@dataclass(frozen=True)
class ModelToolCallDelta:
    """One incremental fragment of a tool call being generated.

    ``call_id``/``name`` are populated only on the first chunk for a given
    ``index``; every later chunk for that same index carries only the next
    ``arguments_delta`` fragment to append.
    """

    index: int
    call_id: str | None
    name: str | None
    arguments_delta: str


@dataclass(frozen=True)
class ModelStreamCompleted:
    """Marks the end of one streamed turn; usage is reported once here, not per-delta."""

    usage: ProviderUsage | None = None


ModelStreamEvent = ModelTextDelta | ModelToolCallDelta | ModelStreamCompleted
ModelStreamListener = Callable[[ModelStreamEvent], Awaitable[None] | None]


@dataclass(frozen=True)
class ModelContext:
    task: ScopedAgentTask
    prompt: AgentPrompt
    iteration: int
    project_state: ProjectStateView
    observations: Sequence[ModelObservation]
    episodes: Sequence[EpisodeSummary]
    continuation: ProviderContinuation | None = None
    projection: ContextProjectionMetadata | None = None
    # The model adapter validates this immutable data binding before sending a
    # request, so a host cannot silently use a client for a different model.
    model_binding: ModelBinding | None = None
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderToolResult:
    """A bounded one-time result projection required by a provider continuation protocol.

    This is not ordinary state-first model context. The BaseAgent passes it only to
    adapters that explicitly implement ``ProviderToolResultConsumer`` so an external
    provider can resolve a function/tool call within its own continuation protocol.
    """

    call_id: str
    name: str
    status: str
    content: str
    truncated: bool


@runtime_checkable
class ProviderToolResultConsumer(Protocol):
    """Optional adapter capability for provider-native tool-call continuations."""

    async def accept_tool_results(
        self,
        continuation: ProviderContinuation,
        results: Sequence[ProviderToolResult],
    ) -> ProviderContinuation: ...


class AgentModel(Protocol):
    """An injected model adapter; it returns untrusted output for runtime validation."""

    async def next_turn(self, context: ModelContext) -> AgentTurn | ModelTurnResponse: ...


@runtime_checkable
class StreamingAgentModel(Protocol):
    """Optional adapter capability: stream incremental deltas while it works.

    ``stream_turn`` still returns the complete turn at the end — streaming is
    an additional side channel for incremental UX, not a different contract.
    An adapter that does not implement this is used through ``next_turn``
    exactly as before; ``BaseAgent`` only calls ``stream_turn`` when both a
    listener is configured and the bound model is an instance of this
    protocol.
    """

    async def stream_turn(
        self, context: ModelContext, on_delta: ModelStreamListener
    ) -> AgentTurn | ModelTurnResponse: ...


class ScriptedModel:
    """Deterministic test adapter; never calls an external model provider."""

    def __init__(self, turns: Sequence[AgentTurn | dict[str, Any]]) -> None:
        self._turns = [_AGENT_TURN_ADAPTER.validate_python(turn) for turn in turns]
        self.calls: list[ModelContext] = []

    async def next_turn(self, context: ModelContext) -> AgentTurn:
        self.calls.append(context)
        index = context.iteration - 1
        if index >= len(self._turns):
            raise AgentSdkError(
                "SCRIPTED_MODEL_EXHAUSTED",
                "No deterministic scripted turn was supplied for this iteration.",
            )
        return self._turns[index]


@dataclass(frozen=True)
class ModelFailoverAttempt:
    index: int
    error: str
    retryable: bool = False
    retry_number: int = 0


class FailoverAgentModel:
    """Try injected provider adapters in declared primary-to-fallback order.

    The class does not select models by itself. Its caller supplies adapters in
    the exact order of the agent definition's model binding and fallback list.
    A ``TransientProviderError`` (429, 5xx, or a connection drop) is retried
    against the *same* adapter with bounded exponential backoff — honoring a
    server-declared ``retry_after_seconds`` when present — before moving to
    the next adapter; any other exception moves to the next adapter
    immediately, exactly as before. When every adapter is exhausted, the
    BaseAgent's bounded termination policy records the failure and performs
    its configured controller or human escalation.

    Implementing ``StreamingAgentModel`` here (via ``stream_turn``) matters:
    without it, wrapping a streaming-capable adapter in ``FailoverAgentModel``
    would silently *lose* streaming, since ``BaseAgent`` decides whether to
    stream by checking ``isinstance(self.model, StreamingAgentModel)`` on
    whatever it was actually given. Retry/backoff behaves identically for a
    streaming attempt; an adapter in the list that does not itself support
    streaming is called through its plain ``next_turn`` for its turn in the
    rotation rather than treated as an error.
    """

    def __init__(
        self,
        models: Sequence[AgentModel],
        *,
        max_retries_per_model: int = 2,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 20.0,
        on_attempt: Callable[[ModelFailoverAttempt], None] | None = None,
    ) -> None:
        if not models:
            raise ValueError("FailoverAgentModel requires a primary adapter.")
        if max_retries_per_model < 0:
            raise ValueError("max_retries_per_model may not be negative.")
        if base_backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("Backoff durations may not be negative.")
        self._models = tuple(models)
        self._max_retries_per_model = max_retries_per_model
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._on_attempt = on_attempt
        self.attempts: list[ModelFailoverAttempt] = []

    async def next_turn(self, context: ModelContext) -> AgentTurn | ModelTurnResponse:
        return await self._call_with_failover(lambda model: model.next_turn(context))

    async def stream_turn(
        self, context: ModelContext, on_delta: ModelStreamListener
    ) -> AgentTurn | ModelTurnResponse:
        async def call_one(model: AgentModel) -> AgentTurn | ModelTurnResponse:
            if isinstance(model, StreamingAgentModel):
                return await model.stream_turn(context, on_delta)
            return await model.next_turn(context)

        return await self._call_with_failover(call_one)

    async def _call_with_failover(
        self, call_model: Callable[[AgentModel], Awaitable[Any]]
    ) -> Any:
        for index, model in enumerate(self._models):
            for retry_number in range(self._max_retries_per_model + 1):
                try:
                    return await call_model(model)
                except TransientProviderError as error:
                    self._record(
                        ModelFailoverAttempt(
                            index=index, error=str(error), retryable=True, retry_number=retry_number
                        )
                    )
                    if retry_number >= self._max_retries_per_model:
                        break  # Retries exhausted for this model; fall over to the next one.
                    await asyncio.sleep(self._backoff_seconds(error, retry_number))
                except Exception as error:
                    self._record(
                        ModelFailoverAttempt(
                            index=index, error=str(error), retry_number=retry_number
                        )
                    )
                    break
        raise AgentSdkError(
            "MODEL_FALLBACK_EXHAUSTED",
            "Primary model and all configured fallback adapters failed.",
            {
                "attempts": [
                    {
                        "index": attempt.index,
                        "error": attempt.error,
                        "retryable": attempt.retryable,
                        "retry_number": attempt.retry_number,
                    }
                    for attempt in self.attempts
                ]
            },
        )

    def _record(self, attempt: ModelFailoverAttempt) -> None:
        self.attempts.append(attempt)
        if self._on_attempt is None:
            return
        try:
            self._on_attempt(attempt)
        except Exception:
            _logger.exception(
                "on_attempt listener raised for adapter index %s (retry_number=%s)",
                attempt.index,
                attempt.retry_number,
            )

    def _backoff_seconds(self, error: TransientProviderError, retry_number: int) -> float:
        if error.retry_after_seconds is not None:
            return min(error.retry_after_seconds, self._max_backoff_seconds)
        return min(self._base_backoff_seconds * (2**retry_number), self._max_backoff_seconds)
