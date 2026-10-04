# Copyright (c) 2026 David Michael Indraputra

"""Subagent delegation, composed from the existing task and worktree managers.

This is deliberately not a new execution primitive: ``SubagentCoordinator``
wires ``BackgroundTaskManager`` (pollable background lifecycle) to
``AgentWorktreeManager`` (isolated concurrent checkouts) so a host can hand
off a bounded sub-run — typically ``some_base_agent.run(...)`` on its own
git worktree — get back a normal ``TaskRecord`` to poll or await, and be
confident a concurrent delegate's file edits cannot collide with the caller's
own. It never constructs or imports ``BaseAgent`` itself, for the same
cycle-avoidance reason ``BackgroundTaskManager`` does not.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .task_models import TaskRecord, TaskStatus
from .tasks import BackgroundTaskManager
from .worktree_models import AgentWorktreeInfo
from .worktrees import AgentWorktreeManager


@dataclass(frozen=True)
class DelegatedRunContext:
    """What a delegated run factory receives: its isolated worktree, if any."""

    worktree: AgentWorktreeInfo | None


DelegatedRunFactory = Callable[[DelegatedRunContext], Awaitable[Any]]


class SubagentCoordinator:
    """Start, poll, and stop delegated sub-runs tracked in one shared task manager."""

    def __init__(
        self,
        tasks: BackgroundTaskManager,
        *,
        worktrees: AgentWorktreeManager | None = None,
    ) -> None:
        self._tasks = tasks
        self._worktrees = worktrees
        self._delegated_task_ids: list[str] = []
        self._cleanup_warnings: dict[str, list[str]] = {}

    async def delegate(
        self,
        description: str,
        run_factory: DelegatedRunFactory,
        *,
        repository_path: Path | None = None,
        worktree_slug: str | None = None,
        agent_id: str | None = None,
        summarize: Callable[[Any], dict[str, Any] | None] | None = None,
        remove_worktree_when_done: bool = False,
    ) -> TaskRecord:
        """Start one delegated sub-run, optionally isolated in its own git worktree.

        ``run_factory`` receives a :class:`DelegatedRunContext` and returns
        the awaitable to run — typically constructing a ``ScopedAgentTask``
        rooted at ``context.worktree.path`` when a worktree was requested.
        Delegation itself does not bound the sub-run's own duration or
        iteration count; that remains the delegated agent's own
        ``TerminationPolicy``.
        """

        worktree: AgentWorktreeInfo | None = None
        if worktree_slug is not None:
            if self._worktrees is None or repository_path is None:
                raise ValueError(
                    "worktree_slug requires both a worktree manager and a repository_path."
                )
            worktree = await self._worktrees.create_worktree(
                repository_path, worktree_slug, agent_id=agent_id
            )

        warnings: list[str] = []

        async def cleanup() -> None:
            if worktree is None or not remove_worktree_when_done or self._worktrees is None:
                return
            try:
                await self._worktrees.remove_worktree(worktree.slug)
            except Exception as error:
                warnings.append(
                    f'Worktree "{worktree.slug}" was not removed: {type(error).__name__}: {error}'
                )

        async def run_with_cleanup() -> Any:
            try:
                outcome = await run_factory(DelegatedRunContext(worktree=worktree))
            except BaseException:
                await cleanup()
                raise
            await cleanup()
            return outcome

        record = self._tasks.start_agent_task(description, run_with_cleanup(), summarize=summarize)
        self._delegated_task_ids.append(record.task_id)
        self._cleanup_warnings[record.task_id] = warnings
        return record

    def cleanup_warnings(self, task_id: str) -> list[str]:
        return list(self._cleanup_warnings.get(task_id, []))

    def get_delegation(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get_task(task_id)

    def list_delegations(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        """Return only the delegations this coordinator started, not every shared task."""

        records = (self._tasks.get_task(task_id) for task_id in self._delegated_task_ids)
        existing = [record for record in records if record is not None]
        if status is not None:
            existing = [record for record in existing if record.status is status]
        return existing

    async def await_delegation(self, task_id: str, *, timeout: float | None = None) -> TaskRecord:
        return await self._tasks.wait_for(task_id, timeout=timeout)

    async def cancel_delegation(self, task_id: str) -> TaskRecord:
        return await self._tasks.stop_task(task_id)
