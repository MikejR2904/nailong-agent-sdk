# Copyright (c) 2026 David Michael Indraputra

"""Standalone types supporting the BaseAgent execution runtime."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ...foundations.contracts import AgentRunStatus, ModelObservation, ToolCall, ToolExecutionResult
from ...tools.tools import ToolInvocationContext
from ..model import ProviderToolResult


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


PAUSE_POLL_SECONDS = 0.05


class PauseGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._since: float | None = None

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._since is not None

    def pause(self) -> bool:
        with self._lock:
            if self._since is not None:
                return False
            self._since = time.monotonic()
            return True

    def resume(self) -> bool:
        with self._lock:
            if self._since is None:
                return False
            self._since = None
            return True

    async def wait_until_resumed(self, cancellation: CancellationToken | None = None) -> float:
        started = time.monotonic()
        while self.paused:
            if cancellation is not None and cancellation.is_cancelled():
                break
            await asyncio.sleep(PAUSE_POLL_SECONDS)
        return time.monotonic() - started


MAX_INBOX_MESSAGE_CHARS = 4_000
MAX_INBOX_PENDING = 20


class MessageInbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[str] = []

    def put(self, text: str) -> None:
        message = text.strip()
        if not message:
            raise ValueError("A message for a running agent cannot be empty.")
        if len(message) > MAX_INBOX_MESSAGE_CHARS:
            raise ValueError(
                f"A message for a running agent is limited to {MAX_INBOX_MESSAGE_CHARS} "
                f"characters; this one has {len(message)}."
            )
        with self._lock:
            if len(self._messages) >= MAX_INBOX_PENDING:
                raise ValueError(
                    f"{MAX_INBOX_PENDING} messages are already waiting for the agent to read "
                    "them; wait for it to take its next step."
                )
            self._messages.append(message)

    def drain(self) -> list[str]:
        with self._lock:
            taken, self._messages = self._messages, []
        return taken

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._messages)


class ToolHookDecision:
    def __init__(self, allowed: bool, reason: str | None = None) -> None:
        self.allowed = allowed
        self.reason = reason


class WatchdogExpired(TimeoutError):
    def __init__(
        self, source: str, seconds: float | None, still_running: Sequence[str] = ()
    ) -> None:
        message = f"{source} expired after {seconds}s" if seconds is not None else source
        if still_running:
            message += (
                f"; the tool function(s) {', '.join(still_running)} cannot be stopped and are "
                "still running in the background, so their effects may still happen"
            )
        super().__init__(message)
        self.source = source
        self.seconds = seconds
        self.still_running = tuple(still_running)


class RunCancelled(Exception):
    pass


class AgentWatchdogPolicy:
    """Optional wall-clock limits and bounded concurrency for one agent invocation."""

    def __init__(
        self,
        *,
        run_deadline_seconds: float | None = None,
        model_turn_timeout_seconds: float | None = None,
        tool_call_timeout_seconds: float | None = None,
        verification_timeout_seconds: float | None = None,
        transient_retry_base_seconds: float = 0.5,
        transient_retry_max_seconds: float = 30.0,
        max_parallel_tool_calls: int = 16,
        invalid_output_retries: int = 2,
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
        if transient_retry_base_seconds <= 0 or transient_retry_max_seconds <= 0:
            raise ValueError("transient retry delays must be positive.")
        if transient_retry_base_seconds > transient_retry_max_seconds:
            raise ValueError(
                "transient_retry_base_seconds must not exceed transient_retry_max_seconds."
            )
        if max_parallel_tool_calls < 1:
            raise ValueError("max_parallel_tool_calls must be at least one.")
        self.run_deadline_seconds = run_deadline_seconds
        self.model_turn_timeout_seconds = model_turn_timeout_seconds
        self.tool_call_timeout_seconds = tool_call_timeout_seconds
        self.verification_timeout_seconds = verification_timeout_seconds
        self.transient_retry_base_seconds = transient_retry_base_seconds
        self.transient_retry_max_seconds = transient_retry_max_seconds
        if invalid_output_retries < 0:
            raise ValueError("invalid_output_retries must not be negative.")
        self.max_parallel_tool_calls = max_parallel_tool_calls
        self.invalid_output_retries = invalid_output_retries


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
