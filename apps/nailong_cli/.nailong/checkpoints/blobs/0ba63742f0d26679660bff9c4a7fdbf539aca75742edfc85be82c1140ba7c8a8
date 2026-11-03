"""@path in a prompt attaches that file (or a directory listing, or a picture) to the message."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from nailong_agent_sdk.starter.images import MEDIA_TYPES
from nailong_agent_sdk.tools.policy import sensitive_pattern_for

MAX_FILE_CHARS = 20_000
MAX_LISTING = 50
_MENTION = re.compile(r"(?<![\w@])@([\w./\\-]+)")


@dataclass
class Expanded:
    text: str
    attached: list[str] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)


def expand_mentions(prompt: str, project: Path) -> Expanded:
    result = Expanded(prompt)
    blocks: list[str] = []
    for match in _MENTION.finditer(prompt):
        name = match.group(1).rstrip(".,;:)")
        if name in result.attached:
            continue
        target = _inside(name, project)
        if target is None:
            continue
        if target.is_file() and target.suffix.lower() in MEDIA_TYPES:
            result.attached.append(name)
            result.images.append(target)
            continue
        block = _block(name, target)
        if block is not None:
            result.attached.append(name)
            blocks.append(block)
    if blocks:
        result.text = prompt + "\n\n" + "\n\n".join(blocks)
    return result


def _inside(name: str, project: Path) -> Path | None:
    target = (project / name).resolve()
    try:
        target.relative_to(project.resolve())
    except ValueError:
        return None
    return None if sensitive_pattern_for(target) is not None else target


def _block(name: str, target: Path) -> str | None:
    if target.is_dir():
        entries = sorted(entry.name + ("/" if entry.is_dir() else "") for entry in target.iterdir())
        shown = entries[:MAX_LISTING]
        more = f"\n... {len(entries) - MAX_LISTING} more" if len(entries) > MAX_LISTING else ""
        return f"Contents of directory {name}:\n" + "\n".join(shown) + more
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"File {name} is not UTF-8 text, so it was not attached."
    clipped = text if len(text) <= MAX_FILE_CHARS else text[:MAX_FILE_CHARS] + "\n...(truncated)"
    return f"Contents of {name}:\n{clipped}"
