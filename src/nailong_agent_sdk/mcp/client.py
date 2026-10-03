# Copyright (c) 2026 David Michael Indraputra

"""Outbound MCP client: connect this SDK to external MCP servers as a caller.

The existing ``mcp/`` surface exposes this SDK's own capabilities to a host
over MCP. ``McpClientManager`` is the other direction: it lets a host-approved
agent run consume tools and resources published by MCP servers it does not
own. Connecting a server is always an explicit, host-driven action; nothing
here discovers or dials a server on its own.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ReadResourceResult

from ..foundations.logging import get_logger
from .client_types import (
    McpConnectionState,
    McpConnectionStatus,
    McpHttpServerConfig,
    McpResourceInfo,
    McpServerConfig,
    McpStdioServerConfig,
    McpToolInfo,
    McpTransportKind,
)

_logger = get_logger("mcp.client")


class McpServerNotConnectedError(RuntimeError):
    """Raised when a call targets an MCP server that is not currently connected."""


class McpClientManager:
    """Own the lifecycle of a fixed set of outbound MCP server connections."""

    def __init__(self, server_configs: list[McpServerConfig]) -> None:
        names = [config.name for config in server_configs]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique.")
        self._configs = {config.name: config for config in server_configs}
        self._statuses: dict[str, McpConnectionStatus] = {
            config.name: McpConnectionStatus(
                name=config.name,
                state=McpConnectionState.PENDING,
                transport=_transport_kind(config),
            )
            for config in server_configs
        }
        self._sessions: dict[str, ClientSession] = {}
        self._stacks: dict[str, AsyncExitStack] = {}

    async def connect_all(self) -> None:
        """Connect every configured server; a per-server failure does not abort the rest."""

        for name, config in self._configs.items():
            if isinstance(config, McpStdioServerConfig):
                await self._connect_stdio(name, config)
            else:
                await self._connect_http(name, config)

    async def reconnect(self, name: str) -> None:
        config = self._configs.get(name)
        if config is None:
            raise ValueError(f'No MCP server named "{name}" is configured.')
        await self._close_one(name)
        self._statuses[name] = McpConnectionStatus(
            name=name, state=McpConnectionState.PENDING, transport=_transport_kind(config)
        )
        if isinstance(config, McpStdioServerConfig):
            await self._connect_stdio(name, config)
        else:
            await self._connect_http(name, config)

    async def close(self) -> None:
        """Close every active session. Safe to call more than once."""

        for name in list(self._stacks):
            await self._close_one(name)

    def list_statuses(self) -> list[McpConnectionStatus]:
        return [self._statuses[name] for name in sorted(self._statuses)]

    def list_tools(self) -> list[McpToolInfo]:
        return [tool for status in self.list_statuses() for tool in status.tools]

    def list_resources(self) -> list[McpResourceInfo]:
        return [resource for status in self.list_statuses() for resource in status.resources]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke one connected tool and return its content flattened to text."""

        session = self._require_session(server_name)
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception as error:
            raise McpServerNotConnectedError(
                f'MCP tool call to "{server_name}::{tool_name}" failed: {error}'
            ) from error
        if not isinstance(result, CallToolResult):
            raise McpServerNotConnectedError(
                f'MCP server "{server_name}" returned an unsupported '
                f"{type(result).__name__} for a tool call; interactive mid-call "
                "input requests are not supported by this client."
            )
        parts = [getattr(item, "text", None) or item.model_dump_json() for item in result.content]
        if result.structured_content and not parts:
            parts.append(str(result.structured_content))
        text = "\n".join(part for part in parts if part).strip() or "(no output)"
        if result.is_error:
            raise McpServerNotConnectedError(
                f'MCP tool "{server_name}::{tool_name}" failed: {text}'
            )
        return text

    async def read_resource(self, server_name: str, uri: str) -> str:
        session = self._require_session(server_name)
        try:
            result = await session.read_resource(uri)
        except Exception as error:
            raise McpServerNotConnectedError(
                f'MCP resource read "{server_name}::{uri}" failed: {error}'
            ) from error
        if not isinstance(result, ReadResourceResult):
            raise McpServerNotConnectedError(
                f'MCP server "{server_name}" returned an unsupported '
                f"{type(result).__name__} for a resource read."
            )
        parts = [
            getattr(item, "text", None) or str(getattr(item, "blob", ""))
            for item in result.contents
        ]
        return "\n".join(parts).strip()

    def _require_session(self, server_name: str) -> ClientSession:
        session = self._sessions.get(server_name)
        if session is None:
            status = self._statuses.get(server_name)
            detail = status.detail if status else "unknown server"
            raise McpServerNotConnectedError(
                f'MCP server "{server_name}" is not connected: {detail}'
            )
        return session

    async def _connect_stdio(self, name: str, config: McpStdioServerConfig) -> None:
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config.command, args=config.args, env=config.env, cwd=config.cwd
                    )
                )
            )
            await self._register_connected_session(
                name=name,
                config=config,
                stack=stack,
                read_stream=read_stream,
                write_stream=write_stream,
                auth_configured=bool(config.env),
            )
        except Exception as error:
            await self._abandon_stack(stack)
            self._mark_failed(name, config, auth_configured=bool(config.env), error=error)

    async def _connect_http(self, name: str, config: McpHttpServerConfig) -> None:
        stack = AsyncExitStack()
        try:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(headers=config.headers or None)
            )
            read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
            await self._register_connected_session(
                name=name,
                config=config,
                stack=stack,
                read_stream=read_stream,
                write_stream=write_stream,
                auth_configured=bool(config.headers),
            )
        except Exception as error:
            await self._abandon_stack(stack)
            self._mark_failed(name, config, auth_configured=bool(config.headers), error=error)

    async def _register_connected_session(
        self,
        *,
        name: str,
        config: McpServerConfig,
        stack: AsyncExitStack,
        read_stream: Any,
        write_stream: Any,
        auth_configured: bool,
    ) -> None:
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        tool_result = await session.list_tools()
        resources, resources_detail = await self._list_resources_best_effort(name, session)
        self._sessions[name] = session
        self._stacks[name] = stack
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.CONNECTED,
            transport=_transport_kind(config),
            auth_configured=auth_configured,
            detail=resources_detail,
            tools=[
                McpToolInfo(
                    server_name=name,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema or {"type": "object", "properties": {}}),
                )
                for tool in tool_result.tools
            ],
            resources=resources,
        )

    @staticmethod
    async def _list_resources_best_effort(
        name: str, session: ClientSession
    ) -> tuple[list[McpResourceInfo], str | None]:
        """List resources, tolerating servers that do not implement the method.

        Only the JSON-RPC "method not found" response is treated as expected;
        any other failure still connects the server (tools remain usable) but
        is surfaced through the status ``detail`` instead of being discarded,
        so a genuine failure here stays visible rather than looking identical
        to "this server has no resources".
        """

        try:
            resource_result = await session.list_resources()
        except Exception as error:
            if "Method not found" in str(error):
                return [], None
            return [], f"Resource listing failed: {error}"
        resources = [
            McpResourceInfo(
                server_name=name,
                name=resource.name or str(resource.uri),
                uri=str(resource.uri),
                description=resource.description or "",
            )
            for resource in resource_result.resources
        ]
        return resources, None

    def _mark_failed(
        self, name: str, config: McpServerConfig, *, auth_configured: bool, error: BaseException
    ) -> None:
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.FAILED,
            transport=_transport_kind(config),
            auth_configured=auth_configured,
            detail=str(error) or type(error).__name__,
        )

    async def _close_one(self, name: str) -> None:
        stack = self._stacks.pop(name, None)
        self._sessions.pop(name, None)
        if stack is not None:
            await self._abandon_stack(stack)

    @staticmethod
    async def _abandon_stack(stack: AsyncExitStack) -> None:
        try:
            await stack.aclose()
        except Exception:
            _logger.debug("MCP connection stack close raised during teardown", exc_info=True)


def _transport_kind(config: McpServerConfig) -> McpTransportKind:
    if isinstance(config, McpStdioServerConfig):
        return McpTransportKind.STDIO
    return McpTransportKind.HTTP
