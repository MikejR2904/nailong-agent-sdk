# Copyright (c) 2026 David Michael Indraputra

"""A bounded background queue for summarization work that never blocks its caller.

Jobs run on daemon threads, so a summarizer that never returns cannot hold the interpreter
open. The queue and the number of workers are bounded; a job that runs past its timeout is
logically abandoned (its result is discarded) and counted, and once too many abandoned jobs
are still running the scheduler stops accepting work until they finish.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobResult:
    key: str
    value: Any = None
    error: BaseException | None = None
    timed_out: bool = False
    submitted_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class _Job:
    key: str
    work: Callable[[], Any]
    submitted_at: float
    started_at: float | None = None
    abandoned: bool = False


class SummarizationScheduler:
    def __init__(
        self,
        *,
        max_workers: int = 1,
        max_queued: int = 8,
        job_timeout_seconds: float | None = 60.0,
        max_abandoned: int = 4,
    ) -> None:
        if max_workers < 1 or max_queued < 1 or max_abandoned < 0:
            raise ValueError(
                "max_workers and max_queued must be at least 1 and max_abandoned at least 0."
            )
        if job_timeout_seconds is not None and job_timeout_seconds <= 0:
            raise ValueError("job_timeout_seconds must be positive when set.")
        self._max_workers = max_workers
        self._max_queued = max_queued
        self._timeout = job_timeout_seconds
        self._max_abandoned = max_abandoned
        self._lock = threading.Lock()
        self._wakeup = threading.Condition(self._lock)
        self._queue: list[_Job] = []
        self._running: dict[str, _Job] = {}
        self._finished: list[JobResult] = []
        self._workers: list[threading.Thread] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def submit(self, key: str, work: Callable[[], Any]) -> str:
        """Queue a job; returns ``accepted`` or why it was not (``duplicate``, ``full``,
        ``unavailable``, ``closed``)."""

        with self._lock:
            if self._closed:
                return "closed"
            if key in self._running or any(job.key == key for job in self._queue):
                return "duplicate"
            if self._abandoned_running() >= self._max_abandoned > 0:
                return "unavailable"
            if len(self._queue) >= self._max_queued:
                return "full"
            self._queue.append(_Job(key=key, work=work, submitted_at=time.monotonic()))
            self._ensure_workers()
            self._wakeup.notify()
            return "accepted"

    def drain(self) -> list[JobResult]:
        now = time.monotonic()
        with self._lock:
            if self._timeout is not None:
                for job in self._running.values():
                    if (
                        not job.abandoned
                        and job.started_at is not None
                        and now - job.started_at > self._timeout
                    ):
                        job.abandoned = True
                        self._finished.append(
                            JobResult(
                                key=job.key,
                                timed_out=True,
                                submitted_at=job.submitted_at,
                                finished_at=now,
                            )
                        )
            finished, self._finished = self._finished, []
        return finished

    def pending_keys(self) -> set[str]:
        with self._lock:
            return {job.key for job in self._queue} | {
                key for key, job in self._running.items() if not job.abandoned
            }

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": len(self._queue),
                "running": sum(1 for job in self._running.values() if not job.abandoned),
                "abandoned": self._abandoned_running(),
            }

    def idle(self) -> bool:
        with self._lock:
            return not self._queue and not any(not job.abandoned for job in self._running.values())

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.idle():
                return True
            time.sleep(0.005)
        return self.idle()

    def cancel_queued(self) -> int:
        with self._lock:
            dropped = len(self._queue)
            self._queue.clear()
            return dropped

    def close(self) -> int:
        with self._lock:
            self._closed = True
            dropped = len(self._queue)
            self._queue.clear()
            self._wakeup.notify_all()
            return dropped

    def _abandoned_running(self) -> int:
        return sum(1 for job in self._running.values() if job.abandoned)

    def _ensure_workers(self) -> None:
        self._workers = [worker for worker in self._workers if worker.is_alive()]
        while len(self._workers) < self._max_workers:
            worker = threading.Thread(target=self._work, name="semantic-summarizer", daemon=True)
            self._workers.append(worker)
            worker.start()

    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._closed:
                    if not self._wakeup.wait(timeout=5.0) and not self._queue:
                        current = threading.current_thread()
                        self._workers = [w for w in self._workers if w is not current]
                        return
                if self._closed and not self._queue:
                    return
                job = self._queue.pop(0)
                job.started_at = time.monotonic()
                self._running[job.key] = job
            value: Any = None
            error: BaseException | None = None
            try:
                value = job.work()
            except BaseException as caught:
                error = caught
            with self._lock:
                self._running.pop(job.key, None)
                if not job.abandoned:
                    self._finished.append(
                        JobResult(
                            key=job.key,
                            value=value,
                            error=error,
                            submitted_at=job.submitted_at,
                            finished_at=time.monotonic(),
                        )
                    )
