# Copyright (c) 2026 David Michael Indraputra

"""Run a blocking function in a detached daemon thread and await its result.
- Executes `function(*args, **kwargs)` on a new daemon thread under a copy of the caller's context.
- Returns or raises the outcome via an asyncio Future scheduled back on the event loop.
- If the awaiting task is cancelled, the thread keeps running but its result is dropped.
- A closed loop is ignored safely, so abandoned calls never block shutdown."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable
from typing import Any


async def run_detached[T](function: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()
    context = contextvars.copy_context()

    def deliver(complete: Callable[[Any], None], value: Any) -> None:
        if not future.done():
            complete(value)

    def work() -> None:
        try:
            value = context.run(function, *args, **kwargs)
        except BaseException as error:
            outcome = (future.set_exception, error)
        else:
            outcome = (future.set_result, value)
        try:
            loop.call_soon_threadsafe(deliver, *outcome)
        except RuntimeError:
            return

    threading.Thread(
        target=work, name=f"detached-{getattr(function, '__name__', 'call')}", daemon=True
    ).start()
    return await future
