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
import contextlib
import os
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import Protocol, runtime_checkable
from uuid import uuid4

from .sandbox_models import DockerSandboxOptions, EnvironmentPolicy, default_native_environment
from .supervisor import (
    CommandTemplate,
    ProcessExecutionRecord,
    ProcessExitKind,
    ProcessSupervisor,
    _abandon_output,
    _drain_output,
    _OutputCapture,
    _wait_for_exit,
)

_CONTAINER_WORKDIR = "/workspace"
_DOCKER_SIGKILL_EXIT_CODE = 137
_DOCKER_KILL_TIMEOUT_SECONDS = 10.0
_DOCKER_POST_KILL_WAIT_SECONDS = 5.0


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
            env = (environment or default_native_environment()).resolve()
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
        inherited = self._inherited_variables(environment) if environment is not None else {}
        try:
            return await self._run_with_env_file(template, cwd, container_name, env_file, inherited)
        finally:
            if env_file is not None:
                env_file.unlink(missing_ok=True)

    async def _run_with_env_file(
        self,
        template: CommandTemplate,
        cwd: Path,
        container_name: str,
        env_file: Path | None,
        inherited: dict[str, str],
    ) -> ProcessExecutionRecord:
        argv = self._build_argv(template, cwd, env_file, container_name, sorted(inherited))
        cli_env = {**os.environ, **inherited} if inherited else None

        started_at = datetime.now(UTC).isoformat()
        started_ns = monotonic_ns()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=cli_env,
            )
        except FileNotFoundError as error:
            raise DockerUnavailableError(
                f'Docker binary "{self._docker_binary}" was not found on PATH.'
            ) from error

        capture = _OutputCapture(process.stdout, template.max_output_bytes)
        output_task = asyncio.create_task(capture.run())
        termination_path: list[str] = []
        exit_kind = ProcessExitKind.SUCCEEDED
        error_code: str | None = None
        try:
            await asyncio.wait_for(_wait_for_exit(process), timeout=template.timeout_seconds)
        except TimeoutError:
            exit_kind = ProcessExitKind.TIMED_OUT
            error_code = "PROCESS_TIMEOUT"
            termination_path = await self._stop_container(process, container_name)
        except asyncio.CancelledError:
            await self._stop_container(process, container_name)
            await _abandon_output(output_task, process)
            raise

        termination_path.extend(await _drain_output(output_task, process))
        output, total_bytes = bytes(capture.retained), capture.total
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
        inherited_names: Sequence[str] = (),
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
        for name in inherited_names:
            argv += ["-e", name]
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
                if DockerSandbox._fits_env_file(name, value):
                    handle.write(f"{name}={value}\n")
        return path

    @staticmethod
    def _fits_env_file(name: str, value: str) -> bool:
        DockerSandbox._check_variable_name(name)
        return not any(character in value for character in "\r\n\x00")

    @staticmethod
    def _check_variable_name(name: str) -> None:
        if not name or "=" in name or any(not c.isprintable() or c.isspace() for c in name):
            raise ValueError(
                f"Environment variable name {name!r} cannot be passed to a container: it must "
                "be non-empty and contain no whitespace, control characters or equals sign."
            )

    @staticmethod
    def _inherited_variables(environment: EnvironmentPolicy) -> dict[str, str]:
        return {
            name: value
            for name, value in environment.resolve().items()
            if not DockerSandbox._fits_env_file(name, value)
        }

    def _enforced_limit_names(self) -> list[str]:
        names = []
        if self._options.memory_bytes is not None:
            names.append("memory_bytes")
        if self._options.cpu_limit is not None:
            names.append("cpu_limit")
        return names

    async def _stop_container(
        self, process: asyncio.subprocess.Process, container_name: str
    ) -> list[str]:
        termination_path = await self._docker_admin(container_name, "kill", "DOCKER_KILL")
        try:
            await asyncio.wait_for(_wait_for_exit(process), timeout=_DOCKER_POST_KILL_WAIT_SECONDS)
        except TimeoutError:
            termination_path += await self._docker_admin(
                container_name, "rm", "DOCKER_RM", "--force"
            )
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            termination_path.append("DOCKER_CLI_KILLED")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    _wait_for_exit(process), timeout=_DOCKER_KILL_TIMEOUT_SECONDS
                )
        return termination_path

    async def _kill_container(self, container_name: str) -> list[str]:
        return await self._docker_admin(container_name, "kill", "DOCKER_KILL")

    async def _docker_admin(
        self, container_name: str, verb: str, label: str, *flags: str
    ) -> list[str]:
        try:
            admin = await asyncio.create_subprocess_exec(
                self._docker_binary,
                verb,
                *flags,
                container_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return [f"{label}_FAILED"]
        try:
            await asyncio.wait_for(admin.wait(), timeout=_DOCKER_KILL_TIMEOUT_SECONDS)
        except TimeoutError:
            admin.kill()
            await admin.wait()
            return [f"{label}_FAILED"]
        return [label] if admin.returncode == 0 else [f"{label}_FAILED"]
