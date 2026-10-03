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


class McpErrorKind(StrEnum):
    """Why an outbound MCP operation failed, so callers can react per cause."""

    NOT_CONNECTED = "not-connected"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    TOOL_ERROR = "tool-error"
    PROTOCOL = "protocol"


class McpConnectionState(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"


class McpStdioServerConfig(StrictModel):
    """A local MCP server launched as a child process over stdio."""

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    connect_timeout_seconds: float = Field(default=30.0, gt=0)
    call_timeout_seconds: float = Field(default=120.0, gt=0)


class McpHttpServerConfig(StrictModel):
    """A remote MCP server reached over the streamable-HTTP transport."""

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: dict[str, str] | None = None
    connect_timeout_seconds: float = Field(default=30.0, gt=0)
    call_timeout_seconds: float = Field(default=120.0, gt=0)


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
    error_kind: McpErrorKind | None = None
    tools: list[McpToolInfo] = Field(default_factory=list)
    resources: list[McpResourceInfo] = Field(default_factory=list)
