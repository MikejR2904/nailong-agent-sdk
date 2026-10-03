# Copyright (c) 2026 David Michael Indraputra

"""Outbound MCP client: connect this SDK to external MCP servers as a caller.

The existing ``mcp/`` surface exposes this SDK's own capabilities to a host
over MCP. ``McpClientManager`` is the other direction: it lets a host-approved
agent run consume tools and resources published by MCP servers it does not
own. Connecting a server is always an explicit, host-driven action; nothing
here discovers or dials a server on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import AsyncExitStack
from typing import Any, TypeVar

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ReadResourceResult

from ..foundations.logging import get_logger
from .client_types import (
    McpConnectionState,
    McpConnectionStatus,
    McpErrorKind,
    McpResourceInfo,
    McpServerConfig,
    McpStdioServerConfig,
    McpToolInfo,
    McpTransportKind,
)

_logger = get_logger("mcp.client")
_T = TypeVar("_T")
_CLOSE_TIMEOUT_SECONDS = 10.0


class McpClientError(RuntimeError):
    """Base for outbound MCP failures; ``kind`` says which cause it was."""

    kind: McpErrorKind = McpErrorKind.TRANSPORT

    def __init__(self, message: str, *, server_name: str, target: str | None = None) -> None:
        super().__init__(message)
        self.server_name = server_name
        self.target = target


class McpServerNotConnectedError(McpClientError):
    """The call targets an MCP server that is not currently connected."""

    kind = McpErrorKind.NOT_CONNECTED


class McpTimeoutError(McpClientError):
    """Connecting, or a call, exceeded the server's configured timeout."""

    kind = McpErrorKind.TIMEOUT


class McpTransportError(McpClientError):
    """The transport or session raised while talking to a connected server."""

    kind = McpErrorKind.TRANSPORT


class McpToolError(McpClientError):
    """The server ran the tool and reported that it failed."""

    kind = McpErrorKind.TOOL_ERROR


class McpProtocolError(McpClientError):
    """The server answered with something this client does not support."""

    kind = McpErrorKind.PROTOCOL


