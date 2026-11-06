"""MCP servers from the settings file or a --mcp-config JSON file."""

from __future__ import annotations

import json
import re
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


MANAGED_FILE = "mcp.json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def managed_path(home: Path) -> Path:
    return home / ".config" / "nailong" / MANAGED_FILE


def load_managed(home: Path) -> dict[str, dict[str, Any]]:
    path = managed_path(home)
    return table_from_json_file(path) if path.is_file() else {}


def _save_managed(home: Path, servers: dict[str, dict[str, Any]]) -> None:
    path = managed_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")


def spec_from_words(words: list[str]) -> dict[str, Any]:
    if not words:
        raise ValueError("Give a command to run (npx -y some-server) or an http(s) address.")
    first = words[0]
    if first.startswith(("http://", "https://")):
        if len(words) > 1:
            raise ValueError("An address takes no further words.")
        return {"url": first}
    return {"command": first, "args": words[1:]}


def add_server(home: Path, name: str, spec: dict[str, Any]) -> None:
    if not _NAME.match(name):
        raise ValueError(
            f'MCP server name "{name}" must be 1 to 32 letters, digits, "-" or "_" characters.'
        )
    servers = load_managed(home)
    if name in servers:
        raise ValueError(f'"{name}" is already added; remove it first.')
    servers[name] = spec
    _save_managed(home, servers)


def remove_server(home: Path, name: str) -> None:
    servers = load_managed(home)
    if name not in servers:
        raise ValueError(f'"{name}" was not added with /mcp add; added: {sorted(servers)}.')
    del servers[name]
    _save_managed(home, servers)


PRESETS: dict[str, dict[str, Any]] = {
    "browser": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
}


def refusal_for(servers: dict[str, dict[str, Any]], tool: str) -> str | None:
    if not tool.startswith("mcp__"):
        return None
    server, _, name = tool.removeprefix("mcp__").partition("__")
    spec = servers.get(server)
    if spec is None:
        return None
    deny = [str(item) for item in spec.get("deny_tools", ())]
    allow = [str(item) for item in spec.get("allow_tools", ())]
    if name in deny:
        return f'{tool} is refused: "{name}" is in deny_tools of the MCP server "{server}".'
    if allow and name not in allow:
        return f'{tool} is refused: the MCP server "{server}" allows only {", ".join(allow)}.'
    return None
