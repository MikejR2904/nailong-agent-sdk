"""/status and /doctor: what is configured, and what is missing or risky."""

from __future__ import annotations

import shutil
import sys

from . import credentials
from .approvals import NOT_SANDBOXED
from .config import project_config_path, user_config_path
from .state import ReplState


def status_lines(state: ReplState) -> list[str]:
    config = state.config
    parts = state.parts
    groups = sum(len(group.definitions) for group in parts.mcp_groups)
    return [
        f"project      {config.project}",
        f"model        {config.model} ({config.provider})",
        f"session      {state.session.id}  turns {len(state.session.turns)}",
        f"mode         {parts.mode.value}",
        f"commands in  {state.sandbox_label}",
        f"mcp tools    {groups}",
        f"hooks        {len(config.hooks)}",
        f"subagents    {', '.join(config.agents) or 'none'}",
        f"memory       {', '.join(file.path.name for file in parts.memory) or 'none'}",
        f"skills       {', '.join(item.id for item in parts.skills) or 'none'}",
        f"web fetch    {'on' if config.web else 'off'}",
        f"offline      {'on' if config.offline else 'off'}",
        f"forge        {'on' if parts.forge is not None else 'off'}",
    ]


def doctor_lines(state: ReplState) -> list[str]:
    config = state.config
    checks: list[tuple[bool | None, str]] = [
        (
            bool(state.environ.get(config.api_key_env) or credentials.lookup(config.api_key_env))
            if config.api_key_env
            else True,
            f"API key: environment variable {config.api_key_env} or a saved key"
            if config.api_key_env
            else "API key: none configured (a server that needs no key)",
        ),
        (
            project_config_path(config.project).is_file() or user_config_path(state.home).is_file(),
            "a settings file exists",
        ),
        (shutil.which("git") is not None, "git is on PATH (for /diff and /review)"),
        (
            state.sandbox_label != NOT_SANDBOXED,
            f"commands are sandboxed ({state.sandbox_label})",
        ),
        (
            None,
            f"bubblewrap: {_found('bwrap')}; sandbox-exec: {_found('sandbox-exec')}; "
            f"docker: {_found('docker')}",
        ),
        (sys.version_info >= (3, 12), f"Python {sys.version_info.major}.{sys.version_info.minor}"),
    ]
    if config.ignored_project_keys:
        checks.append(
            (
                False,
                f"ignored {list(config.ignored_project_keys)} in the project settings: hooks and "
                "MCP servers run programs, so they are read only from your user settings or "
                "--mcp-config",
            )
        )
    return [f"  {_mark(ok)} {text}" for ok, text in checks]


def _found(binary: str) -> str:
    return "found" if shutil.which(binary) else "not found"


def _mark(ok: bool | None) -> str:
    return "ok  " if ok else "info" if ok is None else "WARN"
