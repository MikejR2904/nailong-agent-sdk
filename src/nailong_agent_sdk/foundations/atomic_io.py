# Copyright (c) 2026 David Michael Indraputra

"""Small, dependency-free primitives for atomic local file replacement and locking."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import AgentSdkError

_LOCK_POLL_SECONDS = 0.005


def replace_atomic(temporary: Path, target: Path, *, attempts: int = 5) -> None:
    """Publish a prepared temporary file, retrying transient destination-handle failures.

    Callers are responsible for writing and flushing ``temporary`` before this
    function is called. Retrying only ``PermissionError`` preserves normal failure
    semantics while accommodating brief destination-handle contention on Windows.
    """

    if attempts < 1:
        raise ValueError("attempts must be at least one.")
    for attempt in range(attempts):
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def read_text_retrying(path: Path, *, attempts: int = 10) -> str:
    if attempts < 1:
        raise ValueError("attempts must be at least one.")
    for attempt in range(attempts - 1):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(0.005 * (attempt + 1))
    return path.read_text(encoding="utf-8")


def unique_temporary_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.tmp")


def claim_exclusive(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


@contextmanager
def exclusive_file_lock(
    path: Path, *, timeout_seconds: float = 30.0, timeout_code: str = "FILE_LOCK_TIMEOUT"
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        descriptor = handle.fileno()
        if os.name == "nt":
            _acquire_windows_lock(descriptor, path, timeout_seconds, timeout_code)
            try:
                yield
            finally:
                _release_windows_lock(descriptor)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)


def _acquire_windows_lock(
    descriptor: int, path: Path, timeout_seconds: float, timeout_code: str
) -> None:
    import msvcrt

    deadline = time.monotonic() + timeout_seconds
    while True:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        except PermissionError as error:
            if time.monotonic() >= deadline:
                raise AgentSdkError(
                    timeout_code,
                    f'Could not acquire the lock "{path.name}" within {timeout_seconds:g}s: '
                    "another process is holding it, most likely a writer that hung mid-operation "
                    "or software scanning the lock file.",
                    {"lock_path": str(path), "timeout_seconds": timeout_seconds},
                ) from error
            time.sleep(_LOCK_POLL_SECONDS)


def _release_windows_lock(descriptor: int) -> None:
    import msvcrt

    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
