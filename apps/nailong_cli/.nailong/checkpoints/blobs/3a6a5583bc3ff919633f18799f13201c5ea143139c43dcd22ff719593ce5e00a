"""Approval rules the user has saved, kept outside every project so an agent cannot edit them."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from .commandline import allowed_by


def permissions_path(home: Path) -> Path:
    return home / ".config" / "nailong" / "permissions.toml"


class PermissionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def allows(self, project: Path, tool: str, subject: str) -> bool:
        patterns = [rule["match"] for rule in self._rules(project) if rule["tool"] == tool]
        return allowed_by(patterns, tool, subject)

    def add(self, project: Path, tool: str, pattern: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_block(_key(project), tool, pattern))

    def remove(self, project: Path, index: int) -> tuple[str, str]:
        everything = self._all()
        mine = [i for i, rule in enumerate(everything) if rule.get("project") == _key(project)]
        if not 1 <= index <= len(mine):
            raise ValueError(f"There is no rule number {index}; this project has {len(mine)}.")
        removed = everything.pop(mine[index - 1])
        text = "".join(_block(r["project"], r["tool"], r["match"]) for r in everything)
        self._path.write_text(text, encoding="utf-8", newline="\n")
        return removed["tool"], removed["match"]

    def rules(self, project: Path) -> list[tuple[str, str]]:
        return [(rule["tool"], rule["match"]) for rule in self._rules(project)]

    def _all(self) -> list[dict[str, str]]:
        if not self._path.is_file():
            return []
        return list(tomllib.loads(self._path.read_text(encoding="utf-8")).get("rule", []))

    def _rules(self, project: Path) -> list[dict[str, str]]:
        return [rule for rule in self._all() if rule.get("project") == _key(project)]


def _block(project: str, tool: str, pattern: str) -> str:
    return (
        "[[rule]]\n"
        f"project = {json.dumps(project)}\n"
        f"tool = {json.dumps(tool)}\n"
        f"match = {json.dumps(pattern)}\n\n"
    )


def _key(project: Path) -> str:
    return project.resolve().as_posix()
