# Copyright (c) 2026 David Michael Indraputra

"""Agent Skills as folders: a SKILL.md with a header, plus scripts and files beside it.

This follows the open Agent Skills layout that Claude Code and Codex also read, so a skill written
for either works here: ``<name>/SKILL.md`` starts with a ``---`` header holding ``name`` and
``description`` (and optionally ``allowed-tools``, ``disable-model-invocation`` and
``argument-hint``). A single ``name.md`` file is still read, with its first line as the
description. The module only finds, parses and renders skills; which ones a caller offers and what
a skill may use stay with the caller.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundations.contracts import SkillContext

MAX_SKILL_CHARS = 20_000
MAX_LISTED_FILES = 50
SKILL_FILE = "SKILL.md"
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 1_024
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_IMPLICIT_OFF = re.compile(r"^\s*allow_implicit_invocation\s*:\s*false\s*$", re.MULTILINE)
_QUOTES = ("'", '"')


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    body: str
    path: Path
    source: str
    directory: Path | None = None
    manual_only: bool = False
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    files: tuple[str, ...] = ()


def parse_frontmatter(text: str, label: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        raise ValueError(f"{label} opens a --- header on line 1 but never closes it with ---.")
    meta: dict[str, Any] = {}
    key: str | None = None
    for number, line in enumerate(lines[1:closing], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key is not None:
            current = meta.get(key)
            meta[key] = (
                [*current, _scalar(stripped[2:])]
                if isinstance(current, list)
                else [_scalar(stripped[2:])]
            )
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip() or name != name.lstrip():
            raise ValueError(f'{label} line {number} must look like "key: value", not {line!r}.')
        key = name.strip()
        meta[key] = _value(value.strip())
    return meta, "\n".join(lines[closing + 1 :]).lstrip("\n")


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _QUOTES:
        return text[1:-1]
    return text


def _value(text: str) -> Any:
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(item) for item in text[1:-1].split(",") if item.strip()]
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return _scalar(text)


def _words(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return tuple(re.findall(r"[^\s,()]+(?:\([^)]*\))?", str(value)))


def load_skill_definition(path: Path, source: str) -> SkillDefinition:
    label = f"Skill file {path}"
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"{label} is empty; a skill needs content for the model to read.")
    if len(text) > MAX_SKILL_CHARS:
        raise ValueError(
            f"{label} has {len(text)} characters, over the {MAX_SKILL_CHARS} limit; shorten it "
            "or move detail into files beside it."
        )
    meta, body = parse_frontmatter(text, label)
    folder = path.parent if path.name == SKILL_FILE else None
    name = str(meta.get("name") or (folder.name if folder else path.stem))
    if folder is not None and name != folder.name:
        raise ValueError(f'{label}: name "{name}" must match its folder "{folder.name}".')
    if not _NAME.match(name) or len(name) > NAME_LIMIT:
        raise ValueError(
            f'{label}: name "{name}" must be lowercase letters, digits and single hyphens, at '
            f"most {NAME_LIMIT} characters."
        )
    description = str(meta.get("description") or "").strip()
    if not description:
        if folder is not None:
            raise ValueError(f'{label} needs a "description:" line in its --- header.')
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        description = first.removeprefix("description:").strip()
    if len(description) > DESCRIPTION_LIMIT:
        raise ValueError(
            f"{label}: description has {len(description)} characters, over {DESCRIPTION_LIMIT}."
        )
    manual = bool(meta.get("disable-model-invocation", False))
    files: tuple[str, ...] = ()
    if folder is not None:
        sidecar = folder / "agents" / "openai.yaml"
        if sidecar.is_file() and _IMPLICIT_OFF.search(sidecar.read_text(encoding="utf-8")):
            manual = True
        listed = sorted(
            item.relative_to(folder).as_posix()
            for item in folder.rglob("*")
            if item.is_file() and item.name != SKILL_FILE
        )
        files = tuple(listed[:MAX_LISTED_FILES])
    hint = meta.get("argument-hint")
    return SkillDefinition(
        name=name,
        description=description,
        body=body,
        path=path,
        source=source,
        directory=folder,
        manual_only=manual,
        allowed_tools=_words(meta.get("allowed-tools")),
        argument_hint=str(hint) if hint else None,
        files=files,
    )


def discover_skills(roots: Sequence[tuple[str, Path]]) -> list[SkillDefinition]:
    found: dict[str, SkillDefinition] = {}
    for source, root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and (entry / SKILL_FILE).is_file():
                definition = load_skill_definition(entry / SKILL_FILE, source)
            elif entry.is_file() and entry.suffix == ".md":
                definition = load_skill_definition(entry, source)
            else:
                continue
            found[definition.name] = definition
    return [found[name] for name in sorted(found)]


def render_skill(definition: SkillDefinition, arguments: str = "") -> str:
    body = definition.body
    given = arguments.strip()
    words = shlex.split(given) if given else []
    used = "$ARGUMENTS" in body
    body = body.replace("$ARGUMENTS", given)
    for position in range(9, 0, -1):
        marker = f"${position}"
        if marker in body:
            used = True
            body = body.replace(marker, words[position - 1] if len(words) >= position else "")
    if given and not used:
        body += f"\n\nARGUMENTS: {given}"
    if definition.directory is not None:
        body += f"\n\nSkill directory: {definition.directory}"
        if definition.files:
            listing = "\n".join(f"- {item}" for item in definition.files)
            body += (
                "\nBundled files (read one with use_skill file=..., run scripts by their full "
                f"path):\n{listing}"
            )
    return body


def skill_file_text(definition: SkillDefinition, relative: str) -> str:
    if definition.directory is None:
        raise ValueError(f'Skill "{definition.name}" is a single file and has no bundled files.')
    root = definition.directory.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(
            f'Skill "{definition.name}" has no bundled file "{relative}"; files: '
            f"{list(definition.files)}."
        )
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_SKILL_CHARS:
        text = text[:MAX_SKILL_CHARS] + "\n[cut]"
    return text


def skill_context_of(definition: SkillDefinition, arguments: str = "") -> SkillContext:
    text = render_skill(definition, arguments)
    return SkillContext(
        id=definition.name,
        version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        content=text,
    )
