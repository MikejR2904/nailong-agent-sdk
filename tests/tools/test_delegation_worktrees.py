import asyncio
from pathlib import Path

import pytest

from nailong_agent_sdk.tools.delegation import DelegatedRunContext, SubagentCoordinator
from nailong_agent_sdk.tools.task_models import TaskStatus
from nailong_agent_sdk.tools.tasks import BackgroundTaskManager
from nailong_agent_sdk.tools.worktrees import AgentWorktreeManager
from tests.support.tools import run


def test_delegate_with_worktree_runs_and_cleans_up(repo, tmp_path):
    tasks = BackgroundTaskManager()
    worktrees = AgentWorktreeManager(tmp_path / "wt")
    coordinator = SubagentCoordinator(tasks, worktrees=worktrees)

    async def factory(context: DelegatedRunContext):
        (Path(context.worktree.path) / "made.txt").write_text("delegate output", "utf-8")
        return {"path": context.worktree.path, "ok": True}

    async def scenario():
        record = await coordinator.delegate(
            "do work",
            factory,
            repository_path=repo,
            worktree_slug="del-1",
            agent_id="a1",
            remove_worktree_when_done=True,
        )
        final = await coordinator.await_delegation(record.task_id, timeout=120)
        return record, final

    record, final = run(scenario(), timeout=180)
    assert final.status is TaskStatus.COMPLETED and final.result["ok"] is True
    assert not Path(final.result["path"]).exists() and worktrees.list_worktrees() == []
    assert [r.task_id for r in coordinator.list_delegations()] == [record.task_id]


def test_delegate_failure_cleans_up_and_reports(repo, tmp_path):
    tasks = BackgroundTaskManager()
    worktrees = AgentWorktreeManager(tmp_path / "wt")
    coordinator = SubagentCoordinator(tasks, worktrees=worktrees)

    async def factory(context):
        raise ValueError("delegate failed on purpose")

    async def scenario():
        record = await coordinator.delegate(
            "x",
            factory,
            repository_path=repo,
            worktree_slug="del-2",
            remove_worktree_when_done=True,
        )
        return await coordinator.await_delegation(record.task_id, timeout=120)

    final = run(scenario(), timeout=180)
    assert (
        final.status is TaskStatus.FAILED
        and final.error == "ValueError: delegate failed on purpose"
    )
    assert worktrees.list_worktrees() == []


def test_delegate_cancel_cleans_up_worktree(repo, tmp_path):
    tasks = BackgroundTaskManager()
    worktrees = AgentWorktreeManager(tmp_path / "wt")
    coordinator = SubagentCoordinator(tasks, worktrees=worktrees)

    async def factory(context):
        await asyncio.sleep(60)

    async def scenario():
        record = await coordinator.delegate(
            "x",
            factory,
            repository_path=repo,
            worktree_slug="del-3",
            remove_worktree_when_done=True,
        )
        await asyncio.sleep(0.5)
        stopped = await coordinator.cancel_delegation(record.task_id)
        return stopped

    stopped = run(scenario(), timeout=180)
    assert stopped.status is TaskStatus.KILLED
    assert worktrees.list_worktrees() == [] and not (tmp_path / "wt" / "del-3").exists()


def test_delegate_argument_errors(repo, tmp_path):
    coordinator = SubagentCoordinator(BackgroundTaskManager())

    async def factory(context):
        return None

    with pytest.raises(ValueError, match="worktree_slug requires both a worktree manager"):
        run(coordinator.delegate("x", factory, worktree_slug="s"))


def test_worktree_removal_failure_does_not_replace_a_successful_result(repo, tmp_path, monkeypatch):
    tasks = BackgroundTaskManager()
    worktrees = AgentWorktreeManager(tmp_path / "wt")
    coordinator = SubagentCoordinator(tasks, worktrees=worktrees)

    async def factory(context):
        return {"important": "result"}

    original = worktrees.remove_worktree

    async def failing_remove(slug):
        raise RuntimeError("git worktree remove failed: simulated lock")

    async def scenario():
        record = await coordinator.delegate(
            "x",
            factory,
            repository_path=repo,
            worktree_slug="del-4",
            remove_worktree_when_done=True,
        )
        monkeypatch.setattr(worktrees, "remove_worktree", failing_remove)
        final = await coordinator.await_delegation(record.task_id, timeout=120)
        monkeypatch.setattr(worktrees, "remove_worktree", original)
        await original("del-4")
        return final

    final = run(scenario(), timeout=180)
    assert final.status is TaskStatus.COMPLETED and final.result == {"important": "result"}
    warnings_for_task = coordinator.cleanup_warnings(final.task_id)
    assert len(warnings_for_task) == 1 and "simulated lock" in warnings_for_task[0]
    assert 'Worktree "del-4" was not removed' in warnings_for_task[0]
