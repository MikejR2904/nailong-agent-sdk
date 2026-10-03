# Copyright (c) 2026 David Michael Indraputra

"""Cross-process exclusive locks for the SDK's file-backed stores.

Each durable store (run state, project state, controllers, approvals) performs a
read-check-write sequence. Change detection alone narrows the window in which
two writers can interleave; a lock closes it. ``FileLock`` holds an OS-level
advisory lock (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows) on a
sibling ``.lock`` file plus a per-path in-process lock, so it serializes threads
in one process and separate processes alike. It is re-entrant per thread.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import AgentSdkError

_registry_guard = threading.Lock()
_process_locks: dict[str, threading.RLock] = {}
_holders = threading.local()


def _process_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _registry_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = _process_locks[key] = threading.RLock()
        return lock


def _depths() -> dict[str, int]:
    depths = getattr(_holders, "depths", None)
    if depths is None:
        depths = _holders.depths = {}
    return depths


if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _acquire_os(fd: int, blocking: bool) -> bool:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, mode, 1)
            return True
        except OSError:
            return False

    def _release_os(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire_os(fd: int, blocking: bool) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _release_os(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """Exclusive lock guarding the resource at ``path`` (the lock file is ``path.lock``)."""

    def __init__(self, path: Path, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("FileLock timeout_seconds must be positive.")
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def hold(self) -> Iterator[None]:
        key = str(self.lock_path.resolve())
        depths = _depths()
        if depths.get(key):
            depths[key] += 1  # re-entrant: this thread already holds it
            try:
                yield
            finally:
                depths[key] -= 1
            return
        thread_lock = _process_lock(Path(key))
        if not thread_lock.acquire(timeout=self.timeout_seconds):
            raise self._timeout()
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                deadline = time.monotonic() + self.timeout_seconds
                while not _acquire_os(fd, blocking=False):
                    if time.monotonic() >= deadline:
                        raise self._timeout()
                    time.sleep(0.01)
                depths[key] = 1
                try:
                    yield
                finally:
                    depths.pop(key, None)
                    _release_os(fd)
            finally:
                os.close(fd)
        finally:
            thread_lock.release()

    def _timeout(self) -> AgentSdkError:
        return AgentSdkError(
            "STORE_LOCK_TIMEOUT",
            f"Could not acquire {self.lock_path} within {self.timeout_seconds:g}s; another "
            "writer is holding it. If no SDK process is running, the lock is not stale "
            "(OS locks release on exit), so check for a hung process.",
            {"lock_path": str(self.lock_path), "timeout_seconds": self.timeout_seconds},
        )
