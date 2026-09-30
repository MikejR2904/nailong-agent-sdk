# Copyright (c) 2026 David Michael Indraputra

"""Standalone types supporting the BaseAgent execution runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ...foundations.contracts import AgentRunStatus, ModelObservation, ToolCall, ToolExecutionResult
from ...tools.tools import ToolInvocationContext
from ..model import ProviderToolResult


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


class ToolHookDecision:
    def __init__(self, allowed: bool, reason: str | None = None) -> None:
        self.allowed = allowed
        self.reason = reason


class AgentWatchdogPolicy:
    """Optional wall-clock limits for one bounded agent invocation."""

    def __init__(
        self,
        *,
        run_deadline_seconds: float | None = None,
        model_turn_timeout_seconds: float | None = None,
        tool_call_timeout_seconds: float | None = None,
        verification_timeout_seconds: float | None = None,
    ) -> None:
        values = {
            "run_deadline_seconds": run_deadline_seconds,
            "model_turn_timeout_seconds": model_turn_timeout_seconds,
            "tool_call_timeout_seconds": tool_call_timeout_seconds,
            "verification_timeout_seconds": verification_timeout_seconds,
        }
        for name, value in values.items():
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when configured.")
        self.run_deadline_seconds = run_deadline_seconds
        self.model_turn_timeout_seconds = model_turn_timeout_seconds
        self.tool_call_timeout_seconds = tool_call_timeout_seconds
        self.verification_timeout_seconds = verification_timeout_seconds


PreToolHook = Callable[[ToolInvocationContext], Awaitable[ToolHookDecision | None]]
PostToolHook = Callable[[ToolInvocationContext, ToolExecutionResult], Awaitable[None]]


@dataclass(frozen=True)
class ToolCallOutcome:
    """One execution outcome, including the episode eligible for later projection."""

    call: ToolCall
    result: ToolExecutionResult
    observation: ModelObservation
    episode_id: str | None = None
    episode_kind: str | None = None
    terminal_status: AgentRunStatus | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True)
class ToolBatchExecution:
    """Completed batch projections for state reduction and an optional provider handoff."""

    observations: tuple[ModelObservation, ...]
    provider_results: tuple[ProviderToolResult, ...]
