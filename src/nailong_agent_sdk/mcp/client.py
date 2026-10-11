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
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import (
    CONNECTION_CLOSED,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    CallToolResult,
    ReadResourceResult,
)

from ..foundations.errors import redact_secrets
from ..foundations.identifiers import require_unique
from ..foundations.logging import get_logger
from .client_types import (
    McpConnectionState,
    McpConnectionStatus,
    McpResourceInfo,
    McpServerConfig,
    McpStdioServerConfig,
    McpToolInfo,
    McpTransportKind,
)

_logger = get_logger("mcp.client")


class McpClientError(RuntimeError):
    pass


class McpServerNotConnectedError(McpClientError):
    pass


class McpToolCallError(McpClientError):
    pass


class McpResourceReadError(McpClientError):
    pass


class McpCallTimeoutError(McpToolCallError, McpResourceReadError):
    def __init__(self, server_name: str, action: str, seconds: float) -> None:
        super().__init__(
            f"MCP {action} timed out after {seconds:g}s without an answer from server "
            f'"{server_name}"; the connection stays open for later calls.'
        )
        self.server_name = server_name
        self.seconds = seconds


@dataclass
class _Connection:
    stop: asyncio.Event
    ready: asyncio.Future[None]
    task: asyncio.Task[None] = field(init=False)
    established: bool = False
    http_statuses: list[int] = field(default_factory=list)


