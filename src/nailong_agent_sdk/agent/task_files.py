# Copyright (c) 2026 David Michael Indraputra

"""Bounded, paged reads of the files an agent task left in its workspace."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.paths import relative_to_base
from ..tools.policy import sensitive_pattern_for

DEFAULT_LISTING_LIMIT = 200
MAX_LISTING_LIMIT = 1_000
MAX_DIRECTORY_ENTRIES = 50_000
DEFAULT_READ_BYTES = 65_536
MAX_READ_BYTES = 1_048_576
INTERNAL_STATE_PREFIX = ".agent-"
_PARTIAL_CHARACTER_BYTES = 3


class TaskFileEntry(StrictModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int | None = Field(default=None, ge=0)
    modified_at_utc: str


class TaskFileListing(StrictModel):
    path: str
    entries: list[TaskFileEntry]
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = None


class TaskFileChunk(StrictModel):
    path: str
    size: int = Field(ge=0)
    offset: int = Field(ge=0)
    bytes_returned: int = Field(ge=0)
    next_offset: int | None = None
    encoding: Literal["utf-8", "base64"]
    content: str


def list_task_files(
    workspace: Path,
    path: str = ".",
    *,
    offset: int = 0,
    limit: int = DEFAULT_LISTING_LIMIT,
) -> TaskFileListing:
    if not 1 <= limit <= MAX_LISTING_LIMIT:
        raise ValueError(f"limit must be from 1 to {MAX_LISTING_LIMIT}, got {limit}.")
    if offset < 0:
        raise ValueError(f"offset must be at least 0, got {offset}.")
    base = workspace.resolve()
    directory = _target(base, path)
    if not directory.exists():
        raise ValueError(f'Task file path "{path}" does not exist in the workspace.')
    if not directory.is_dir():
        raise ValueError(f'Task file path "{path}" is a file, not a directory.')
    entries: list[TaskFileEntry] = []
    with os.scandir(directory) as scanned:
        for index, item in enumerate(scanned):
            if index >= MAX_DIRECTORY_ENTRIES:
                raise ValueError(
                    f'Task file path "{path}" holds more than {MAX_DIRECTORY_ENTRIES} entries; '
                    "list a narrower directory."
                )
            entry = _entry(base, Path(item.path))
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda value: value.name)
    page = entries[offset : offset + limit]
    following = offset + len(page)
    return TaskFileListing(
        path=_display(base, directory),
        entries=page,
        offset=offset,
        total=len(entries),
        next_offset=following if following < len(entries) else None,
    )


def read_task_file(
    workspace: Path,
    path: str,
    *,
    offset: int = 0,
    max_bytes: int = DEFAULT_READ_BYTES,
    encoding: str = "utf-8",
) -> TaskFileChunk:
    if not 1 <= max_bytes <= MAX_READ_BYTES:
        raise ValueError(f"max_bytes must be from 1 to {MAX_READ_BYTES}, got {max_bytes}.")
    if offset < 0:
        raise ValueError(f"offset must be at least 0, got {offset}.")
    if encoding not in ("utf-8", "base64"):
        raise ValueError(f'encoding must be "utf-8" or "base64", got "{encoding}".')
    base = workspace.resolve()
    target = _target(base, path)
    if not target.exists():
        raise ValueError(f'Task file path "{path}" does not exist in the workspace.')
    if not target.is_file():
        raise ValueError(f'Task file path "{path}" is a directory; list it instead.')
    size = target.stat().st_size
    if offset > size:
        raise ValueError(f'offset {offset} is beyond the end of "{path}" ({size} bytes).')
    with target.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(max_bytes)
    if encoding == "base64":
        content = base64.b64encode(data).decode("ascii")
    else:
        data, content = _decode_utf8(data, path, offset, size)
    following = offset + len(data)
    return TaskFileChunk(
        path=_display(base, target),
        size=size,
        offset=offset,
        bytes_returned=len(data),
        next_offset=following if following < size else None,
        encoding=encoding,
        content=content,
    )


def _decode_utf8(data: bytes, path: str, offset: int, size: int) -> tuple[bytes, str]:
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError as error:
        cut_by_the_page = (
            error.reason == "unexpected end of data"
            and error.end == len(data)
            and offset + len(data) < size
        )
        if cut_by_the_page and error.start > 0:
            kept = data[: error.start]
            return kept, kept.decode("utf-8")
        if cut_by_the_page:
            raise ValueError(
                f"max_bytes {len(data)} cannot hold the UTF-8 character at byte {offset}; "
                f"use at least {_PARTIAL_CHARACTER_BYTES + 1}."
            ) from error
        raise ValueError(
            f'Task file path "{path}" is not valid UTF-8 at byte {offset + error.start}; '
            'request encoding="base64" to read it as bytes.'
        ) from error


def _target(base: Path, path: str) -> Path:
    windows = PureWindowsPath(path)
    if (
        not path.strip()
        or "\x00" in path
        or path.startswith(("/", "\\"))
        or windows.is_absolute()
        or windows.drive
    ):
        raise ValueError(
            f'Task file path "{path}" must be non-empty and relative to the workspace.'
        )
    target = (base / path).resolve()
    try:
        relative = relative_to_base(target, base)
    except ValueError as error:
        raise ValueError(f'Task file path "{path}" escapes the workspace.') from error
    if relative.parts and relative.parts[0].lower().startswith(INTERNAL_STATE_PREFIX):
        raise ValueError(f'Task file path "{path}" is SDK-internal run state.')
    pattern = sensitive_pattern_for(target)
    if pattern is not None:
        raise ValueError(
            f'Task file path "{path}" matches the built-in sensitive-path pattern "{pattern}" '
            "and cannot be read."
        )
    return target


def _entry(base: Path, item: Path) -> TaskFileEntry | None:
    resolved = item.resolve()
    try:
        relative = relative_to_base(resolved, base)
    except ValueError:
        return None
    if relative.parts and relative.parts[0].lower().startswith(INTERNAL_STATE_PREFIX):
        return None
    if sensitive_pattern_for(resolved) is not None:
        return None
    try:
        status = resolved.stat()
    except OSError:
        return None
    directory = resolved.is_dir()
    return TaskFileEntry(
        name=item.name,
        path=relative_to_base(item, base).as_posix(),
        kind="directory" if directory else "file",
        size=None if directory else status.st_size,
        modified_at_utc=datetime.fromtimestamp(status.st_mtime, UTC).isoformat(),
    )


def _display(base: Path, target: Path) -> str:
    relative = relative_to_base(target, base).as_posix()
    return relative or "."
