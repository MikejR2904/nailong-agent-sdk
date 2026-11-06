"""Task notes the agent keeps in files, so a long job survives compaction and a restart."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool

MAX_NOTE_CHARS = 20_000
MAX_NOTES = 50
_TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


class NotesStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def tools(self) -> list[FunctionTool]:
        def notes_read(title: str | None = None) -> dict[str, Any]:
            """Read a task note by title, or list the notes when no title is given."""
            return self.read(title)

        def notes_write(title: str, text: str, append: bool = False) -> dict[str, Any]:
            """Save progress notes (goal, facts learned, what is done, what is next) under a title.

            Notes live in a file, so they survive compaction and a restart; reread them when a long
            task resumes.
            """
            return self.write(title, text, append)

        return [tool(notes_read, read_only=True), tool(notes_write)]

    def titles(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(path.stem for path in self._directory.glob("*.md"))

    def read(self, title: str | None) -> dict[str, Any]:
        if title is None:
            return {"notes": self.titles()}
        path = self._path(title)
        if not path.is_file():
            raise ValueError(f'There is no note "{title}"; notes: {self.titles()}.')
        return {"title": title, "text": path.read_text(encoding="utf-8")}

    def write(self, title: str, text: str, append: bool) -> dict[str, Any]:
        path = self._path(title)
        existing = path.read_text(encoding="utf-8") if append and path.is_file() else ""
        combined = (existing.rstrip() + "\n" + text).strip() if existing else text.strip()
        if not combined:
            raise ValueError("A note cannot be empty.")
        if len(combined) > MAX_NOTE_CHARS:
            raise ValueError(
                f'Note "{title}" would be {len(combined)} characters; the limit is '
                f"{MAX_NOTE_CHARS}. Summarise it first."
            )
        if not path.is_file() and len(self.titles()) >= MAX_NOTES:
            raise ValueError(f"{MAX_NOTES} notes exist already; reuse one of {self.titles()}.")
        self._directory.mkdir(parents=True, exist_ok=True)
        path.write_text(combined + "\n", encoding="utf-8")
        return {"title": title, "characters": len(combined)}

    def _path(self, title: str) -> Path:
        if not _TITLE.match(title):
            raise ValueError(
                f'Note title "{title}" must be 1 to 64 letters, digits, spaces, "-", "_" or ".".'
            )
        return self._directory / f"{title}.md"
