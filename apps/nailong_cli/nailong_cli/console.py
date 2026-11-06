"""Reading and writing the terminal, with colour only when it helps."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO

_CODES = {"dim": "2", "bold": "1", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
_ERASE_LINE = "\r\x1b[K"
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_INTERVAL_SECONDS = 0.1

Chooser = Callable[[str, list[str]], "int | None"]


class Console:
    def __init__(
        self,
        stdin: TextIO,
        stdout: TextIO,
        stderr: TextIO,
        *,
        color: bool | None = None,
        interactive: bool | None = None,
    ) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.interactive = (
            interactive if interactive is not None else stdin.isatty() and stdout.isatty()
        )
        self._color = self.interactive if color is None else color
        self.fancy = False
        self.chooser: Chooser | None = None
        self.remote_approval: Callable[[dict[str, str]], bool] | None = None
        self.remote_cancel: threading.Event | None = None
        self.reading = 0
        self.typeahead = False
        self._typed = ""
        self.key_hooks: tuple[Callable[[], None], Callable[[], None]] | None = None
        self._lock = threading.RLock()
        self._label: str | None = None
        self._started = 0.0
        self._drawn = False
        self._mid_line = False
        self._frame = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def style(self, text: str, name: str) -> str:
        return f"\x1b[{_CODES[name]}m{text}\x1b[0m" if self._color else text

    def box(self, lines: list[str]) -> None:
        width = max(len(line) for line in lines) + 2
        self.line(self.style("╭" + "─" * width + "╮", "dim"))
        for line in lines:
            edge = self.style("│", "dim")
            self.line(edge + " " + line.ljust(width - 1) + edge)
        self.line(self.style("╰" + "─" * width + "╯", "dim"))

    def start_spinner(self, label: str) -> None:
        if not self.fancy:
            return
        with self._lock:
            self._label = label
            self._started = time.monotonic()
            self._frame = 0
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._spin, daemon=True)
                self._thread.start()
            self._draw()

    def set_spinner_label(self, label: str) -> None:
        with self._lock:
            if self._label is not None:
                self._label = label
                self._draw()

    def set_typed(self, text: str) -> None:
        with self._lock:
            self._typed = text
            self._draw()

    def stop_spinner(self) -> None:
        with self._lock:
            self._label = None
            self._erase()
        thread, self._thread = self._thread, None
        if thread is not None:
            self._stop.set()
            thread.join(timeout=1.0)

    def _spin(self) -> None:
        while not self._stop.wait(SPINNER_INTERVAL_SECONDS):
            with self._lock:
                self._frame += 1
                self._draw()

    def _draw(self) -> None:
        if self._label is None or self._mid_line or self.reading:
            return
        seconds = int(time.monotonic() - self._started)
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        hint = "Esc to interrupt · type to queue" if self.typeahead else "Esc to interrupt"
        text = f"{frame} {self._label} ({seconds}s · {hint})"
        if self._typed:
            text += "  ▸ " + self._typed[-60:]
        self.stdout.write(_ERASE_LINE + self.style(text, "dim"))
        self.stdout.flush()
        self._drawn = True

    def _erase(self) -> None:
        if self._drawn:
            self.stdout.write(_ERASE_LINE)
            self.stdout.flush()
            self._drawn = False

    @contextmanager
    def reading_input(self) -> Iterator[None]:
        with self._lock:
            self._erase()
            self.reading += 1
        hooks = self.key_hooks
        if hooks is not None:
            hooks[0]()
        try:
            yield
        finally:
            if hooks is not None:
                hooks[1]()
            with self._lock:
                self.reading -= 1

    def write(self, text: str) -> None:
        with self._lock:
            self._erase()
            self.stdout.write(text)
            self.stdout.flush()
            if text:
                self._mid_line = not text.endswith("\n")

    def line(self, text: str = "") -> None:
        self.write(text + "\n")

    def error(self, text: str) -> None:
        with self._lock:
            self._erase()
            self.stderr.write(self.style(text, "red") + "\n")
            self.stderr.flush()

    def ask(self, prompt: str) -> str:
        with self.reading_input():
            self.write(prompt)
            answer = self.stdin.readline()
            self._mid_line = False
        if answer == "":
            raise EOFError("Input ended while waiting for an answer.")
        return answer.strip()
