# Copyright (c) 2026 David Michael Indraputra

"""Deterministic watchdog and bounded process supervision for registered commands."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic_ns
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..observability.telemetry_store import TelemetryStore
from .sandbox_models import EnvironmentPolicy

_OUTPUT_DRAIN_GRACE_SECONDS = 1.0


class ProcessExitKind(StrEnum):
    SUCCEEDED = "succeeded"
    EXIT_NONZERO = "exit-nonzero"
    TIMED_OUT = "timed-out"
    CANCELLED = "cancelled"
    RESOURCE_LIMIT = "resource-limit"


class WatchdogState(StrEnum):
    REGISTERED = "registered"
    STARTED = "started"
    TERMINATING = "terminating"
    KILLED = "killed"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed-out"
    CANCELLED = "cancelled"
    RESOURCE_LIMITED = "resource-limited"


class ResourceLimits(StrictModel):
    """Portable limits plus optional POSIX rlimits applied in the child process."""

    cpu_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_address_space_bytes: int | None = Field(default=None, ge=1)
    max_file_bytes: int | None = Field(default=None, ge=1)


class RetryPolicy(StrictModel):
    max_attempts: int = Field(default=1, ge=1, le=5)
    retryable_exit_kinds: list[ProcessExitKind] = Field(default_factory=list)
    base_backoff_seconds: float = Field(default=0, ge=0, le=60)
    idempotent: bool = False


class CommandTemplate(StrictModel):
    """A registered, host-declared command; ``environment`` is opt-in and unset by default.

    Leaving ``environment`` unset preserves the historical behavior of
    inheriting the full host environment, which many toolchains need for
    ``PATH``, license-server variables, and similar. A host that wants the
    same allowlisting ``NativeSandbox``/``DockerSandbox`` apply, without
    switching this template to run inside one of those backends, sets it
    explicitly per template instead.
    """

    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    timeout_seconds: float = Field(gt=0, le=86_400)
    max_output_bytes: int = Field(default=65_536, ge=1, le=10_000_000)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    environment: EnvironmentPolicy | None = None


class ProcessExecutionRecord(StrictModel):
    template_name: str
    command: list[str]
    started_at_utc: str
    ended_at_utc: str
    duration_ns: int = Field(ge=0)
    pid: int | None = None
    process_group_id: int | None = None
    return_code: int | None = None
    exit_signal: str | None = None
    exit_kind: ProcessExitKind
    error_code: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    resource_limited: bool = False
    output: str
    output_bytes_total: int = Field(ge=0)
    output_bytes_retained: int = Field(ge=0)
    output_truncated_bytes: int = Field(ge=0)
    termination_path: list[str] = Field(default_factory=list)
    resource_limits_enforced: list[str] = Field(default_factory=list)
    resource_limits_unsupported: list[str] = Field(default_factory=list)
    attempts: int = Field(default=1, ge=1)

    @property
    def successful(self) -> bool:
        return self.exit_kind is ProcessExitKind.SUCCEEDED


class WatchdogController:
    """Emit structured watchdog state changes; the supervisor owns the kill action."""

    def __init__(
        self,
        telemetry: TelemetryStore | None = None,
        telemetry_context: TelemetryContext | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._context = telemetry_context

    def emit(self, state: WatchdogState, *, payload: dict[str, Any] | None = None) -> None:
        if self._telemetry is None or self._context is None:
            return
        self._telemetry.emit(
            f"watchdog.{state.value}",
            self._context,
            actor=TelemetryActor(kind="system", identifier="process-supervisor", role="watchdog"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status=state.value,
            payload=payload or {},
        )


class ProcessSupervisor:
    """Run only registered commands with bounded capture, timeout, and process-tree termination."""

    def __init__(
        self,
        templates: list[CommandTemplate] = (),
        *,
        telemetry: TelemetryStore | None = None,
        telemetry_context_factory: Callable[[str], TelemetryContext] | None = None,
    ) -> None:
        self._templates = {template.name: template for template in templates}
        self._telemetry = telemetry
        self._telemetry_context_factory = telemetry_context_factory

    def template(self, name: str) -> CommandTemplate | None:
        return self._templates.get(name)

    async def execute(
        self,
        template_name: str,
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> ProcessExecutionRecord:
        template = self._templates.get(template_name)
        if template is None:
            raise ValueError(f'No registered command template exists for "{template_name}".')
        if not cwd.is_dir():
            raise ValueError(f'Command working directory "{cwd}" does not exist.')
        # An explicit call-site override wins; otherwise a template-declared
        # policy scrubs the environment; otherwise the host is fully inherited.
        effective_env = (
            env
            if env is not None
            else (template.environment.resolve() if template.environment is not None else None)
        )

        attempts = 0
        last: ProcessExecutionRecord | None = None
        while attempts < template.retry_policy.max_attempts:
            attempts += 1
            record = await self._execute_once(
                template, cwd=cwd, attempts=attempts, env=effective_env
            )
            last = record
            retryable = record.exit_kind in template.retry_policy.retryable_exit_kinds
            if record.successful or not retryable or not template.retry_policy.idempotent:
                return record
            if (
                attempts < template.retry_policy.max_attempts
                and template.retry_policy.base_backoff_seconds
            ):
                await asyncio.sleep(template.retry_policy.base_backoff_seconds * attempts)
        if last is None:
            raise RuntimeError("Process supervisor reached an impossible empty attempt state.")
        return last

    async def _execute_once(
        self,
        template: CommandTemplate,
        *,
        cwd: Path,
        attempts: int,
        env: dict[str, str] | None = None,
    ) -> ProcessExecutionRecord:
        started_at = datetime.now(UTC).isoformat()
        started_ns = monotonic_ns()
        context = (
            self._telemetry_context_factory(template.name)
            if self._telemetry_context_factory
            else None
        )
        watchdog = WatchdogController(self._telemetry, context)
        watchdog.emit(
            WatchdogState.REGISTERED, payload={"template": template.name, "attempt": attempts}
        )
        preexec, enforced, unsupported = _resource_preexec(template.resource_limits)
        try:
            process = await asyncio.create_subprocess_exec(
                *template.command,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                **_subprocess_launch_options(preexec),
            )
        except OSError as error:
            detail = (
                f'Registered command "{template.name}" could not start '
                f"{template.command[0]!r}: {type(error).__name__}: {error}"
            )
            watchdog.emit(
                WatchdogState.FAILED,
                payload={"template": template.name, "attempt": attempts, "error": detail},
            )
            if isinstance(error, FileNotFoundError):
                raise FileNotFoundError(error.errno, detail) from error
            if isinstance(error, PermissionError):
                raise PermissionError(error.errno, detail) from error
            raise
        watchdog.emit(
            WatchdogState.STARTED,
            payload={"template": template.name, "attempt": attempts, "pid": process.pid},
        )
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
            watchdog.emit(
                WatchdogState.TERMINATING,
                payload={"template": template.name, "reason": error_code},
            )
            termination_path = await self._terminate_process_group(process)
            watchdog.emit(
                WatchdogState.KILLED,
                payload={"template": template.name, "termination_path": termination_path},
            )
        except asyncio.CancelledError:
            exit_kind = ProcessExitKind.CANCELLED
            error_code = "PROCESS_CANCELLED"
            watchdog.emit(
                WatchdogState.TERMINATING,
                payload={"template": template.name, "reason": error_code},
            )
            termination_path = await self._terminate_process_group(process)
            await _abandon_output(output_task, process)
            watchdog.emit(
                WatchdogState.CANCELLED,
                payload={"template": template.name, "termination_path": termination_path},
            )
            raise
        termination_path.extend(await _drain_output(output_task, process))
        output, total_bytes = bytes(capture.retained), capture.total
        return_code = process.returncode
        exit_signal = _signal_name(return_code)
        if exit_kind is ProcessExitKind.SUCCEEDED:
            if return_code == 0:
                exit_kind = ProcessExitKind.SUCCEEDED
            elif _is_resource_signal(return_code):
                exit_kind = ProcessExitKind.RESOURCE_LIMIT
                error_code = "PROCESS_RESOURCE_LIMIT"
            else:
                exit_kind = ProcessExitKind.EXIT_NONZERO
                error_code = "PROCESS_EXIT_NONZERO"
        ended_at = datetime.now(UTC).isoformat()
        record = ProcessExecutionRecord(
            template_name=template.name,
            command=list(template.command),
            started_at_utc=started_at,
            ended_at_utc=ended_at,
            duration_ns=max(0, monotonic_ns() - started_ns),
            pid=process.pid,
            process_group_id=process.pid if os.name == "posix" else None,
            return_code=return_code,
            exit_signal=exit_signal,
            exit_kind=exit_kind,
            error_code=error_code,
            timed_out=exit_kind is ProcessExitKind.TIMED_OUT,
            cancelled=exit_kind is ProcessExitKind.CANCELLED,
            resource_limited=exit_kind is ProcessExitKind.RESOURCE_LIMIT,
            output=output.decode("utf-8", errors="replace"),
            output_bytes_total=total_bytes,
            output_bytes_retained=len(output),
            output_truncated_bytes=max(0, total_bytes - len(output)),
            termination_path=termination_path,
            resource_limits_enforced=enforced,
            resource_limits_unsupported=unsupported,
            attempts=attempts,
        )
        final_state = {
            ProcessExitKind.SUCCEEDED: WatchdogState.COMPLETED,
            ProcessExitKind.EXIT_NONZERO: WatchdogState.FAILED,
            ProcessExitKind.TIMED_OUT: WatchdogState.TIMED_OUT,
            ProcessExitKind.CANCELLED: WatchdogState.CANCELLED,
            ProcessExitKind.RESOURCE_LIMIT: WatchdogState.RESOURCE_LIMITED,
        }[record.exit_kind]
        watchdog.emit(final_state, payload=record.model_dump(mode="json"))
        return record

    @staticmethod
    async def _terminate_process_group(process: asyncio.subprocess.Process) -> list[str]:
        if process.returncode is not None:
            return ["already-exited"]
        if os.name == "nt":
            return await _terminate_windows_process_tree(process)
        termination_path: list[str] = []
        try:
            os.killpg(process.pid, signal.SIGTERM)
            termination_path.append("SIGTERM")
        except (OSError, ProcessLookupError):
            return ["process-not-found"]
        try:
            await asyncio.wait_for(_wait_for_exit(process), timeout=1)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                termination_path.append("SIGKILL")
            except (OSError, ProcessLookupError):
                return termination_path
            await _wait_for_exit(process)
        return termination_path


def _subprocess_launch_options(preexec: Callable[[], None] | None) -> dict[str, Any]:
    """Return launch options supported by the active platform only."""

    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True, "preexec_fn": preexec}


async def _terminate_windows_process_tree(process: asyncio.subprocess.Process) -> list[str]:
    """Terminate a Windows process tree and always return an auditable outcome.

    ``taskkill /T /F`` is used through an argument vector and includes descendants.
    It is the documented Windows-specific termination mechanism for this supervisor;
    any failure remains a recordable termination path rather than an uncaught error.
    """

    if process.returncode is not None:
        return ["already-exited"]
    taskkill = await asyncio.create_subprocess_exec(
        "taskkill",
        "/PID",
        str(process.pid),
        "/T",
        "/F",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(taskkill.wait(), timeout=5)
    except TimeoutError:
        taskkill.kill()
        await taskkill.wait()
        return ["TASKKILL_TIMEOUT"]
    try:
        await asyncio.wait_for(_wait_for_exit(process), timeout=5)
    except TimeoutError:
        return ["TASKKILL_RETURNED_PROCESS_STILL_RUNNING"]
    if taskkill.returncode == 0:
        return ["TASKKILL_TREE_FORCE"]
    return [f"TASKKILL_EXIT_{taskkill.returncode}"]


class _OutputCapture:
    def __init__(self, stream: asyncio.StreamReader | None, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self.retained = bytearray()
        self.total = 0

    async def run(self) -> None:
        if self._stream is None:
            return
        while chunk := await self._stream.read(65_536):
            self.total += len(chunk)
            remaining = self._limit - len(self.retained)
            if remaining > 0:
                self.retained.extend(chunk[:remaining])


async def _wait_for_exit(process: asyncio.subprocess.Process) -> None:
    delay = 0.005
    while process.returncode is None:
        await asyncio.sleep(delay)
        delay = min(delay * 2, 0.05)


async def _drain_output(
    output_task: asyncio.Task[None], process: asyncio.subprocess.Process
) -> list[str]:
    _done, pending = await asyncio.wait({output_task}, timeout=_OUTPUT_DRAIN_GRACE_SECONDS)
    if not pending:
        output_task.result()
        return []
    await _abandon_output(output_task, process)
    return ["OUTPUT_PIPE_HELD_BY_DESCENDANT"]


async def _abandon_output(
    output_task: asyncio.Task[None], process: asyncio.subprocess.Process
) -> None:
    output_task.cancel()
    await asyncio.gather(output_task, return_exceptions=True)
    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()


def _resource_preexec(
    limits: ResourceLimits,
) -> tuple[Callable[[], None] | None, list[str], list[str]]:
    requested = {
        "cpu_seconds": limits.cpu_seconds,
        "max_address_space_bytes": limits.max_address_space_bytes,
        "max_file_bytes": limits.max_file_bytes,
    }
    requested = {key: value for key, value in requested.items() if value is not None}
    if not requested:
        return None, [], []
    if os.name != "posix":
        return None, [], sorted(requested)
    try:
        import resource
    except ImportError:
        return None, [], sorted(requested)

    applied: list[str] = []
    unsupported: list[str] = []
    limit_specs: list[tuple[int, int, str]] = []
    if limits.cpu_seconds is not None:
        limit_specs.append((resource.RLIMIT_CPU, math.ceil(limits.cpu_seconds), "cpu_seconds"))
    if limits.max_address_space_bytes is not None and hasattr(resource, "RLIMIT_AS"):
        limit_specs.append(
            (resource.RLIMIT_AS, limits.max_address_space_bytes, "max_address_space_bytes")
        )
    elif limits.max_address_space_bytes is not None:
        unsupported.append("max_address_space_bytes")
    if limits.max_file_bytes is not None:
        limit_specs.append((resource.RLIMIT_FSIZE, limits.max_file_bytes, "max_file_bytes"))

    def apply() -> None:
        for resource_id, value, _name in limit_specs:
            hard = value + 1 if resource_id == resource.RLIMIT_CPU else value
            current_hard = resource.getrlimit(resource_id)[1]
            if current_hard != resource.RLIM_INFINITY:
                hard = min(hard, current_hard)
            resource.setrlimit(resource_id, (value, hard))

    applied.extend(name for _resource_id, _value, name in limit_specs)
    return apply, applied, unsupported


def _signal_name(return_code: int | None) -> str | None:
    if return_code is None or return_code >= 0:
        return None
    try:
        return signal.Signals(-return_code).name
    except ValueError:
        return f"SIG{-return_code}"


def _is_resource_signal(return_code: int | None) -> bool:
    if return_code is None or return_code >= 0:
        return False
    return -return_code in {signal.SIGXCPU, signal.SIGXFSZ}
