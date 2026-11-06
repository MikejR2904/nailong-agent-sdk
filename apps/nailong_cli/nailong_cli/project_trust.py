from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

TRUST_FILE = "trusted-project-settings.json"
SHARED_KEYS = ("hooks", "mcp")


def trust_path(home: Path) -> Path:
    return home / ".config" / "nailong" / TRUST_FILE


def shared_part(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in SHARED_KEYS if data.get(key)}


def digest(shared: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(shared, sort_keys=True, default=str).encode()).hexdigest()


def _known(home: Path) -> dict[str, str]:
    path = trust_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"The trusted project settings file {path} cannot be read: "
            f"{type(error).__name__}: {error}. Delete it to approve each project's hooks and "
            "MCP servers again."
        ) from error
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def is_trusted(home: Path, config_file: Path, shared: dict[str, Any]) -> bool:
    return _known(home).get(str(config_file.resolve())) == digest(shared)


def trust(home: Path, config_file: Path, shared: dict[str, Any]) -> None:
    known = _known(home)
    known[str(config_file.resolve())] = digest(shared)
    target = trust_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(known, indent=2), encoding="utf-8")


def describe(shared: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for hook in shared.get("hooks", ()):
        lines.append(
            f"hook on {hook.get('event', '?')} (tool {hook.get('tool', '*')}) runs: "
            f"{hook.get('command', '?')}"
        )
    for name, spec in shared.get("mcp", {}).items():
        if spec.get("command"):
            command = " ".join([str(spec["command"]), *map(str, spec.get("args", []))])
            lines.append(f"MCP server {name} starts: {command}")
        else:
            lines.append(f"MCP server {name} connects to: {spec.get('url', '?')}")
    return lines
