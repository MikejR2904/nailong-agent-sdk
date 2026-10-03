# Copyright (c) 2026 David Michael Indraputra

"""Capability-bound BaseAgent tool interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..foundations.contracts import ScopedAgentTask, ToolCall, ToolDefinition, ToolExecutionResult


@dataclass(frozen=True)
class ToolInvocationContext:
    agent_identity: str
    task: ScopedAgentTask
    iteration: int
    call: ToolCall


class ToolExecutor(Protocol):
    async def execute(
        self,
        tool: ToolDefinition,
        context: ToolInvocationContext,
    ) -> ToolExecutionResult: ...