class _Connection:
    """One server session, owned end to end by a dedicated task.

    The MCP transports are anyio context managers whose cancel scopes must be
    entered and exited by the same task. Giving every connection its own task
    satisfies that and lets servers connect concurrently.
    """

    def __init__(self) -> None:
        self.ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        self.closing = asyncio.Event()
        self.task: asyncio.Task[None] | None = None


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
        self._connections: dict[str, _Connection] = {}

    async def connect_all(self) -> None:
        """Connect every configured server concurrently.

        Each server has its own connect timeout; a failure or timeout marks that
        server failed (with its ``error_kind``) and does not abort the rest.
        """

        await asyncio.gather(*(self._connect(name) for name in self._configs))

    async def reconnect(self, name: str) -> None:
        config = self._configs.get(name)
        if config is None:
            raise ValueError(f'No MCP server named "{name}" is configured.')
        await self._close_one(name)
        self._statuses[name] = McpConnectionStatus(
            name=name, state=McpConnectionState.PENDING, transport=_transport_kind(config)
        )
        await self._connect(name)

    async def close(self) -> None:
        """Close every active session. Safe to call more than once."""

        await asyncio.gather(*(self._close_one(name) for name in list(self._connections)))

    def list_statuses(self) -> list[McpConnectionStatus]:
        return [self._statuses[name] for name in sorted(self._statuses)]

    def list_tools(self) -> list[McpToolInfo]:
        return [tool for status in self.list_statuses() for tool in status.tools]

    def list_resources(self) -> list[McpResourceInfo]:
        return [resource for status in self.list_statuses() for resource in status.resources]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke one connected tool and return its content flattened to text."""

        target = f"{server_name}::{tool_name}"
        session = self._require_session(server_name)
        result = await self._bounded(
            server_name, target, "tool call", session.call_tool(tool_name, arguments)
        )
        if not isinstance(result, CallToolResult):
            raise McpProtocolError(
                f'MCP server "{server_name}" returned an unsupported '
                f"{type(result).__name__} for a tool call; interactive mid-call "
                "input requests are not supported by this client.",
                server_name=server_name,
                target=target,
            )
        parts = [getattr(item, "text", None) or item.model_dump_json() for item in result.content]
        if result.structured_content and not parts:
            parts.append(str(result.structured_content))
        text = "\n".join(part for part in parts if part).strip() or "(no output)"
        if result.is_error:
            raise McpToolError(
                f'MCP tool "{target}" failed: {text}', server_name=server_name, target=target
            )
        return text

    async def read_resource(self, server_name: str, uri: str) -> str:
        target = f"{server_name}::{uri}"
        session = self._require_session(server_name)
        result = await self._bounded(
            server_name, target, "resource read", session.read_resource(uri)
        )
        if not isinstance(result, ReadResourceResult):
            raise McpProtocolError(
                f'MCP server "{server_name}" returned an unsupported '
                f"{type(result).__name__} for a resource read.",
                server_name=server_name,
                target=target,
            )
        parts = [
            getattr(item, "text", None) or str(getattr(item, "blob", ""))
            for item in result.contents
        ]
        return "\n".join(parts).strip()

    async def _bounded(
        self, server_name: str, target: str, operation: str, call: Awaitable[_T]
    ) -> _T:
        timeout = self._configs[server_name].call_timeout_seconds
        try:
            return await asyncio.wait_for(call, timeout)
        except TimeoutError as error:
            raise McpTimeoutError(
                f'MCP {operation} "{target}" did not finish within {timeout:g}s.',
                server_name=server_name,
                target=target,
            ) from error
        except McpClientError:
            raise
        except Exception as error:
            raise McpTransportError(
                f'MCP {operation} "{target}" failed: {error}',
                server_name=server_name,
                target=target,
            ) from error

    def _require_session(self, server_name: str) -> ClientSession:
        session = self._sessions.get(server_name)
        if session is None:
            status = self._statuses.get(server_name)
            detail = status.detail if status else "unknown server"
            raise McpServerNotConnectedError(
                f'MCP server "{server_name}" is not connected: {detail}',
                server_name=server_name,
            )
        return session

    async def _connect(self, name: str) -> None:
        config = self._configs[name]
        connection = _Connection()
        connection.task = asyncio.create_task(
            self._run_connection(name, config, connection), name=f"mcp-client:{name}"
        )
        self._connections[name] = connection
        try:
            await asyncio.wait_for(asyncio.shield(connection.ready), config.connect_timeout_seconds)
        except TimeoutError:
            await self._close_one(name, cancel=True)
            self._mark_failed(
                name,
                config,
                McpErrorKind.TIMEOUT,
                f"Did not connect within {config.connect_timeout_seconds:g}s.",
            )
        except Exception as error:
            await self._close_one(name)
            self._mark_failed(
                name, config, McpErrorKind.TRANSPORT, str(error) or type(error).__name__
            )

    async def _run_connection(
        self, name: str, config: McpServerConfig, connection: _Connection
    ) -> None:
        try:
            async with AsyncExitStack() as stack:
                if isinstance(config, McpStdioServerConfig):
                    read_stream, write_stream = await stack.enter_async_context(
                        stdio_client(
                            StdioServerParameters(
                                command=config.command,
                                args=config.args,
                                env=config.env,
                                cwd=config.cwd,
                            )
                        )
                    )
                else:
                    http_client = await stack.enter_async_context(
                        httpx.AsyncClient(headers=config.headers or None)
                    )
                    read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                        streamable_http_client(config.url, http_client=http_client)
                    )
                session = await self._register_connected_session(
                    name=name,
                    config=config,
                    stack=stack,
                    read_stream=read_stream,
                    write_stream=write_stream,
                    auth_configured=_auth_configured(config),
                )
                if not connection.ready.done():
                    connection.ready.set_result(session)
                await connection.closing.wait()
        except asyncio.CancelledError:
            if not connection.ready.done():
                connection.ready.cancel()
            raise
        except Exception as error:
            if not connection.ready.done():
                connection.ready.set_exception(error)
            else:
                _logger.debug("MCP connection %s ended with an error", name, exc_info=True)
        finally:
            if self._connections.get(name) is connection:
                self._sessions.pop(name, None)

    async def _register_connected_session(
        self,
        *,
        name: str,
        config: McpServerConfig,
        stack: AsyncExitStack,
        read_stream: Any,
        write_stream: Any,
        auth_configured: bool,
    ) -> ClientSession:
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        tool_result = await session.list_tools()
        resources, resources_detail = await self._list_resources_best_effort(name, session)
        self._sessions[name] = session
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
        return session

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
        self, name: str, config: McpServerConfig, kind: McpErrorKind, detail: str
    ) -> None:
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.FAILED,
            transport=_transport_kind(config),
            auth_configured=_auth_configured(config),
            detail=detail,
            error_kind=kind,
        )

    async def _close_one(self, name: str, *, cancel: bool = False) -> None:
        connection = self._connections.pop(name, None)
        self._sessions.pop(name, None)
        if connection is None or connection.task is None:
            return
        if cancel:  # still connecting: nothing to close gracefully
            connection.task.cancel()
            await asyncio.gather(connection.task, return_exceptions=True)
            return
        connection.closing.set()
        try:
            await asyncio.wait_for(asyncio.shield(connection.task), _CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            connection.task.cancel()
            await asyncio.gather(connection.task, return_exceptions=True)
        except BaseException:
            _logger.debug("MCP connection %s raised during teardown", name, exc_info=True)


def _auth_configured(config: McpServerConfig) -> bool:
    if isinstance(config, McpStdioServerConfig):
        return bool(config.env)
    return bool(config.headers)


def _transport_kind(config: McpServerConfig) -> McpTransportKind:
    if isinstance(config, McpStdioServerConfig):
        return McpTransportKind.STDIO
    return McpTransportKind.HTTP
