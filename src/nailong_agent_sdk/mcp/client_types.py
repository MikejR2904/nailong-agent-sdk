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


class McpStdioServerConfig(StrictModel):
    """A local MCP server launched as a child process over stdio."""

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    connect_timeout_seconds: float = Field(
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS, gt=0, le=MAX_CONNECT_TIMEOUT_SECONDS
    )


class McpHttpServerConfig(StrictModel):
    """A remote MCP server reached over the streamable-HTTP transport."""

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None
    connect_timeout_seconds: float = Field(
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS, gt=0, le=MAX_CONNECT_TIMEOUT_SECONDS
    )


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
