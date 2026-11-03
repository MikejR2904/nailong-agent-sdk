# Copyright (c) 2026 David Michael Indraputra

"""Coding tools for a project directory: read, search, write, edit and run commands."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..tools.policy import sensitive_pattern_for
from ..tools.sandbox import DockerSandbox, SandboxBackend
from ..tools.sandbox_models import (
    DockerSandboxOptions,
    EnvironmentPolicy,
    default_native_environment,
)
from ..tools.supervisor import CommandTemplate, ProcessSupervisor, ResourceLimits
from .core_tools import (
    PRIMARY_ROOT,
    CoreTools,
    MultiRootCoreTools,
    core_tools,
    multi_root_read_tools,
)
from .tools import FunctionTool, tool

MAX_FILE_BYTES = 1_000_000
_SHELL_TEMPLATE = "shell"
_PROTECTED_PARTS = frozenset({".git"})


def dev_tools(
    root: Path,
    *,
    allow_writes: bool = True,
    approve_writes: bool = True,
    allow_commands: bool = True,
    approve_commands: bool = True,
    command_timeout_seconds: float = 120.0,
    max_output_bytes: int = 65_536,
    cpu_seconds: int | None = None,
    environment: Sequence[str] = (),
    docker_image: str | None = None,
    sandbox: SandboxBackend | None = None,
    max_file_bytes: int = MAX_FILE_BYTES,
    protected: Sequence[str] = (),
    extra_roots: Mapping[str, Path] | None = None,
) -> list[FunctionTool | CoreTools | MultiRootCoreTools]:
    base = root.resolve()
    if not base.is_dir():
        raise ValueError(f"The project directory {base} does not exist.")
    if docker_image is not None and sandbox is not None:
        raise ValueError("Pass either docker_image or sandbox, not both.")
    roots = _roots(base, extra_roots or {})
    tools: list[FunctionTool | CoreTools | MultiRootCoreTools] = [
        multi_root_read_tools(roots) if extra_roots else core_tools(base)
    ]
    if allow_writes:
        tools += _write_tools(roots, approve_writes, max_file_bytes, frozenset(protected))
    if allow_commands:
        backend = sandbox or (
            DockerSandbox(DockerSandboxOptions(image=docker_image)) if docker_image else None
        )
        tools.append(
            _command_tool(
                base,
                approve_commands,
                command_timeout_seconds,
                max_output_bytes,
                cpu_seconds,
                environment,
                backend,
            )
        )
    return tools


def _roots(base: Path, extra: Mapping[str, Path]) -> dict[str, Path]:
    roots = {PRIMARY_ROOT: base}
    for name, path in extra.items():
        resolved = path.resolve()
        if name == PRIMARY_ROOT or name in roots:
            raise ValueError(f'The root name "{name}" is already used.')
        if not resolved.is_dir():
            raise ValueError(f'The added directory "{name}" ({resolved}) does not exist.')
        roots[name] = resolved
    return roots


def _root_for(roots: Mapping[str, Path], name: str) -> Path:
    chosen = name or PRIMARY_ROOT
    if chosen not in roots:
        raise ValueError(f'There is no root named "{chosen}"; the roots are {sorted(roots)}.')
    return roots[chosen]


def _write_tools(
    roots: Mapping[str, Path],
    needs_approval: bool,
    max_file_bytes: int,
    protected: frozenset[str],
) -> list[FunctionTool]:
    def write_file(path: str, content: str, root: str = "") -> dict[str, Any]:
        target = _confined(_root_for(roots, root), path, protected)
        data = content.encode("utf-8")
        if len(data) > max_file_bytes:
            raise ValueError(
                f'"{path}" would be {len(data)} bytes; the limit for one write is {max_file_bytes}.'
            )
        existed = target.exists()
        _write_bytes(target, data)
        return {"path": path, "bytes": len(data), "created": not existed}

    def edit_file(
        path: str, old_text: str, new_text: str, replace_all: bool = False, root: str = ""
    ) -> dict[str, Any]:
        target = _confined(_root_for(roots, root), path, protected)
        if not target.is_file():
            raise ValueError(f'"{path}" is not an existing file, so there is nothing to edit.')
        if not old_text:
            raise ValueError("old_text must not be empty.")
        text = target.read_bytes().decode("utf-8")
        found = text.count(old_text)
        if found == 0:
            raise ValueError(f'old_text does not occur in "{path}".')
        if found > 1 and not replace_all:
            raise ValueError(
                f'old_text occurs {found} times in "{path}"; add surrounding context so it '
                "matches once, or set replace_all."
            )
        updated = (
            text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        )
        data = updated.encode("utf-8")
        if len(data) > max_file_bytes:
            raise ValueError(
                f'The edited "{path}" would be {len(data)} bytes; the limit is {max_file_bytes}.'
            )
        _write_bytes(target, data)
        return {"path": path, "replacements": found if replace_all else 1}

    return [
        tool(
            write_file,
            description=(
                "Create or overwrite a UTF-8 text file inside the project directory (or inside "
                "the added directory named by root)."
            ),
            needs_approval=needs_approval,
        ),
        tool(
            edit_file,
            description=(
                "Replace exact text in an existing project file. old_text must match once "
                "unless replace_all is set."
            ),
            needs_approval=needs_approval,
        ),
    ]


def _command_tool(
    base: Path,
    needs_approval: bool,
    timeout_limit: float,
    max_output_bytes: int,
    cpu_seconds: int | None,
    extra_variables: Sequence[str],
    backend: SandboxBackend | None,
) -> FunctionTool:
    defaults = default_native_environment()
    policy = EnvironmentPolicy(
        allowed_variable_names=[*defaults.allowed_variable_names, *extra_variables]
    )

    async def run_command(command: str, timeout_seconds: float | None = None) -> dict[str, Any]:
        if not command.strip():
            raise ValueError("command must not be empty.")
        seconds = min(timeout_seconds or timeout_limit, timeout_limit)
        argv, script = shell_argv(command, containerized=backend is not None)
        template = CommandTemplate(
            name=_SHELL_TEMPLATE,
            command=argv,
            timeout_seconds=seconds,
            max_output_bytes=max_output_bytes,
            resource_limits=ResourceLimits(cpu_seconds=cpu_seconds),
            environment=policy,
        )
        try:
            if backend is not None:
                record = await backend.run(template, cwd=base, environment=policy)
            else:
                supervisor = ProcessSupervisor([template])
                record = await supervisor.execute(_SHELL_TEMPLATE, cwd=base, env=policy.resolve())
        finally:
            if script is not None:
                script.unlink(missing_ok=True)
        return {
            "exit_code": record.return_code,
            "outcome": record.exit_kind.value,
            "output": record.output,
            "output_truncated_bytes": record.output_truncated_bytes,
            "seconds": round(record.duration_ns / 1e9, 3),
        }

    return tool(
        run_command,
        description=(
            "Run one shell command in the project directory and return its exit code and "
            "combined output. Output and run time are limited."
        ),
        needs_approval=needs_approval,
    )


def shell_argv(command: str, *, containerized: bool) -> tuple[list[str], Path | None]:
    if containerized:
        return ["sh", "-c", command], None
    if os.name == "nt":
        script = _batch_file(command)
        return ["cmd.exe", "/d", "/c", str(script)], script
    return ["/bin/sh", "-c", command], None


def _batch_file(command: str) -> Path:
    lines = ["@echo off", "@chcp 65001 >nul", command, ""]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".cmd", delete=False, encoding="utf-8", newline="\r\n"
    ) as handle:
        handle.write("\n".join(lines))
        return Path(handle.name)


def _confined(base: Path, relative_path: str, protected: frozenset[str] = frozenset()) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or not relative_path.strip():
        raise ValueError("Paths must be non-empty and relative to the project directory.")
    target = (base / candidate).resolve()
    try:
        parts = target.relative_to(base).parts
    except ValueError as error:
        raise ValueError(f'"{relative_path}" escapes the project directory.') from error
    if sensitive_pattern_for(target) is not None:
        raise ValueError(f'"{relative_path}" is a protected credential path.')
    if (_PROTECTED_PARTS | protected) & set(parts) or any(
        part.startswith(".agent-") for part in parts
    ):
        raise ValueError(f'"{relative_path}" is inside a directory the tools do not write to.')
    return target


def _write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_temporary_path(target)
    try:
        temporary.write_bytes(data)
        replace_atomic(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
