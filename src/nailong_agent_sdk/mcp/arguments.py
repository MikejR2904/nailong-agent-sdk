# Copyright (c) 2026 David Michael Indraputra

"""Refuse tool arguments the MCP surface does not define instead of dropping them silently."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent

if TYPE_CHECKING:
    from mcp.server import MCPServer

PYTHON_ONLY_CAPABILITIES: Mapping[str, str] = {
    "model": (
        "a model adapter is an object in the host process; send runtime_options.model_endpoint "
        "(base_url and api_key) to use an OpenAI-compatible provider"
    ),
    "verification_gates": (
        "a verification gate is code that runs in the host process and has no serializable form"
    ),
    "pre_tool_hooks": (
        "a tool hook is code that runs in the host process and has no serializable form"
    ),
    "post_tool_hooks": (
        "a tool hook is code that runs in the host process and has no serializable form"
    ),
    "hooks": "a hook is code that runs in the host process and has no serializable form",
    "cancellation": (
        "a cancellation token is an object in the host process; call cancel_agent_task with the "
        "task id instead"
    ),
    "approved_by": (
        "who approved a capability in advance is a claim the host process makes about its own "
        "decision; an MCP caller cannot name an approver, and a decision made while a task waits "
        "goes through decide_task_approval with an approver credential"
    ),
    "approval_reason": (
        "the reason kept with an advance approval is written by the host process that makes it"
    ),
    "tool_executor": "a tool executor is an object in the host process",
    "transport": "a transport is an object in the host process",
    "streaming_transport": "a transport is an object in the host process",
    "sub_agents": (
        "sub-agents are composed in the host process by running one BaseAgent per delegated task"
    ),
    "subagents": (
        "sub-agents are composed in the host process by running one BaseAgent per delegated task"
    ),
}

_NESTED_OBJECTS: Mapping[str, str] = {"run_agent_task": "runtime_options"}


def _quoted(names: list[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


class ToolArgumentGuard:
    def __init__(self, server: MCPServer) -> None:
        self._server = server
        self._accepted: dict[str, frozenset[str]] | None = None

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if getattr(ctx, "method", None) == "tools/call" and isinstance(ctx.params, dict):
            message = await self._refusal(ctx.params)
            if message is not None:
                envelope = {
                    "ok": False,
                    "errors": [{"message": message, "type": "UnsupportedArgument"}],
                }
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(envelope))],
                    structured_content=envelope,
                    is_error=False,
                )
        return await call_next(ctx)

    async def _refusal(self, params: Mapping[str, Any]) -> str | None:
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return None
        accepted = await self._accepted_for(name)
        if accepted is None:
            return None
        unknown = sorted(set(arguments) - accepted)
        if unknown:
            return self._describe(f'Tool "{name}"', unknown, sorted(accepted))
        nested = _NESTED_OBJECTS.get(name)
        options = arguments.get(nested) if nested is not None else None
        if nested is not None and isinstance(options, dict):
            python_only = sorted(key for key in options if key in PYTHON_ONLY_CAPABILITIES)
            if python_only:
                return self._describe(f'The "{nested}" of tool "{name}"', python_only, None)
        return None

    async def _accepted_for(self, name: str) -> frozenset[str] | None:
        if self._accepted is None:
            self._accepted = {
                tool.name: frozenset(tool.input_schema.get("properties", {}))
                for tool in await self._server.list_tools()
            }
        return self._accepted.get(name)

    @staticmethod
    def _describe(subject: str, unknown: list[str], accepted: list[str] | None) -> str:
        reasons = [
            f'"{name}" is a Python-only capability of AgentTaskRunner.run and is not exposed '
            f"through MCP: {PYTHON_ONLY_CAPABILITIES[name]}"
            for name in unknown
            if name in PYTHON_ONLY_CAPABILITIES
        ]
        message = f"{subject} does not accept {_quoted(unknown)}."
        if reasons:
            message += " " + "; ".join(reasons) + "."
        if accepted is not None:
            message += f" It accepts: {', '.join(accepted)}."
        return message
