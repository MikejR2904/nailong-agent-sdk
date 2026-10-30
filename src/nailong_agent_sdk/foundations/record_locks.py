# Copyright (c) 2026 David Michael Indraputra

"""
Cross-process record locks with per-thread re-entrance.
- Each record ID maps to a lock file in the store directory.
- Locks are advisory and exclusive across processes.
- A thread can re-enter the same lock multiple times without blocking itself.
- Used to serialize updates so records are not corrupted by concurrent writers.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .atomic_io import exclusive_file_lock
from .identifiers import file_safe_name

_HELD = threading.local()


class RecordLocks:
    def __init__(
        self, directory: Path, *, timeout_code: str, timeout_seconds: float = 30.0
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._directory = directory
        self._timeout_code = timeout_code
        self._timeout_seconds = timeout_seconds

    @contextmanager
    def hold(self, record_id: str) -> Iterator[None]:
        path = self._directory / f"{file_safe_name(record_id)}.lock"
        key = os.path.normcase(os.path.abspath(path))
        depths = _depths()
        if key in depths:
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        with exclusive_file_lock(
            path, timeout_seconds=self._timeout_seconds, timeout_code=self._timeout_code
        ):
            depths[key] = 1
            try:
                yield
            finally:
                del depths[key]


def _depths() -> dict[str, int]:
    depths = getattr(_HELD, "depths", None)
    if depths is None:
        depths = {}
        _HELD.depths = depths
    return depths
