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
ENTER = "enter"
BACKSPACE = "backspace"
MAX_TYPED = 4_000


def classify(character: str) -> str:
    if character == "\x1b":
        return ESCAPE
    if character in ("\r", "\n"):
        return ENTER
    if character in ("\x08", "\x7f"):
        return BACKSPACE
    return character if character.isprintable() else OTHER


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
                return classify(character)
            time.sleep(0.01)
        return None

    def close(self) -> None:
        return None


class PosixKeys:
    def __init__(self) -> None:
        self._saved: list | None = None
        self._descriptor = sys.stdin.fileno()

    def open(self) -> None:
        if sys.platform == "win32":
            return
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
            lead = first[0]
            extra = 0 if lead < 0xC0 else 1 if lead < 0xE0 else 2 if lead < 0xF0 else 3
            if extra:
                first += os.read(self._descriptor, extra)
            try:
                return classify(first.decode("utf-8"))
            except UnicodeDecodeError:
                return OTHER
        more, _, _ = select.select([self._descriptor], [], [], 0.03)
        if more:
            os.read(self._descriptor, 8)
            return OTHER
        return ESCAPE

    def close(self) -> None:
        if sys.platform == "win32":
            return
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
        self,
        console: Console,
        on_escape: Callable[[], None],
        source: KeySource | None,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self._console = console
        self._on_escape = on_escape
        self._source = source
        self._on_line = on_line
        self._buffer = ""
        self.leftover = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> EscapeWatcher:
        if self._source is None:
            return self
        self._source.open()
        self._console.typeahead = self._on_line is not None
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
            self._console.typeahead = False
            self._console.set_typed("")
            self._source.close()
        self.leftover = self._buffer

    def _watch(self) -> None:
        assert self._source is not None
        while not self._stop.is_set():
            if self._console.reading:
                self._stop.wait(POLL_SECONDS)
                continue
            token = self._source.poll(POLL_SECONDS)
            if token is None or self._console.reading:
                continue
            if token == ESCAPE:
                self._on_escape()
                return
            if self._on_line is not None:
                self._type(token)

    def _type(self, token: str) -> None:
        assert self._on_line is not None
        if token == ENTER:
            line, self._buffer = self._buffer.strip(), ""
            self._console.set_typed("")
            if line:
                self._on_line(line)
            return
        if token == BACKSPACE:
            self._buffer = self._buffer[:-1]
        elif len(token) == 1 and len(self._buffer) < MAX_TYPED:
            self._buffer += token
        else:
            return
        self._console.set_typed(self._buffer)
