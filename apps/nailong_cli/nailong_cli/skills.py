"""Skills: folders with a SKILL.md (or single markdown files) the agent or you can run by name."""

from __future__ import annotations

import dataclasses
import fnmatch
import re
from pathlib import Path

from nailong_agent_sdk.starter import (
    FunctionTool,
    SkillDefinition,
    ToolEvent,
    discover_skills,
    load_skill_definition,
    render_skill,
    skill_file_text,
    tool,
)

from .config import PROJECT_DIRECTORY

BUILTIN_SKILLS = Path(__file__).parent / "builtin_skills"
ALWAYS_ALLOWED = frozenset({"use_skill", "todo_write", "ask_human_question"})
TOOL_NAMES = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
    "notebookedit": "notebook_edit",
    "bash": "run_command",
    "grep": "grep",
    "glob": "glob",
    "webfetch": "web_fetch",
    "websearch": "web_search",
}
_ENTRY = re.compile(r"^([A-Za-z_][\w:-]*)(?:\((.*)\))?$")


def skill_roots(project: Path, home: Path) -> list[tuple[str, Path]]:
    return [
        ("built-in", BUILTIN_SKILLS),
        ("user (~/.codex)", home / ".codex" / "skills"),
        ("user (~/.claude)", home / ".claude" / "skills"),
        ("user", home / ".config" / "nailong" / "skills"),
        ("project (.claude)", project / ".claude" / "skills"),
        ("project", project / PROJECT_DIRECTORY / "skills"),
    ]


def load_skills(
    project: Path, home: Path, extra: dict[str, SkillDefinition] | None = None
) -> list[SkillDefinition]:
    found = {item.name: item for item in discover_skills(skill_roots(project, home))}
    found.update(extra or {})
    return [found[name] for name in sorted(found)]


def read_skill(name: str, path: Path) -> SkillDefinition:
    return dataclasses.replace(load_skill_definition(path, "plugin"), name=name)


def skills_index(entries: list[SkillDefinition]) -> str:
    offered = [entry for entry in entries if not entry.manual_only]
    if not offered:
        return ""
    lines = [f"- {entry.name}: {entry.description}" for entry in offered]
    return (
        "Skills you can load with use_skill when a task matches one (do not load them otherwise):\n"
        + "\n".join(lines)
    )


def find_skill(entries: list[SkillDefinition], name: str) -> SkillDefinition | None:
    return next((entry for entry in entries if entry.name == name), None)


def invocation_prompt(entry: SkillDefinition, arguments: str) -> str:
    return (
        f'Run the skill "{entry.name}" now. Its instructions:\n\n{render_skill(entry, arguments)}'
    )


class SkillScope:
    def __init__(self) -> None:
        self.skill: str | None = None
        self._allowed: list[tuple[str, str | None]] = []

    def activate(self, entry: SkillDefinition) -> None:
        if not entry.allowed_tools:
            return
        self.skill = entry.name
        self._allowed = [self._parse(item, entry) for item in entry.allowed_tools]

    def clear(self) -> None:
        self.skill = None
        self._allowed = []

    @staticmethod
    def _parse(text: str, entry: SkillDefinition) -> tuple[str, str | None]:
        found = _ENTRY.match(text.strip())
        if found is None:
            raise ValueError(
                f'Skill "{entry.name}" lists allowed-tools entry "{text}", which is not a tool '
                "name or Tool(pattern), for example read_file or Bash(git status:*)."
            )
        name = TOOL_NAMES.get(found.group(1).lower(), found.group(1))
        return name, found.group(2)

    def before_tool(self, event: ToolEvent) -> str | None:
        if self.skill is None or event.tool in ALWAYS_ALLOWED:
            return None
        for name, pattern in self._allowed:
            if name != event.tool:
                continue
            if pattern is None or _matches(pattern, event):
                return None
        listed = ", ".join(sorted({name for name, _ in self._allowed}))
        return (
            f'The skill "{self.skill}" allows only {listed}; {event.tool} is outside that list '
            "(allowed-tools in its SKILL.md)."
        )


def _matches(pattern: str, event: ToolEvent) -> bool:
    subject = str(event.arguments.get("command") or event.arguments.get("path") or "")
    prefix = pattern.removesuffix(":*")
    if pattern.endswith(":*"):
        return subject.startswith(prefix)
    return fnmatch.fnmatchcase(subject, pattern)


def skill_tool(
    entries: list[SkillDefinition], scope: SkillScope | None = None
) -> FunctionTool | None:
    if not entries:
        return None
    by_name = {entry.name: entry for entry in entries}

    def use_skill(name: str, arguments: str = "", file: str | None = None) -> str:
        entry = by_name.get(name)
        if entry is None:
            raise ValueError(f'There is no skill "{name}"; the skills are {sorted(by_name)}.')
        if entry.manual_only:
            raise ValueError(
                f'The skill "{name}" is manual-only: the user runs it with /{name}, you do not.'
            )
        if file is not None:
            return skill_file_text(entry, file)
        return render_skill(entry, arguments)

    return tool(
        use_skill,
        description=(
            "Load a skill listed in your instructions, with arguments if it takes any. Pass "
            "file to read one of the files bundled with it instead."
        ),
        read_only=True,
    )


TEMPLATE = """---
name: {name}
description: Say in one sentence what this skill does and when to use it.
---

Describe the steps here. Use $ARGUMENTS where the text after /{name} should go.
"""


def create_skill(project: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        raise ValueError(
            f'Skill name "{name}" must be lowercase letters, digits and single hyphens, at most '
            "64 characters."
        )
    folder = project / PROJECT_DIRECTORY / "skills" / name
    if folder.exists():
        raise ValueError(f"{folder} already exists; edit it or pick another name.")
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    path.write_text(TEMPLATE.format(name=name), encoding="utf-8")
    return path


def install_skill(project: Path, source: Path) -> Path:
    import shutil

    source = source.resolve()
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"{source} is not a skill folder: it has no SKILL.md.")
    entry = load_skill_definition(source / "SKILL.md", "install")
    target = project / PROJECT_DIRECTORY / "skills" / entry.name
    if target.exists():
        raise ValueError(f"{target} already exists; remove it first to install over it.")
    shutil.copytree(source, target)
    return target
