"""Your own slash commands and skills: markdown files the agent can be handed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_DIRECTORY

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")


@dataclass(frozen=True)
class UserCommand:
    name: str
    text: str
    path: Path

    def render(self, arguments: str) -> str:
        return self.text.replace("$ARGUMENTS", arguments).strip()


def _directories(project: Path, home: Path, kind: str) -> list[Path]:
    return [home / ".config" / "nailong" / kind, project / PROJECT_DIRECTORY / kind]


def load_commands(project: Path, home: Path) -> dict[str, UserCommand]:
    found: dict[str, UserCommand] = {}
    for directory in _directories(project, home, "commands"):
        for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            if _NAME.fullmatch(path.stem):
                found[path.stem] = UserCommand(path.stem, path.read_text(encoding="utf-8"), path)
    return found
