# Copyright (c) 2026 David Michael Indraputra

"""Pluggable sandbox execution backends for registered command templates.

``AgentRuntimeServices`` deliberately leaves the choice of execution target to
the embedding host. ``SandboxBackend`` is that seam: ``NativeSandbox`` runs a
template as a resource-limited OS subprocess inside a fresh, auto-removed
scratch directory with an explicit environment allowlist; ``DockerSandbox``
runs the same template inside an ephemeral, network-isolated container. Both
return the same ``ProcessExecutionRecord`` shape produced by ``ProcessSupervisor``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import Protocol, runtime_checkable
from uuid import uuid4

from .sandbox_models import DockerSandboxOptions, EnvironmentPolicy
from .supervisor import (
    CommandTemplate,
    ProcessExecutionRecord,
    ProcessExitKind,
    ProcessSupervisor,
    _capture_bounded_output,
)

_CONTAINER_WORKDIR = "/workspace"
_DOCKER_SIGKILL_EXIT_CODE = 137


class DockerUnavailableError(RuntimeError):
    """Raised when the configured Docker binary cannot be located or invoked."""


@runtime_checkable
class SandboxBackend(Protocol):
    """A backend that can execute one registered ``CommandTemplate``."""

    async def run(
        self,
        template: CommandTemplate,
        *,
        cwd: Path,
        environment: EnvironmentPolicy | None = None,
    ) -> ProcessExecutionRecord: ...


class NativeSandbox:
    """Run a template as an OS subprocess inside a fresh, disposable scratch directory."""

    def __init__(self, *, scratch_parent: Path | None = None) -> None:
        self._scratch_parent = scratch_parent

    async def run(
        self,
        template: CommandTemplate,
        *,
        cwd: Path,
        environment: EnvironmentPolicy | None = None,
    ) -> ProcessExecutionRecord:
        parent = self._scratch_parent or cwd
        parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="nailong-sandbox-", dir=str(parent)))
        try:
            env = environment.resolve() if environment is not None else None
            supervisor = ProcessSupervisor([template])
            return await supervisor.execute(template.name, cwd=scratch, env=env)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class DockerSandbox:
    """Run a template inside an ephemeral, ``--rm`` container for stronger isolation.

    The container never shares the host network or filesystem beyond the one
    bind-mounted working directory (plus any explicitly configured extra
    binds), and it is force-removed on exit regardless of outcome.
    """

    def __init__(self, options: DockerSandboxOptions, *, docker_binary: str = "docker") -> None:
        self._options = options
        self._docker_binary = docker_binary

    async def run(
        self,
        template: CommandTemplate,
        *,
        cwd: Path,
        environment: EnvironmentPolicy | None = None,
    ) -> ProcessExecutionRecord:
        if not cwd.is_dir():
            raise ValueError(f'Command working directory "{cwd}" does not exist.')
        container_name = f"nailong-sandbox-{uuid4().hex}"
        env_file = self._write_env_file(environment) if environment is not None else None
        try:
            return await self._run_with_env_file(template, cwd, container_name, env_file)
        finally:
            if env_file is not None:
                env_file.unlink(missing_ok=True)

    async def _run_with_env_file(
        self,
        template: CommandTemplate,
        cwd: Path,
        container_name: str,
        env_file: Path | None,
    ) -> ProcessExecutionRecord:
        argv = self._build_argv(template, cwd, env_file, container_name)

        started_at = datetime.now(UTC).isoformat()
        started_ns = monotonic_ns()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as error:
            raise DockerUnavailableError(
                f'Docker binary "{self._docker_binary}" was not found on PATH.'
            ) from error

        output_task = asyncio.create_task(
            _capture_bounded_output(process.stdout, template.max_output_bytes)
        )
        termination_path: list[str] = []
        exit_kind = ProcessExitKind.SUCCEEDED
        error_code: str | None = None
        try:
            await asyncio.wait_for(process.wait(), timeout=template.timeout_seconds)
        except TimeoutError:
            exit_kind = ProcessExitKind.TIMED_OUT
            error_code = "PROCESS_TIMEOUT"
            termination_path = await self._kill_container(container_name)
            await process.wait()

        output, total_bytes = await output_task
        return_code = process.returncode
        if exit_kind is ProcessExitKind.SUCCEEDED:
            if return_code == 0:
                exit_kind = ProcessExitKind.SUCCEEDED
            elif return_code == _DOCKER_SIGKILL_EXIT_CODE:
                exit_kind = ProcessExitKind.RESOURCE_LIMIT
                error_code = "DOCKER_RESOURCE_LIMIT"
            else:
                exit_kind = ProcessExitKind.EXIT_NONZERO
                error_code = "PROCESS_EXIT_NONZERO"

        ended_at = datetime.now(UTC).isoformat()
        return ProcessExecutionRecord(
            template_name=template.name,
            command=list(template.command),
            started_at_utc=started_at,
            ended_at_utc=ended_at,
            duration_ns=max(0, monotonic_ns() - started_ns),
            pid=process.pid,
            process_group_id=None,
            return_code=return_code,
            exit_signal=None,
            exit_kind=exit_kind,
            error_code=error_code,
            timed_out=exit_kind is ProcessExitKind.TIMED_OUT,
            cancelled=False,
            resource_limited=exit_kind is ProcessExitKind.RESOURCE_LIMIT,
            output=output.decode("utf-8", errors="replace"),
            output_bytes_total=total_bytes,
            output_bytes_retained=len(output),
            output_truncated_bytes=max(0, total_bytes - len(output)),
            termination_path=termination_path,
            resource_limits_enforced=self._enforced_limit_names(),
            resource_limits_unsupported=[],
            attempts=1,
        )

    def _build_argv(
        self,
        template: CommandTemplate,
        cwd: Path,
        env_file: Path | None,
        container_name: str,
    ) -> list[str]:
        options = self._options
        argv = [self._docker_binary, "run", "--rm", "--name", container_name]
        if options.network_disabled:
            argv += ["--network", "none"]
        if options.read_only_root:
            argv += ["--read-only", "--tmpfs", "/tmp"]
        if options.memory_bytes is not None:
            argv += ["--memory", str(options.memory_bytes)]
        if options.cpu_limit is not None:
            argv += ["--cpus", str(options.cpu_limit)]
        argv += ["-v", f"{cwd.resolve()}:{_CONTAINER_WORKDIR}", "-w", _CONTAINER_WORKDIR]
        for bind in options.extra_binds:
            argv += ["-v", bind]
        if env_file is not None:
            argv += ["--env-file", str(env_file)]
        argv.append(options.image)
        argv += list(template.command)
        return argv

    @staticmethod
    def _write_env_file(environment: EnvironmentPolicy) -> Path:
        """Write the scrubbed environment to a private temp file for ``--env-file``.

        Passing values through ``-e NAME=VALUE`` puts them on the ``docker``
        CLI's own command line, which other host processes can read from the
        process list; an env file avoids that exposure.
        """

        scrubbed = environment.resolve()
        descriptor, raw_path = tempfile.mkstemp(prefix="nailong-sandbox-env-", suffix=".env")
        path = Path(raw_path)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for name, value in scrubbed.items():
                handle.write(f"{name}={value}\n")
        return path

    def _enforced_limit_names(self) -> list[str]:
        names = []
        if self._options.memory_bytes is not None:
            names.append("memory_bytes")
        if self._options.cpu_limit is not None:
            names.append("cpu_limit")
        return names

    async def _kill_container(self, container_name: str) -> list[str]:
        try:
            killer = await asyncio.create_subprocess_exec(
                self._docker_binary,
                "kill",
                container_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=10)
        except (OSError, TimeoutError):
            return ["DOCKER_KILL_FAILED"]
        return ["DOCKER_KILL"]
