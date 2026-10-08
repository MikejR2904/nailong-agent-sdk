import asyncio
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from nailong_agent_sdk.tools.delegation import DelegatedRunContext, SubagentCoordinator
from nailong_agent_sdk.tools.sandbox import NativeSandbox
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from nailong_agent_sdk.tools.task_models import TaskKind, TaskStatus
from nailong_agent_sdk.tools.tasks import BackgroundTaskManager
from nailong_agent_sdk.tools.worktrees import AgentWorktreeManager, validate_worktree_slug
from tests.support.tools import run

PY = sys.executable


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "audit@example.com")
    git(path, "config", "user.name", "audit")
    (path / "f.txt").write_text("one\n", "utf-8")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")
    return path


def cmd(name, code, timeout=30):
    return CommandTemplate(
        name=name, command=[PY, "-c", textwrap.dedent(code)], timeout_seconds=timeout
    )


def test_command_task_completes_with_ordered_transitions(tmp_path):
    seen = []
    manager = BackgroundTaskManager(on_transition=lambda record: seen.append(record.status))

    async def scenario():
        record = manager.start_command_task(
            "say hi", NativeSandbox(), cmd("hi", "print('hi')"), cwd=tmp_path
        )
        assert record.status is TaskStatus.RUNNING and record.kind is TaskKind.COMMAND_TEMPLATE
        final = await manager.wait_for(record.task_id, timeout=60)
        return record, final

    record, final = run(scenario())
    assert record.task_id == "task-command-template-1"
    assert final.status is TaskStatus.COMPLETED and final.result["output"].strip() == "hi"
    assert final.ended_at_utc and final.error is None
    assert seen == [TaskStatus.RUNNING, TaskStatus.COMPLETED]
    assert manager.get_task("nope") is None
    with pytest.raises(ValueError, match='No task found with ID "nope"'):
        run(manager.wait_for("nope"))


def test_failed_command_task_keeps_result(tmp_path):
    manager = BackgroundTaskManager()

    async def scenario():
        record = manager.start_command_task(
            "fail", NativeSandbox(), cmd("bad", "import sys; sys.exit(4)"), cwd=tmp_path
        )
        return await manager.wait_for(record.task_id, timeout=60)

    final = run(scenario())
    assert final.status is TaskStatus.FAILED and final.result["return_code"] == 4


def test_backend_exception_becomes_failed_with_type_and_message(tmp_path):
    class Boom:
        async def run(self, template, *, cwd, environment=None):
            raise RuntimeError("backend exploded")

    manager = BackgroundTaskManager()

    async def scenario():
        record = manager.start_command_task("x", Boom(), cmd("x", "print(1)"), cwd=tmp_path)
        return await manager.wait_for(record.task_id, timeout=30)

    final = run(scenario())
    assert final.status is TaskStatus.FAILED and final.error == "RuntimeError: backend exploded"


def test_stop_running_command_task_reports_a_single_terminal_state(tmp_path):
    seen = []
    manager = BackgroundTaskManager(on_transition=lambda record: seen.append(record.status.value))

    async def scenario():
        record = manager.start_command_task(
            "sleepy",
            NativeSandbox(),
            cmd("sleepy", "import time; time.sleep(60)", timeout=60),
            cwd=tmp_path,
        )
        await asyncio.sleep(1.0)
        stopped = await manager.stop_task(record.task_id)
        return stopped

    stopped = run(scenario())
    assert stopped.status is TaskStatus.KILLED
    assert seen == ["running", "killed"], seen


def test_stopping_a_task_does_not_swallow_the_cancellation_of_its_caller():
    manager = BackgroundTaskManager()
    outcome = {}

    async def scenario():
        async def slow():
            try:
                await asyncio.sleep(30)
            finally:
                await asyncio.sleep(0.2)

        record = manager.start_agent_task("slow", slow())

        async def stopper():
            await manager.stop_task(record.task_id)
            outcome["returned"] = True

        caller = asyncio.create_task(stopper())
        await asyncio.sleep(0.05)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert caller.cancelled()
        return record

    record = run(scenario())
    assert "returned" not in outcome
    assert manager.get_task(record.task_id).status is TaskStatus.KILLED


def test_stopping_a_task_that_finished_first_keeps_its_own_outcome():
    manager = BackgroundTaskManager()

    async def scenario():
        async def shielded():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return {"finished": "despite the cancel"}

        record = manager.start_agent_task("shielded", shielded())
        await asyncio.sleep(0.05)
        stopped = await manager.stop_task(record.task_id)
        return record, stopped

    record, stopped = run(scenario())
    assert stopped.status is TaskStatus.COMPLETED
    assert stopped.result == {"finished": "despite the cancel"}


