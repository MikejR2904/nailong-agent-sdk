# Copyright (c) 2026 David Michael Indraputra

"""A flag a caller sets to stop a run between steps."""

from __future__ import annotations


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled
