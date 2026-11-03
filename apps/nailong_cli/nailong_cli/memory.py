"""Instruction files: what the agent is told about this project and this user every session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import MEMORY_FILES

MAX_FILE_CHARS = 20_000
INIT_PROMPT = (
    "Look through this project (its layout, how it is built and tested, its conventions) and "
    "write the contents of an AGENTS.md file for it: short sections on layout, commands to build "
    "and test, code style, and things to avoid. Reply with only the file contents in the "
    "answer. Do not change any file."
)


@dataclass(frozen=True)
class MemoryFile:
    label: str
    path: Path
    text: str


def user_memory_path(home: Path) -> Path:
    return home / ".config" / "nailong" / "NAILONG.md"


def load_memory(project: Path, home: Path) -> list[MemoryFile]:
    candidates = [("user", user_memory_path(home))]
    candidates += [("project", project / name) for name in MEMORY_FILES]
    found: list[MemoryFile] = []
    for label, path in candidates:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if len(text) > MAX_FILE_CHARS:
                text = text[:MAX_FILE_CHARS] + "\n...(truncated)"
            found.append(MemoryFile(label, path, text))
    return found


def memory_text(files: list[MemoryFile]) -> str:
    return "\n\n".join(
        f"Notes from {file.path.name} ({file.label}):\n{file.text}" for file in files
    )
