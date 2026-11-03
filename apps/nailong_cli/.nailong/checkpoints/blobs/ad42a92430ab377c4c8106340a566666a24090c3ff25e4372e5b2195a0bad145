"""Answers as formatted text: split streamed text into blocks and render each with rich."""

from __future__ import annotations

import io
import shutil

MAX_WIDTH = 100
FENCE = "```"


class BlockSplitter:
    def __init__(self) -> None:
        self._pending = ""

    def feed(self, text: str) -> list[str]:
        self._pending += text
        blocks: list[str] = []
        while True:
            end = self._block_end()
            if end is None:
                return blocks
            block, self._pending = self._pending[:end], self._pending[end:]
            if block.strip():
                blocks.append(block.strip("\n"))

    def flush(self) -> str | None:
        rest, self._pending = self._pending.strip("\n"), ""
        return rest or None

    def _block_end(self) -> int | None:
        position = 0
        in_fence = False
        while True:
            newline = self._pending.find("\n", position)
            if newline == -1:
                return None
            line = self._pending[position:newline]
            if line.lstrip().startswith(FENCE):
                in_fence = not in_fence
                if not in_fence:
                    return newline + 1
            elif not in_fence and line.strip() == "" and position > 0:
                return newline + 1
            position = newline + 1


def render_markdown(source: str, *, width: int | None = None) -> str:
    from rich.console import Console as RichConsole
    from rich.markdown import Markdown

    columns = width or min(shutil.get_terminal_size((80, 20)).columns, MAX_WIDTH)
    buffer = io.StringIO()
    RichConsole(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=columns,
        legacy_windows=False,
    ).print(Markdown(source, code_theme="ansi_dark"))
    return buffer.getvalue().rstrip("\n") + "\n"
