# Copyright (c) 2026 David Michael Indraputra

"""Background task lifecycle: start, poll, and stop long-running work.

Two kinds of work are trackable. A command task runs one registered
``CommandTemplate`` through a caller-selected ``SandboxBackend`` — never a raw
shell string, matching the harness's closed-capability boundary. An agent-run
task tracks any host-supplied coroutine (typically a ``BaseAgent.run`` call);
this module never constructs or imports an agent itself, since ``agent``
already depends on ``tools`` and importing it back here would form a cycle.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..foundations.logging import get_logger
from .sandbox import SandboxBackend
from .sandbox_models import EnvironmentPolicy
from .supervisor import CommandTemplate
from .task_models import TaskKind, TaskRecord, TaskStatus

TaskTransitionListener = Callable[[TaskRecord], None]
_logger = get_logger("tools.tasks")


class BackgroundTaskManager:
    """Own every tracked background task's record and underlying ``asyncio.Task``.

    Background work runs outside any single agent turn, so without an explicit
    hook its outcome would never reach telemetry or the audit log the way
    every other tool call does. ``on_transition``, if given, is called with
    the updated record on every status change (including the initial
    ``RUNNING`` registration); a host wires it to ``TelemetryStore.emit`` or
    similar to keep background tasks inside the same observability pipeline.
    A raising listener does not fail the task — it is reported by the task
    that was already in flight, not by task bookkeeping.
    """

    def __init__(
        self,
        *,
        on_transition: TaskTransitionListener | None = None,
        max_retained_finished_tasks: int = 1_000,
    ) -> None:
        if max_retained_finished_tasks < 1:
            raise ValueError("max_retained_finished_tasks must be at least one.")
        self._records: dict[str, TaskRecord] = {}
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._next_sequence = 1
        self._on_transition = on_transition
        self._max_retained_finished = max_retained_finished_tasks

    def start_command_task(
        self,
        description: str,
        backend: SandboxBackend,
        template: CommandTemplate,
        *,
        cwd: Path,
        environment: EnvironmentPolicy | None = None,
    ) -> TaskRecord:
        """Start one registered command template in the background."""

        _require_running_loop("start_command_task")
        task_id = self._allocate_id(TaskKind.COMMAND_TEMPLATE)
        record = self._register(task_id, TaskKind.COMMAND_TEMPLATE, description)
        coroutine = backend.run(template, cwd=cwd, environment=environment)
        self._running[task_id] = asyncio.create_task(self._run_command(task_id, coroutine))
        return record

    def start_agent_task(
        self,
        description: str,
        run: Awaitable[Any],
        *,
        summarize: Any = None,
    ) -> TaskRecord:
        """Track a host-supplied coroutine (typically a ``BaseAgent.run`` call).

        ``summarize``, if given, receives the coroutine's return value and
        must return a JSON-serializable summary to store on the completed
        record; without it, the raw return value is stored only when it is
        already a plain ``dict``.
        """

        try:
            _require_running_loop("start_agent_task")
        except RuntimeError:
            if inspect.iscoroutine(run):
                run.close()
            raise
        task_id = self._allocate_id(TaskKind.AGENT_RUN)
        record = self._register(task_id, TaskKind.AGENT_RUN, description)
        self._running[task_id] = asyncio.create_task(self._run_agent(task_id, run, summarize))
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def list_tasks(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        records = list(self._records.values())
        if status is not None:
            records = [record for record in records if record.status is status]
        return sorted(records, key=lambda record: record.created_at_utc)

    async def stop_task(self, task_id: str) -> TaskRecord:
        record = self._require(task_id)
        running = self._running.get(task_id)
        if running is None or running.done():
            return record
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        return self._transition(task_id, TaskStatus.KILLED)

    async def wait_for(self, task_id: str, *, timeout: float | None = None) -> TaskRecord:
        """Block until one task reaches a terminal status, or ``timeout`` elapses."""

        running = self._running.get(task_id)
        if running is not None:
            done, _pending = await asyncio.wait({running}, timeout=timeout)
            if not done:
                raise TimeoutError(f'Task "{task_id}" did not finish within {timeout}s.')
        return self._require(task_id)

    async def _run_command(self, task_id: str, run: Awaitable[Any]) -> None:
        try:
            outcome = await run
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._transition(
                task_id,
                TaskStatus.FAILED,
                error=f"{type(error).__name__}: {error}",
            )
            return
        status = TaskStatus.COMPLETED if outcome.successful else TaskStatus.FAILED
        self._transition(task_id, status, result=outcome.model_dump(mode="json"))

    async def _run_agent(self, task_id: str, run: Awaitable[Any], summarize: Any) -> None:
        try:
            outcome = await run
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._transition(
                task_id,
                TaskStatus.FAILED,
                error=f"{type(error).__name__}: {error}",
            )
            return
        try:
            if summarize is not None:
                result = summarize(outcome)
            elif isinstance(outcome, dict):
                result = outcome
            else:
                result = None
        except Exception as error:
            self._transition(
                task_id,
                TaskStatus.FAILED,
                error=f"summarize raised {type(error).__name__}: {error}",
            )
            return
        self._transition(task_id, TaskStatus.COMPLETED, result=result)

    def _allocate_id(self, kind: TaskKind) -> str:
        task_id = f"task-{kind.value}-{self._next_sequence}"
        self._next_sequence += 1
        return task_id

    def _register(self, task_id: str, kind: TaskKind, description: str) -> TaskRecord:
        now = _now()
        record = TaskRecord(
            task_id=task_id,
            kind=kind,
            description=description,
            status=TaskStatus.RUNNING,
            created_at_utc=now,
            started_at_utc=now,
        )
        self._records[task_id] = record
        self._notify(record)
        return record

    def _transition(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TaskRecord:
        current = self._require(task_id)
        updated = current.model_copy(
            update={"status": status, "ended_at_utc": _now(), "result": result, "error": error}
        )
        self._records[task_id] = updated
        if status is not TaskStatus.RUNNING:
            self._running.pop(task_id, None)
            self._prune_finished()
        self._notify(updated)
        return updated

    def _prune_finished(self) -> None:
        finished = [
            record.task_id
            for record in self._records.values()
            if record.status is not TaskStatus.RUNNING
        ]
        for task_id in finished[: max(0, len(finished) - self._max_retained_finished)]:
            del self._records[task_id]

    def _notify(self, record: TaskRecord) -> None:
        if self._on_transition is None:
            return
        try:
            self._on_transition(record)
        except Exception:
            _logger.exception(
                "on_transition listener raised for task %s (status=%s)",
                record.task_id,
                record.status.value,
            )

    def _require(self, task_id: str) -> TaskRecord:
        record = self._records.get(task_id)
        if record is None:
            raise ValueError(f'No task found with ID "{task_id}".')
        return record


def _require_running_loop(operation: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError as error:
        raise RuntimeError(
            f"BackgroundTaskManager.{operation} must be called from code running inside an "
            "asyncio event loop."
        ) from error


def _now() -> str:
    return datetime.now(UTC).isoformat()
