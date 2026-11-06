# Copyright (c) 2026 David Michael Indraputra

"""Everything an agent may call, behind one executor that runs only what was handed to it."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..foundations.contracts import ToolDefinition, ToolExecutionResult
from ..tools.tools import ToolInvocationContext
from .hooks import BeforeTool, event_for, refusal_reason
from .tools import FunctionTool


@dataclass(frozen=True)
class ApprovalRequest:
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Refusal:
    reason: str

    def __bool__(self) -> bool:
        return False


Approver = Callable[[ApprovalRequest], bool | Refusal | Awaitable[bool | Refusal]]


class ToolGroup(Protocol):
    definitions: Sequence[ToolDefinition]

    async def run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult: ...


class Toolbox:
    enforces_authorization = True

    def __init__(
        self,
        tools: Sequence[FunctionTool | ToolGroup],
        approve: Approver | None = None,
        *,
        guards: Sequence[BeforeTool] = (),
        refusal_status: Literal["blocked", "failed"] = "blocked",
    ) -> None:
        self._approve = approve
        self._guards = tuple(guards)
        self._refusal_status = refusal_status
        self._functions: dict[str, FunctionTool] = {}
        self.groups: list[ToolGroup] = []
        for item in tools:
            if isinstance(item, FunctionTool):
                if item.name in self._functions:
                    raise ValueError(
                        f'Two tools are named "{item.name}"; give one of them a different '
                        "name= so the model can tell them apart."
                    )
                self._functions[item.name] = item
            else:
                self.groups.append(item)
        self._unique(self._collect())

    @property
    def definitions(self) -> list[ToolDefinition]:
        return self._unique(self._collect())

    def _collect(self) -> list[ToolDefinition]:
        found = [function.definition for function in self._functions.values()]
        for group in self.groups:
            found.extend(group.definitions)
        return found

    @staticmethod
    def _unique(found: list[ToolDefinition]) -> list[ToolDefinition]:
        seen: set[str] = set()
        for definition in found:
            if definition.name in seen:
                raise ValueError(
                    f'Two tools are named "{definition.name}"; give one of them a different '
                    "name= so the model can tell them apart."
                )
            seen.add(definition.name)
        return found

    async def execute(
        self, tool: ToolDefinition, context: ToolInvocationContext
    ) -> ToolExecutionResult:
        arguments = context.call.arguments
        if self._guards:
            event = event_for(context)
            for guard in self._guards:
                reason = await refusal_reason(guard, event)
                if reason is not None:
                    return ToolExecutionResult(status=self._refusal_status, error=reason)
            arguments = event.arguments
        function = self._functions.get(tool.name)
        if function is not None:
            if function.needs_approval:
                refusal = await self._refusal(tool.name, arguments)
                if refusal is not None:
                    return refusal
            return await function.run(arguments)
        group = self._group_for(tool.name)
        if group is not None:
            if getattr(group, "needs_approval", False):
                refusal = await self._refusal(tool.name, arguments)
                if refusal is not None:
                    return refusal
            return await group.run(tool.name, arguments)
        return ToolExecutionResult(
            status="failed", error=f'This agent was not given a tool named "{tool.name}".'
        )

    def _group_for(self, name: str) -> ToolGroup | None:
        for group in self.groups:
            if any(definition.name == name for definition in group.definitions):
                return group
        return None

    async def _refusal(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult | None:
        if self._approve is None:
            return ToolExecutionResult(
                status=self._refusal_status,
                error=f'Tool "{name}" needs approval and this agent has no approve= callback.',
            )
        verdict = self._approve(ApprovalRequest(name, arguments))
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if verdict:
            return None
        reason = getattr(verdict, "reason", "")
        declined = f'Tool "{name}" was declined by the approve= callback.'
        return ToolExecutionResult(
            status=self._refusal_status, error=f"{declined} {reason}" if reason else declined
        )
