# Copyright (c) 2026 David Michael Indraputra

"""Expose connected external MCP tools as explicit, named harness tool extensions.

This is the seam that keeps ``HarnessToolRegistry`` closed by design: a host
must connect specific MCP servers and then opt their tools in by name through
``HarnessToolRegistry.with_extensions``, rather than an agent being able to
request an arbitrary, dynamically-named external tool.
"""

from __future__ import annotations

from typing import Any

from ..tools.policy import SideEffectClass
from ..tools.registry import HarnessExecutionContext, HarnessToolHandler, RegisteredTool
from .client import McpClientManager


def registered_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp__{server_name}__{tool_name}"


def mcp_tools_as_extensions(
    manager: McpClientManager,
    *,
    capability_prefix: str = "mcp",
) -> tuple[list[RegisteredTool], dict[str, HarnessToolHandler]]:
    """Return one ``RegisteredTool`` plus handler per currently connected MCP tool.

    Every tool is gated behind the capability ``"<capability_prefix>.<server>"``
    (server-scoped, not tool-scoped) and classified as ``PROCESS`` rather than
    ``READ_ONLY``: an external server's side effects are unknown to this SDK,
    so a call always needs an explicit capability grant and, unless the role's
    grant carries a standing approval, a typed approval decision.
    """

    extensions: list[RegisteredTool] = []
    handlers: dict[str, HarnessToolHandler] = {}
    for tool in manager.list_tools():
        name = registered_tool_name(tool.server_name, tool.name)
        extensions.append(
            RegisteredTool(name, f"{capability_prefix}.{tool.server_name}", SideEffectClass.PROCESS)
        )
        handlers[name] = _bind_handler(manager, tool.server_name, tool.name)
    return extensions, handlers


def _bind_handler(
    manager: McpClientManager, server_name: str, tool_name: str
) -> HarnessToolHandler:
    async def handler(_context: HarnessExecutionContext, arguments: dict[str, Any]) -> str:
        return await manager.call_tool(server_name, tool_name, arguments)

    return handler
