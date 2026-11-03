"""MCP servers from the settings file or a --mcp-config JSON file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nailong_agent_sdk.mcp.client_types import (
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
)


def servers_from_table(table: dict[str, dict[str, Any]]) -> list[McpServerConfig]:
    servers: list[McpServerConfig] = []
    for name, spec in table.items():
        if spec.get("command"):
            servers.append(
                McpStdioServerConfig(
                    name=name,
                    command=spec["command"],
                    args=list(spec.get("args", [])),
                    env=dict(spec.get("env", {})),
                    cwd=spec.get("cwd"),
                )
            )
        else:
            servers.append(
                McpHttpServerConfig(
                    name=name, url=spec["url"], headers=dict(spec.get("headers", {}))
                )
            )
    return servers


def table_from_json_file(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read MCP config {path}: {error}") from error
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise ValueError(f'MCP config {path} needs a top-level "mcpServers" object.')
    for name, spec in servers.items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            raise ValueError(f'MCP server "{name}" in {path} needs a command or a url.')
    return servers