def test_wait_for_on_a_stopped_agent_task_returns_the_killed_record():
    manager = BackgroundTaskManager()

    async def work():
        await asyncio.sleep(60)

    async def scenario():
        record = manager.start_agent_task("long", work())
        await asyncio.sleep(0.2)
        stopped = await manager.stop_task(record.task_id)
        try:
            waited = await manager.wait_for(record.task_id, timeout=5)
            outcome = ("returned", waited.status.value)
        except asyncio.CancelledError:
            outcome = ("CancelledError", None)
        return stopped.status.value, outcome

    status, outcome = run(scenario())
    assert status == "killed" and outcome == ("returned", "killed"), outcome


def test_summarize_exception_leaves_the_task_running_forever():
    manager = BackgroundTaskManager()

    async def work():
        return {"ok": True}

    def bad_summary(outcome):
        raise ZeroDivisionError("summary bug")

    async def scenario():
        record = manager.start_agent_task("x", work(), summarize=bad_summary)
        await asyncio.sleep(0.5)
        return manager.get_task(record.task_id)

    final = run(scenario())
    assert final.status is not TaskStatus.RUNNING, "task still RUNNING after its coroutine finished"
    assert final.error and "ZeroDivisionError" in final.error


def test_agent_task_exception_marks_failed():
    manager = BackgroundTaskManager()

    async def work():
        raise KeyError("missing-key")

    async def scenario():
        record = manager.start_agent_task("x", work())
        return await manager.wait_for(record.task_id, timeout=10)

    final = run(scenario())
    assert final.status is TaskStatus.FAILED and final.error == "KeyError: 'missing-key'"


def test_finished_task_records_are_bounded():
    manager = BackgroundTaskManager(max_retained_finished_tasks=5)

    async def work():
        return {"ok": True}

    async def scenario():
        ids = []
        for _ in range(12):
            record = manager.start_agent_task("t", work())
            ids.append(record.task_id)
            await manager.wait_for(record.task_id, timeout=10)
        return ids

    ids = run(scenario())
    assert [record.task_id for record in manager.list_tasks()] == ids[-5:]
    assert manager.get_task(ids[0]) is None


def test_start_agent_task_outside_a_loop_closes_the_coroutine_it_was_given():
    manager = BackgroundTaskManager()

    async def work():
        return {}

    pending = work()
    with pytest.raises(RuntimeError, match="must be called from code running inside an asyncio"):
        manager.start_agent_task("x", pending)
    assert pending.cr_frame is None
    assert manager.list_tasks() == []


def test_start_without_running_loop_leaves_no_ghost_task(tmp_path):
    manager = BackgroundTaskManager()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(RuntimeError):
            manager.start_command_task("x", NativeSandbox(), cmd("x", "print(1)"), cwd=tmp_path)
    running = manager.list_tasks(status=TaskStatus.RUNNING)
    assert running == []


def test_listener_exception_does_not_break_the_task():
    def listener(record):
        raise RuntimeError("listener bug")

    manager = BackgroundTaskManager(on_transition=listener)

    async def work():
        return {"v": 1}

    async def scenario():
        record = manager.start_agent_task("x", work())
        return await manager.wait_for(record.task_id, timeout=10)

    final = run(scenario())
    assert final.status is TaskStatus.COMPLETED and final.result == {"v": 1}


def test_wait_for_timeout_does_not_cancel_the_task():
    manager = BackgroundTaskManager()

    async def work():
        await asyncio.sleep(1.0)
        return {"done": True}

    async def scenario():
        record = manager.start_agent_task("x", work())
        with pytest.raises(TimeoutError):
            await manager.wait_for(record.task_id, timeout=0.2)
        still = manager.get_task(record.task_id).status
        final = await manager.wait_for(record.task_id, timeout=10)
        return still, final

    still, final = run(scenario())
    assert still is TaskStatus.RUNNING and final.status is TaskStatus.COMPLETED


def test_many_concurrent_agent_tasks_and_bookkeeping():
    manager = BackgroundTaskManager()

    async def work(index):
        await asyncio.sleep(0.01)
        return {"index": index}

    async def scenario():
        ids = [manager.start_agent_task(f"t{i}", work(i)).task_id for i in range(300)]
        await asyncio.gather(*(manager.wait_for(task_id, timeout=30) for task_id in ids))
        return ids

    ids = run(scenario())
    assert len(set(ids)) == 300
    assert all(manager.get_task(i).status is TaskStatus.COMPLETED for i in ids)
    listed = manager.list_tasks()
    assert [r.task_id for r in listed] == ids


@pytest.mark.parametrize(
    "slug",
    ["a", "a-b", "a.b", "a_b", "A1", "a/b", "a/b/c", "x" * 64],
)
def test_valid_slugs_pass(slug):
    assert validate_worktree_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "x" * 65,
        "/abs",
        "\\abs",
        "a/../b",
        "..",
        ".",
        "a/./b",
        "a//b",
        "a b",
        "a:b",
        "a+b",
        "a\\b",
        "ü",
        "a/",
        "a\n",
        "a\nb",
    ],
)
def test_invalid_slugs_fail(slug):
    with pytest.raises(ValueError):
        validate_worktree_slug(slug)


def test_worktree_create_list_remove_roundtrip(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        info = await manager.create_worktree(repo, "agent/one", agent_id="agent-1")
        again = await manager.create_worktree(repo, "agent/one")
        assert again is info
        listed = manager.list_worktrees()
        removed = await manager.remove_worktree("agent/one")
        missing = await manager.remove_worktree("agent/one")
        return info, listed, removed, missing

    info, listed, removed, missing = run(scenario(), timeout=120)
    assert Path(info.path).name == "agent+one" and info.branch == "agent-worktree-agent+one"
    assert info.agent_id == "agent-1" and listed == [info] and removed is True and missing is False
    assert not Path(info.path).exists()
    assert manager.get_worktree("agent/one") is None


def test_not_a_repository_error_names_git_output(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    manager = AgentWorktreeManager(tmp_path / "wt")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RuntimeError) as excinfo:
        run(manager.create_worktree(plain, "w"), timeout=60)
    assert "git worktree add failed" in str(excinfo.value) and "not a git repository" in str(
        excinfo.value
    )


def test_recreating_a_slug_discards_the_previous_runs_commits(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def first():
        return await manager.create_worktree(repo, "w1")

    info = run(first(), timeout=60)
    worktree = Path(info.path)
    (worktree / "agent_work.txt").write_text("valuable agent output\n", "utf-8")
    git(worktree, "config", "user.email", "agent@example.com")
    git(worktree, "config", "user.name", "agent")
    git(worktree, "add", ".")
    git(worktree, "commit", "-q", "-m", "agent commit")
    agent_commit = git(worktree, "rev-parse", "HEAD")
    run(manager.remove_worktree("w1"), timeout=60)
    assert git(repo, "rev-parse", info.branch) == agent_commit

    recreated = run(manager.create_worktree(repo, "w1"), timeout=60)
    assert (Path(recreated.path) / "agent_work.txt").read_text("utf-8") == "valuable agent output\n"
    git(repo, "rev-parse", info.branch)
    reachable = (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", agent_commit, info.branch],
            cwd=str(repo),
            capture_output=True,
        ).returncode
        == 0
    )
    assert reachable, (
        "agent commit is no longer reachable from its branch after the slug was reused"
    )


def test_explicit_branch_name_resets_an_existing_unmerged_branch(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("unmerged feature work\n", "utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feature work")
    feature_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    manager = AgentWorktreeManager(tmp_path / "wt")
    run(manager.create_worktree(repo, "w2", branch="feature"), timeout=60)
    after = git(repo, "rev-parse", "feature")
    assert after == feature_commit, "existing branch 'feature' was force-reset by create_worktree"


def test_concurrent_creates_for_the_same_slug_return_one_worktree(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        return await asyncio.gather(
            manager.create_worktree(repo, "same"),
            manager.create_worktree(repo, "same"),
            return_exceptions=True,
        )

    results = run(scenario(), timeout=120)
    assert all(not isinstance(r, Exception) for r in results), results


def test_repository_path_inside_another_repository_is_rejected(repo, tmp_path):
    nested = repo / "src"
    nested.mkdir()
    manager = AgentWorktreeManager(tmp_path / "wt")
    with pytest.raises(ValueError, match="is not the root of a git repository"):
        run(manager.create_worktree(nested, "w"), timeout=60)
    assert manager.list_worktrees() == []
    assert git(repo, "branch", "--list", "agent-worktree-w") == ""


def test_concurrent_creates_for_distinct_slugs(repo, tmp_path):
    manager = AgentWorktreeManager(tmp_path / "wt")

    async def scenario():
        return await asyncio.gather(
            *(manager.create_worktree(repo, f"slug-{i}") for i in range(8)),
            return_exceptions=True,
        )

    results = run(scenario(), timeout=180)
    errors = [str(r) for r in results if isinstance(r, Exception)]
    assert errors == [], errors[:2]


def test_case_variant_slug_message_is_accurate(repo, tmp_path):
    if sys.platform != "win32":
        pytest.skip("case-insensitive filesystem behaviour")
    manager = AgentWorktreeManager(tmp_path / "wt")
    run(manager.create_worktree(repo, "Foo"), timeout=60)
    with pytest.raises(Exception) as excinfo:
        run(manager.create_worktree(repo, "foo"), timeout=60)
    assert "not tracked" not in str(excinfo.value), str(excinfo.value)


def test_trailing_newline_slug_cannot_reach_git(repo, tmp_path):
    AgentWorktreeManager(tmp_path / "wt")
    try:
        validate_worktree_slug("a\n")
        passed = True
    except ValueError:
        passed = False
    assert not passed


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
