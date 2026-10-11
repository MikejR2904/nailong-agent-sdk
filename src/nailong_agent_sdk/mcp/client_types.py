# Copyright (c) 2026 David Michael Indraputra

"""Typed configuration and status models for the outbound MCP client."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel


class McpTransportKind(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


class McpConnectionState(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"
    CLOSED = "closed"


DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0
MAX_CONNECT_TIMEOUT_SECONDS = 3_600.0
MAX_CALL_TIMEOUT_SECONDS = 86_400.0
DEFAULT_PING_TIMEOUT_SECONDS = 10.0
MAX_PING_SECONDS = 3_600.0


class McpServerOptions(StrictModel):
    """The settings every outbound MCP server connection takes, whatever its transport."""

    name: str = Field(min_length=1)
    connect_timeout_seconds: float = Field(
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS, gt=0, le=MAX_CONNECT_TIMEOUT_SECONDS
    )
    call_timeout_seconds: float | None = Field(default=None, gt=0, le=MAX_CALL_TIMEOUT_SECONDS)
    ping_interval_seconds: float | None = Field(default=None, gt=0, le=MAX_PING_SECONDS)
    ping_timeout_seconds: float = Field(
        default=DEFAULT_PING_TIMEOUT_SECONDS, gt=0, le=MAX_PING_SECONDS
    )
    reconnect_on_ping_failure: bool = False


class McpStdioServerConfig(McpServerOptions):
    """A local MCP server launched as a child process over stdio."""

    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class McpHttpServerConfig(McpServerOptions):
    """A remote MCP server reached over the streamable-HTTP transport."""

    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None


McpServerConfig = McpStdioServerConfig | McpHttpServerConfig


class McpToolInfo(StrictModel):
    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any]


class McpResourceInfo(StrictModel):
    server_name: str
    name: str
    uri: str
    description: str


class McpConnectionStatus(StrictModel):
    name: str
    state: McpConnectionState
    transport: McpTransportKind
    auth_configured: bool = False
    detail: str | None = None
    tools: list[McpToolInfo] = Field(default_factory=list)
    resources: list[McpResourceInfo] = Field(default_factory=list)
