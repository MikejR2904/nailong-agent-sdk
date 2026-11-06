"""Instruction files: what the agent is told about this project and this user every session."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import MEMORY_FILES, PROJECT_DIRECTORY

MAX_FILE_CHARS = 20_000
MAX_IMPORT_DEPTH = 3
MAX_RULE_FILES = 30
MAX_NESTED_LISTED = 20
NESTED_DEPTH = 3
LOCAL_NAME = "NAILONG.local.md"
SKIPPED = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".nailong", ".tox"}
)
INIT_PROMPT = (
    "Look through this project (its layout, how it is built and tested, its conventions) and "
    "write the contents of an AGENTS.md file for it: short sections on layout, commands to build "
    "and test, code style, and things to avoid. Reply with only the file contents in the "
    "answer. Do not change any file."
)
_IMPORT = re.compile(r"(?<![\w@`])@((?:\.{0,2}/)?[\w.\-/]+\.[\w]+)")


@dataclass(frozen=True)
class MemoryFile:
    label: str
    path: Path
    text: str


def user_memory_path(home: Path) -> Path:
    return home / ".config" / "nailong" / "NAILONG.md"


def repository_root(project: Path) -> Path:
    for candidate in (project, *project.parents):
        if (candidate / ".git").exists():
            return candidate
    return project


def _read(path: Path, roots: list[Path], depth: int, seen: frozenset[Path]) -> str:
    text = path.read_text(encoding="utf-8")
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + "\n...(truncated)"
    if depth >= MAX_IMPORT_DEPTH:
        return text

    def expand(found: re.Match[str]) -> str:
        target = (path.parent / found.group(1)).resolve()
        name = found.group(1)
        if not any(target.is_relative_to(root) for root in roots):
            return f"[not imported: {name} is outside the project and your settings folder]"
        if not target.is_file():
            return f"[not imported: {name} was not found]"
        if target in seen or target == path.resolve():
            return f"[not imported: {name} would include itself]"
        body = _read(target, roots, depth + 1, seen | {path.resolve()})
        return f"\n--- {name} ---\n{body.strip()}\n--- end {name} ---\n"

    return _IMPORT.sub(expand, text)


def load_memory(project: Path, home: Path, listed: tuple[str, ...] = ()) -> list[MemoryFile]:
    project = project.resolve()
    roots = [project, home.resolve() / ".config" / "nailong"]
    top = repository_root(project)
    candidates: list[tuple[str, Path]] = [("user", user_memory_path(home))]
    between = [
        directory for directory in reversed(project.parents) if directory.is_relative_to(top)
    ]
    for directory in between:
        candidates += [("parent", directory / name) for name in MEMORY_FILES]
    candidates += [("project", project / name) for name in MEMORY_FILES]
    candidates.append(("local", project / LOCAL_NAME))
    for pattern in listed:
        candidates += [
            ("listed", path)
            for path in sorted(project.glob(pattern))[:MAX_RULE_FILES]
            if path.resolve().is_relative_to(project)
        ]
    rules = project / PROJECT_DIRECTORY / "rules"
    if rules.is_dir():
        candidates += [("rule", path) for path in sorted(rules.glob("*.md"))[:MAX_RULE_FILES]]
    found: list[MemoryFile] = []
    for label, path in candidates:
        if path.is_file():
            allowed = roots if label != "parent" else [*roots, path.parent.resolve()]
            found.append(MemoryFile(label, path, _read(path, allowed, 0, frozenset())))
    return found


def memory_text(files: list[MemoryFile]) -> str:
    return "\n\n".join(
        f"Notes from {file.path.name} ({file.label}):\n{file.text}" for file in files
    )


def nested_instruction_files(project: Path) -> list[Path]:
    project = project.resolve()
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(project):
        here = Path(directory)
        depth = len(here.relative_to(project).parts)
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in SKIPPED and not name.startswith(".")
        )
        if depth >= NESTED_DEPTH:
            subdirectories[:] = []
        if here == project:
            continue
        for name in MEMORY_FILES:
            if name in names:
                found.append(here / name)
                break
        if len(found) >= MAX_NESTED_LISTED:
            break
    return found


def nested_note(project: Path) -> str:
    files = nested_instruction_files(project)
    if not files:
        return ""
    listing = "\n".join(f"- {path.relative_to(project.resolve()).as_posix()}" for path in files)
    return (
        "These folders have their own instruction files. Read the file with read_file before you "
        f"change anything in that folder:\n{listing}"
    )