class McpClientManager:
    """Own the lifecycle of a fixed set of outbound MCP server connections."""

    def __init__(self, server_configs: list[McpServerConfig]) -> None:
        names = [config.name for config in server_configs]
        require_unique(names, "MCP server names")
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
        self._pings: dict[str, asyncio.Task[None]] = {}

    async def connect_all(self) -> None:
        """Connect every configured server; a per-server failure does not abort the rest."""

        await asyncio.gather(
            *(self._connect(name) for name in self._configs if name not in self._connections)
        )
        for name in self._configs:
            self._start_ping(name)

    async def reconnect(self, name: str) -> None:
        if name not in self._configs:
            raise ValueError(f'No MCP server named "{name}" is configured.')
        await self._close_one(name)
        await self._connect(name)
        self._start_ping(name)

    async def close(self) -> None:
        """Close every active session. Safe to call more than once."""

        await asyncio.gather(
            *(self._close_one(name) for name in {*self._connections, *self._pings})
        )

    def list_statuses(self) -> list[McpConnectionStatus]:
        return [self._statuses[name] for name in sorted(self._statuses)]

    def list_tools(self) -> list[McpToolInfo]:
        return [tool for status in self.list_statuses() for tool in status.tools]

    def list_resources(self) -> list[McpResourceInfo]:
        return [resource for status in self.list_statuses() for resource in status.resources]

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> str:
        """Invoke one connected tool and return its content flattened to text."""

        _require_positive(timeout_seconds)
        session = self._require_session(server_name)
        action = f'tool call to "{server_name}::{tool_name}"'
        seconds = self._call_timeout(server_name, timeout_seconds)
        try:
            result = await session.call_tool(tool_name, arguments, read_timeout_seconds=seconds)
        except Exception as error:
            raise self._session_failure(
                server_name, action, error, McpToolCallError, seconds
            ) from error
        if not isinstance(result, CallToolResult):
            raise McpToolCallError(
                f'MCP server "{server_name}" returned an unsupported '
                f"{type(result).__name__} for the {action}; interactive mid-call "
                "input requests are not supported by this client."
            )
        parts = [getattr(item, "text", None) or item.model_dump_json() for item in result.content]
        if result.structured_content and not parts:
            parts.append(str(result.structured_content))
        text = "\n".join(part for part in parts if part).strip() or "(no output)"
        if result.is_error:
            raise McpToolCallError(f'MCP tool "{server_name}::{tool_name}" failed: {text}')
        return text

    async def read_resource(
        self, server_name: str, uri: str, *, timeout_seconds: float | None = None
    ) -> str:
        _require_positive(timeout_seconds)
        session = self._require_session(server_name)
        action = f'resource read "{server_name}::{uri}"'
        seconds = self._call_timeout(server_name, timeout_seconds)
        deadline = asyncio.timeout(seconds)
        try:
            async with deadline:
                result = await session.read_resource(uri)
        except Exception as error:
            if seconds is not None and isinstance(error, TimeoutError) and deadline.expired():
                raise McpCallTimeoutError(server_name, action, seconds) from error
            raise self._session_failure(
                server_name, action, error, McpResourceReadError, seconds
            ) from error
        if not isinstance(result, ReadResourceResult):
            raise McpResourceReadError(
                f'MCP server "{server_name}" returned an unsupported '
                f"{type(result).__name__} for the {action}."
            )
        parts = [
            getattr(item, "text", None) or str(getattr(item, "blob", ""))
            for item in result.contents
        ]
        return "\n".join(parts).strip()

    def _call_timeout(self, server_name: str, override: float | None) -> float | None:
        if override is not None:
            return override
        return self._configs[server_name].call_timeout_seconds

    def _require_session(self, server_name: str) -> ClientSession:
        session = self._sessions.get(server_name)
        if session is None:
            status = self._statuses.get(server_name)
            detail = (status.detail or status.state.value) if status else "unknown server"
            raise McpServerNotConnectedError(
                f'MCP server "{server_name}" is not connected: {detail}'
            )
        return session

    def _session_failure(
        self,
        server_name: str,
        action: str,
        error: Exception,
        failure: type[McpClientError],
        timeout_seconds: float | None = None,
    ) -> McpClientError:
        if (
            timeout_seconds is not None
            and isinstance(error, MCPError)
            and error.code == REQUEST_TIMEOUT
        ):
            return McpCallTimeoutError(server_name, action, timeout_seconds)
        if isinstance(error, MCPError) and error.code == CONNECTION_CLOSED:
            self._sessions.pop(server_name, None)
            config = self._configs[server_name]
            current = self._statuses.get(server_name)
            if current is None or current.state is McpConnectionState.CONNECTED:
                self._statuses[server_name] = McpConnectionStatus(
                    name=server_name,
                    state=McpConnectionState.FAILED,
                    transport=_transport_kind(config),
                    auth_configured=_auth_configured(config),
                    detail=f"connection closed by the server during the {action}",
                )
            return McpServerNotConnectedError(
                f'MCP server "{server_name}" closed its connection during the {action}.'
            )
        reason = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
        return failure(f"MCP {action} failed: {reason}")

    async def _connect(self, name: str) -> None:
        config = self._configs[name]
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.PENDING,
            transport=_transport_kind(config),
            auth_configured=_auth_configured(config),
        )
        connection = _Connection(
            stop=asyncio.Event(), ready=asyncio.get_running_loop().create_future()
        )
        connection.task = asyncio.create_task(
            self._hold_connection(name, config, connection), name=f"mcp-client-{name}"
        )
        self._connections[name] = connection
        try:
            await connection.ready
        except asyncio.CancelledError:
            await self._release(name, connection)
            raise
        except (Exception, BaseExceptionGroup) as error:
            await self._release(name, connection)
            self._mark_failed(name, config, error, connection.http_statuses)

    async def _hold_connection(
        self, name: str, config: McpServerConfig, connection: _Connection
    ) -> None:
        ready = connection.ready
        try:
            async with AsyncExitStack() as stack:
                await self._establish(stack, name, config, connection)
                connection.established = True
                if not ready.done():
                    ready.set_result(None)
                await connection.stop.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.set_exception(
                    McpClientError(f'MCP server "{name}" was closed before it finished connecting.')
                )
            raise
        except (Exception, BaseExceptionGroup) as error:
            if ready.done():
                _logger.debug("MCP connection to %s raised during teardown", name, exc_info=True)
            else:
                ready.set_exception(error)

    async def _establish(
        self, stack: AsyncExitStack, name: str, config: McpServerConfig, connection: _Connection
    ) -> None:
        deadline = asyncio.timeout(config.connect_timeout_seconds)
        try:
            async with deadline:
                read_stream, write_stream = await _open_transport(stack, config, connection)
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                tool_result = await session.list_tools()
                resources, resources_detail = await self._list_resources_best_effort(name, session)
        except TimeoutError as error:
            if not deadline.expired():
                raise
            raise TimeoutError(
                f"no initialize response within {config.connect_timeout_seconds:g}s"
            ) from error
        self._sessions[name] = session
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.CONNECTED,
            transport=_transport_kind(config),
            auth_configured=_auth_configured(config),
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
            if isinstance(error, MCPError) and error.code == METHOD_NOT_FOUND:
                return [], None
            return [], f"Resource listing failed: {type(error).__name__}: {error}"
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
        self,
        name: str,
        config: McpServerConfig,
        error: BaseException,
        http_statuses: list[int] | None = None,
    ) -> None:
        detail = _failure_detail(config, error, http_statuses or [])
        _logger.warning("MCP server %s failed to connect: %s", name, detail)
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.FAILED,
            transport=_transport_kind(config),
            auth_configured=_auth_configured(config),
            detail=detail,
        )

    def _start_ping(self, name: str) -> None:
        config = self._configs[name]
        if config.ping_interval_seconds is None or name in self._pings:
            return
        connected = self._statuses[name].state is McpConnectionState.CONNECTED
        if connected or config.reconnect_on_ping_failure:
            self._pings[name] = asyncio.create_task(
                self._ping_loop(name, config, config.ping_interval_seconds),
                name=f"mcp-ping-{name}",
            )

    async def _stop_ping(self, name: str) -> None:
        task = self._pings.get(name)
        if task is None or task is asyncio.current_task():
            return
        del self._pings[name]
        task.cancel()
        await asyncio.wait({task})

    async def _ping_loop(self, name: str, config: McpServerConfig, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            session = self._sessions.get(name)
            if session is not None:
                failure = await _ping_failure(session, config.ping_timeout_seconds)
                if failure is None:
                    continue
                await self._declare_unresponsive(name, config, failure)
            if not config.reconnect_on_ping_failure:
                self._pings.pop(name, None)
                return
            connection = self._connections.get(name)
            if connection is not None:
                await self._release(name, connection)
            await self._connect(name)

    async def _declare_unresponsive(
        self, name: str, config: McpServerConfig, failure: BaseException
    ) -> None:
        detail = _liveness_detail(config, failure)
        _logger.warning("MCP server %s stopped answering: %s", name, detail)
        self._sessions.pop(name, None)
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.FAILED,
            transport=_transport_kind(config),
            auth_configured=_auth_configured(config),
            detail=detail,
        )
        connection = self._connections.get(name)
        if connection is not None:
            await self._release(name, connection)

    async def _close_one(self, name: str) -> None:
        await self._stop_ping(name)
        connection = self._connections.get(name)
        if connection is None:
            return
        await self._release(name, connection)
        config = self._configs[name]
        self._statuses[name] = McpConnectionStatus(
            name=name,
            state=McpConnectionState.CLOSED,
            transport=_transport_kind(config),
            auth_configured=_auth_configured(config),
        )

    async def _release(self, name: str, connection: _Connection) -> None:
        if self._connections.get(name) is connection:
            self._sessions.pop(name, None)
        connection.stop.set()
        if not connection.established:
            connection.task.cancel()
        try:
            await asyncio.wait({connection.task})
        finally:
            if self._connections.get(name) is connection and connection.task.done():
                del self._connections[name]
        if not connection.task.cancelled() and connection.task.exception() is not None:
            _logger.debug(
                "MCP connection task for %s ended with %r", name, connection.task.exception()
            )


