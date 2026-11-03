"""Skills: markdown files the agent loads when a task calls for one, not on every turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nailong_agent_sdk.starter import FunctionTool, tool

from .config import PROJECT_DIRECTORY

MAX_SKILL_CHARS = 20_000
_DESCRIPTION_CHARS = 120


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    content: str
    path: Path


def load_skills(
    project: Path, home: Path, extra: dict[str, SkillEntry] | None = None
) -> list[SkillEntry]:
    found: dict[str, SkillEntry] = {}
    for directory in (
        home / ".config" / "nailong" / "skills",
        project / PROJECT_DIRECTORY / "skills",
    ):
        for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            found[path.stem] = _read(path.stem, path)
    found.update(extra or {})
    return [found[name] for name in sorted(found)]


def read_skill(name: str, path: Path) -> SkillEntry:
    return _read(name, path)


def skills_index(entries: list[SkillEntry]) -> str:
    if not entries:
        return ""
    lines = [f"- {entry.name}: {entry.description}" for entry in entries]
    return (
        "Skills you can load with use_skill when a task matches one (do not load them otherwise):\n"
        + "\n".join(lines)
    )


def skill_tool(entries: list[SkillEntry]) -> FunctionTool | None:
    if not entries:
        return None
    by_name = {entry.name: entry for entry in entries}

    def use_skill(name: str) -> str:
        entry = by_name.get(name)
        if entry is None:
            raise ValueError(f'There is no skill "{name}"; the skills are {sorted(by_name)}.')
        return entry.content

    return tool(
        use_skill,
        description="Load the full text of one skill listed in your instructions.",
        read_only=True,
    )


def _read(name: str, path: Path) -> SkillEntry:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(
            f"Skill file {path} is empty; a skill needs content for the model to read."
        )
    if len(text) > MAX_SKILL_CHARS:
        raise ValueError(
            f"Skill file {path} has {len(text)} characters, over the {MAX_SKILL_CHARS} limit; "
            "shorten it or split it."
        )
    first = next(line.strip() for line in text.splitlines() if line.strip())
    description = first.removeprefix("description:").strip()[:_DESCRIPTION_CHARS]
    return SkillEntry(name, description or name, text, path)
