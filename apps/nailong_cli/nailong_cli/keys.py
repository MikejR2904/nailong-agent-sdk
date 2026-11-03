"""Watching the keyboard during a turn so Esc can stop it, leaving Ctrl+C to the terminal."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Protocol

from .console import Console

POLL_SECONDS = 0.05
ESCAPE = "esc"
OTHER = "other"


class KeySource(Protocol):
    def open(self) -> None: ...

    def poll(self, timeout: float) -> str | None: ...

    def close(self) -> None: ...


class WindowsKeys:
    def open(self) -> None:
        return None

    def poll(self, timeout: float) -> str | None:
        import msvcrt
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                character = msvcrt.getwch()
                if character in ("\x00", "\xe0"):
                    msvcrt.getwch()
                    return OTHER
                return ESCAPE if character == "\x1b" else OTHER
            time.sleep(0.01)
        return None

    def close(self) -> None:
        return None


class PosixKeys:
    def __init__(self) -> None:
        self._saved: list | None = None
        self._descriptor = sys.stdin.fileno()

    def open(self) -> None:
        import termios
        import tty

        self._saved = termios.tcgetattr(self._descriptor)
        tty.setcbreak(self._descriptor)

    def poll(self, timeout: float) -> str | None:
        import os
        import select

        ready, _, _ = select.select([self._descriptor], [], [], timeout)
        if not ready:
            return None
        first = os.read(self._descriptor, 1)
        if first != b"\x1b":
            return OTHER
        more, _, _ = select.select([self._descriptor], [], [], 0.03)
        if more:
            os.read(self._descriptor, 8)
            return OTHER
        return ESCAPE

    def close(self) -> None:
        import termios

        if self._saved is not None:
            termios.tcsetattr(self._descriptor, termios.TCSADRAIN, self._saved)
            self._saved = None


def system_keys() -> KeySource | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if sys.platform == "win32":
        return WindowsKeys()
    try:
        import termios  # noqa: F401
    except ImportError:
        return None
    return PosixKeys()


class EscapeWatcher:
    def __init__(
        self, console: Console, on_escape: Callable[[], None], source: KeySource | None
    ) -> None:
        self._console = console
        self._on_escape = on_escape
        self._source = source
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> EscapeWatcher:
        if self._source is None:
            return self
        self._source.open()
        self._console.key_hooks = (self._source.close, self._source.open)
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._source is not None:
            self._console.key_hooks = None
            self._source.close()

    def _watch(self) -> None:
        assert self._source is not None
        while not self._stop.is_set():
            if self._console.reading:
                self._stop.wait(POLL_SECONDS)
                continue
            if self._source.poll(POLL_SECONDS) == ESCAPE and not self._console.reading:
                self._on_escape()
                return
