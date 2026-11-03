# Copyright (c) 2026 David Michael Indraputra

"""Tool hooks as plain functions: look at a call before it runs, or at its result after."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..agent.base_agent.types import PostToolHook, PreToolHook, ToolHookDecision
from ..foundations.contracts import ToolExecutionResult
from ..tools.tools import ToolInvocationContext


@dataclass(frozen=True)
class ToolEvent:
    tool: str
    arguments: dict[str, Any]
    iteration: int
    result: ToolExecutionResult | None = None


BeforeTool = Callable[[ToolEvent], bool | str | None | Awaitable[bool | str | None]]
AfterTool = Callable[[ToolEvent], None | Awaitable[None]]


def before_tool_hooks(callbacks: Sequence[BeforeTool]) -> list[PreToolHook]:
    return [_before(callback) for callback in callbacks]


def after_tool_hooks(callbacks: Sequence[AfterTool]) -> list[PostToolHook]:
    return [_after(callback) for callback in callbacks]


async def refusal_reason(callback: BeforeTool, event: ToolEvent) -> str | None:
    name = getattr(callback, "__name__", type(callback).__name__)
    verdict = callback(event)
    if inspect.isawaitable(verdict):
        verdict = await verdict
    if verdict is None or verdict is True:
        return None
    if verdict is False:
        return f'Hook "{name}" refused the call.'
    if isinstance(verdict, str) and verdict.strip():
        return verdict
    raise TypeError(
        f'Hook "{name}" must return None or True to allow the call, False or a non-empty '
        f"reason string to refuse it; it returned {type(verdict).__name__}."
    )


def event_for(
    context: ToolInvocationContext, result: ToolExecutionResult | None = None
) -> ToolEvent:
    return ToolEvent(
        tool=context.call.name,
        arguments=dict(context.call.arguments),
        iteration=context.iteration,
        result=result,
    )


def _before(callback: BeforeTool) -> PreToolHook:
    async def hook(context: ToolInvocationContext) -> ToolHookDecision | None:
        reason = await refusal_reason(callback, event_for(context))
        return None if reason is None else ToolHookDecision(False, reason)

    return hook


def _after(callback: AfterTool) -> PostToolHook:
    async def hook(context: ToolInvocationContext, result: ToolExecutionResult) -> None:
        outcome = callback(event_for(context, result))
        if inspect.isawaitable(outcome):
            await outcome

    return hook
