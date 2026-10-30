# Copyright (c) 2026 David Michael Indraputra

"""Callbacks a ledger runs after it has persisted a record."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from ..foundations.logging import get_logger

_logger = get_logger("append-listeners")

type AppendListener = Callable[[Any], None]


class AppendListeners:
    def __init__(self, store: str) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._listeners: list[tuple[AppendListener, bool]] = []

    def add(self, listener: AppendListener, *, required: bool = False) -> None:
        with self._lock:
            self._listeners.append((listener, required))

    def remove(self, listener: AppendListener) -> None:
        with self._lock:
            self._listeners = [item for item in self._listeners if item[0] is not listener]

    def notify(self, record: Any) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener, required in listeners:
            try:
                listener(record)
            except Exception as error:
                if required:
                    raise
                _logger.warning(
                    "A %s append listener failed after the record was persisted: %s: %s",
                    self._store,
                    type(error).__name__,
                    error,
                )
