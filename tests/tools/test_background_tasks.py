import asyncio
import sys
import textwrap
import warnings

import pytest

from nailong_agent_sdk.tools.sandbox import NativeSandbox
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from nailong_agent_sdk.tools.task_models import TaskKind, TaskStatus
from nailong_agent_sdk.tools.tasks import BackgroundTaskManager
from tests.support.tools import run

PY = sys.executable


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
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        pending.send(None)
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