async def _open_transport(
    stack: AsyncExitStack, config: McpServerConfig, connection: _Connection
) -> tuple[Any, Any]:
    if isinstance(config, McpStdioServerConfig):
        streams = await stack.enter_async_context(
            stdio_client(
                StdioServerParameters(
                    command=config.command, args=config.args, env=config.env, cwd=config.cwd
                )
            )
        )
    else:
        http_client = create_mcp_http_client(headers=config.headers or None)

        async def record_error_status(response: Any) -> None:
            if response.status_code >= 400:
                connection.http_statuses.append(response.status_code)

        http_client.event_hooks["response"].append(record_error_status)
        await stack.enter_async_context(http_client)
        streams = await stack.enter_async_context(
            streamable_http_client(config.url, http_client=http_client)
        )
    return streams[0], streams[1]


def _transport_kind(config: McpServerConfig) -> McpTransportKind:
    if isinstance(config, McpStdioServerConfig):
        return McpTransportKind.STDIO
    return McpTransportKind.HTTP


def _auth_configured(config: McpServerConfig) -> bool:
    if isinstance(config, McpStdioServerConfig):
        return bool(config.env)
    return bool(config.headers)


def _target(config: McpServerConfig) -> str:
    if isinstance(config, McpStdioServerConfig):
        return f'stdio command "{config.command}"'
    parts = urlsplit(config.url)
    endpoint = f"{parts.scheme}://{parts.hostname or ''}"
    if parts.port is not None:
        endpoint = f"{endpoint}:{parts.port}"
    return f'HTTP endpoint "{endpoint}{parts.path}"'


def _failure_detail(config: McpServerConfig, error: BaseException, http_statuses: list[int]) -> str:
    reasons = "; ".join(_leaf_descriptions(error))
    answered = f"; the endpoint answered HTTP {http_statuses[-1]}" if http_statuses else ""
    return str(redact_secrets(f"{_target(config)} failed to connect: {reasons}{answered}"))


def _liveness_detail(config: McpServerConfig, failure: BaseException) -> str:
    if isinstance(failure, TimeoutError):
        reason = f"no answer to a ping within {config.ping_timeout_seconds:g}s"
    else:
        reason = "; ".join(_leaf_descriptions(failure))
    return str(redact_secrets(f"{_target(config)} stopped answering: {reason}"))


def _require_positive(timeout_seconds: float | None) -> None:
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be positive, got {timeout_seconds:g}.")


async def _ping_failure(session: ClientSession, timeout_seconds: float) -> BaseException | None:
    try:
        async with asyncio.timeout(timeout_seconds):
            await session.send_ping()
    except asyncio.CancelledError:
        raise
    except (Exception, BaseExceptionGroup) as failure:
        return failure
    return None


def _leaf_descriptions(error: BaseException) -> list[str]:
    if isinstance(error, BaseExceptionGroup):
        return [text for inner in error.exceptions for text in _leaf_descriptions(inner)]
    if isinstance(error, MCPError):
        return [f"MCPError({error.code}): {error.message}"]
    message = str(error)
    return [f"{type(error).__name__}: {message}" if message else type(error).__name__]
