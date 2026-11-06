"""Your own slash commands: markdown files that become prompts, with arguments and options."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from nailong_agent_sdk.starter.skill_files import parse_frontmatter

from .config import PROJECT_DIRECTORY

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
_INJECTION = re.compile(r"!`([^`\n]+)`")


@dataclass(frozen=True)
class CommandOptions:
    description: str = ""
    agent: str | None = None
    model: str | None = None
    subtask: bool = False


@dataclass(frozen=True)
class UserCommand:
    name: str
    text: str
    path: Path

    def _parts(self) -> tuple[dict, str]:
        return parse_frontmatter(self.text, f"Command file {self.path}")

    def options(self) -> CommandOptions:
        meta, _ = self._parts()
        agent = str(meta["agent"]).replace("-", "_") if meta.get("agent") else None
        model = str(meta["model"]) if meta.get("model") else None
        subtask = bool(meta.get("subtask", False))
        if subtask and agent is None:
            raise ValueError(
                f"Command file {self.path} sets subtask: true but names no agent: to run in a "
                "subagent, add an agent: line."
            )
        return CommandOptions(str(meta.get("description") or ""), agent, model, subtask)

    def render(self, arguments: str) -> str:
        _, body = self._parts()
        given = arguments.strip()
        try:
            words = shlex.split(given) if given else []
        except ValueError:
            words = given.split()
        body = body.replace("$ARGUMENTS", given)
        for position in range(9, 0, -1):
            marker = f"${position}"
            if marker in body:
                body = body.replace(marker, words[position - 1] if len(words) >= position else "")
        return body.strip()


def injected_commands(text: str) -> list[str]:
    return _INJECTION.findall(text)


def fill_injections(text: str, outputs: dict[str, str]) -> str:
    return _INJECTION.sub(lambda found: outputs[found.group(1)].rstrip(), text)


def _directories(project: Path, home: Path, kind: str) -> list[Path]:
    return [home / ".config" / "nailong" / kind, project / PROJECT_DIRECTORY / kind]


def load_commands(project: Path, home: Path) -> dict[str, UserCommand]:
    found: dict[str, UserCommand] = {}
    for directory in _directories(project, home, "commands"):
        for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            if _NAME.fullmatch(path.stem):
                found[path.stem] = UserCommand(path.stem, path.read_text(encoding="utf-8"), path)
    return found
