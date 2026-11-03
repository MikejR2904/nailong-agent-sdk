"""Plugins: a local folder of commands, skills, subagents, hooks and MCP servers you installed."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .skills import SkillEntry, read_skill
from .user_commands import UserCommand


@dataclass
class PluginBundle:
    names: list[str] = field(default_factory=list)
    commands: dict[str, UserCommand] = field(default_factory=dict)
    skills: dict[str, SkillEntry] = field(default_factory=dict)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    mcp: dict[str, dict[str, Any]] = field(default_factory=dict)


def plugin_directories(home: Path, explicit: list[Path]) -> list[Path]:
    installed = home / ".config" / "nailong" / "plugins"
    found = (
        sorted(path for path in installed.iterdir() if path.is_dir()) if installed.is_dir() else []
    )
    return [*found, *explicit]


def load_plugins(directories: list[Path]) -> PluginBundle:
    bundle = PluginBundle()
    for directory in directories:
        if not directory.is_dir():
            raise ValueError(f"The plugin directory {directory} does not exist.")
        manifest = _manifest(directory)
        name = str(manifest.get("name") or directory.name)
        if name in bundle.names:
            raise ValueError(f'Two plugins are named "{name}".')
        bundle.names.append(name)
        for path in (
            sorted((directory / "commands").glob("*.md"))
            if (directory / "commands").is_dir()
            else []
        ):
            qualified = f"{name}:{path.stem}"
            bundle.commands[qualified] = UserCommand(
                qualified, path.read_text(encoding="utf-8"), path
            )
        for path in (
            sorted((directory / "skills").glob("*.md")) if (directory / "skills").is_dir() else []
        ):
            qualified = f"{name}:{path.stem}"
            bundle.skills[qualified] = read_skill(qualified, path)
        for agent, spec in manifest.get("agents", {}).items():
            bundle.agents[f"{name}_{agent}"] = spec
        bundle.hooks.extend(manifest.get("hooks", []))
        for server, spec in manifest.get("mcp", {}).items():
            bundle.mcp[f"{name}_{server}"] = spec
    return bundle


def _manifest(directory: Path) -> dict[str, Any]:
    path = directory / "plugin.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path} is not valid TOML: {error}") from error
