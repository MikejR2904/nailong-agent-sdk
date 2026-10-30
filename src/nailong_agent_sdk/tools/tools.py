# Copyright (c) 2026 David Michael Indraputra

"""Capability-bound BaseAgent tool interfaces and deterministic test executor."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Protocol

from ..foundations.contracts import ScopedAgentTask, ToolCall, ToolDefinition, ToolExecutionResult


@dataclass(frozen=True)
class ToolAuthorization:
    """The verdict of a ``ToolAuthorizer`` on one call, stamped with who issued it.

    ``issuer`` is the authorizer object itself: an executor that holds the same object accepts the
    stamp instead of authorizing again, so a single-use approval is consumed once per call.
    ``result`` is the blocked or denied result to return when ``allowed`` is false.
    """

    allowed: bool
    tool: str
    call_id: str
    issuer: object | None = None
    capability: str | None = None
    approval_id: str | None = None
    result: ToolExecutionResult | None = None


@dataclass(frozen=True)
class ToolInvocationContext:
    agent_identity: str
    task: ScopedAgentTask
    iteration: int
    call: ToolCall
    trace_parent: str | None = None
    authorization: ToolAuthorization | None = None


class ToolAuthorizer(Protocol):
    """Decides whether one tool call may run, before any executor sees it."""

    def authorize(
        self, tool: ToolDefinition, context: ToolInvocationContext
    ) -> ToolAuthorization | Awaitable[ToolAuthorization]: ...


class ToolExecutor(Protocol):
    async def execute(
        self,
        tool: ToolDefinition,
        context: ToolInvocationContext,
    ) -> ToolExecutionResult: ...


class InMemoryTaskToolExecutor:
    """Safe deterministic tools used by protocol and cross-language tests.

    `read_locked_interface` is read-only and returns the verbatim task snapshot.
    `echo` returns the validated argument payload. No filesystem or process is
    started by this test executor.
    """

    def __init__(self) -> None:
        self.calls: list[ToolInvocationContext] = []

    async def execute(
        self,
        tool: ToolDefinition,
        context: ToolInvocationContext,
    ) -> ToolExecutionResult:
        self.calls.append(context)
        if tool.name == "read_locked_interface":
            return ToolExecutionResult(status="succeeded", output=context.task.locked_interface)
        if tool.name == "echo":
            return ToolExecutionResult(status="succeeded", output=context.call.arguments)
        return ToolExecutionResult(
            status="failed",
            error=f'No deterministic executor implementation exists for tool "{tool.name}".',
        )


@dataclass
class RecordingToolExecutor:
    """Small test double whose behaviour is explicit at the test call site."""

    results_by_name: dict[str, ToolExecutionResult]
    calls: list[ToolInvocationContext] = field(default_factory=list)

    async def execute(
        self,
        tool: ToolDefinition,
        context: ToolInvocationContext,
    ) -> ToolExecutionResult:
        self.calls.append(context)
        return self.results_by_name.get(
            tool.name,
            ToolExecutionResult(status="failed", error=f'No result configured for "{tool.name}".'),
        )
