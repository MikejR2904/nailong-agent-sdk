# Copyright (c) 2026 David Michael Indraputra

"""Small, dependency-free primitives for atomic local file replacement."""

from __future__ import annotations

import os
import time
from pathlib import Path


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
