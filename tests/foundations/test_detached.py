import asyncio
import contextvars
import threading
import time

import pytest

from nailong_agent_sdk.foundations.detached import run_detached

REQUEST = contextvars.ContextVar("request", default="none")


def test_the_result_arguments_and_context_of_a_detached_call_arrive_intact():
    def work(first, second, *, scale):
        return (first + second) * scale, REQUEST.get(), threading.current_thread().daemon

    async def scenario():
        REQUEST.set("r-7")
        return await run_detached(work, 2, 3, scale=4)

    assert asyncio.run(scenario()) == (20, "r-7", True)


def test_an_exception_in_a_detached_call_is_raised_to_the_awaiting_task():
    def fail():
        raise LookupError("no such record")

    async def scenario():
        await run_detached(fail)

    with pytest.raises(LookupError, match="no such record"):
        asyncio.run(scenario())


def test_cancelling_the_awaiting_task_returns_at_once_and_the_loop_does_not_wait_for_the_thread():
    finished = threading.Event()
    problems = []
    previous = threading.excepthook
    threading.excepthook = lambda arguments: problems.append(arguments.exc_value)

    def slow():
        time.sleep(1.5)
        finished.set()
        return "late"

    async def scenario():
        task = asyncio.ensure_future(run_detached(slow))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        started = time.monotonic()
        asyncio.run(scenario())
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, elapsed
        assert finished.wait(10)
        time.sleep(0.2)
    finally:
        threading.excepthook = previous
    assert problems == []


def test_a_call_that_fails_after_the_loop_has_closed_is_dropped_silently():
    release = threading.Event()
    problems = []
    previous = threading.excepthook
    threading.excepthook = lambda arguments: problems.append(arguments.exc_value)

    def late_failure():
        release.wait(10)
        raise RuntimeError("too late to matter")

    async def scenario():
        task = asyncio.ensure_future(run_detached(late_failure))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        release.set()
        time.sleep(0.3)
    finally:
        threading.excepthook = previous
    assert problems == []
