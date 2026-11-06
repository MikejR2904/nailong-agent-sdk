"""Small file-storage helpers shared by the wiki and the discoveries store."""

from __future__ import annotations

import contextlib
import os
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(
            0x1000, False, pid
        )  # QUERY_LIMITED_INFORMATION
        if not process:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_durably(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@contextlib.contextmanager
def exclusive(lock: Path, *, wait: float, stale: float, what: str) -> Iterator[None]:
    """One writer at a time across processes; a lock a dead writer left is taken over."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            break
        except FileExistsError:
            with contextlib.suppress(OSError, ValueError):
                holder = int(lock.read_text(encoding="utf-8").strip() or 0)
                if holder and holder != os.getpid() and not pid_alive(holder):
                    lock.unlink()
                    continue
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink()
                    continue
            if time.monotonic() > deadline:
                raise ValueError(
                    f"Another writer holds the {what} lock ({lock}); it did not finish within "
                    f"{wait:g}s. Try again."
                ) from None
            time.sleep(0.02)
    try:
        yield
    finally:
        _release(lock)


def _release(lock: Path) -> None:
    """Remove the lock; on Windows a waiter reading it can make the first try fail."""
    for _ in range(100):
        try:
            lock.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.02)
    lock.unlink(missing_ok=True)
