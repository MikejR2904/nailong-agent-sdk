# Copyright (c) 2026 David Michael Indraputra

"""Identifier utilities for safe naming and unique reservations.
This module enforces rules for IDs and filenames:
- Validate that identifiers follow length and character rules.
- Detect duplicates and require uniqueness.
- Convert arbitrary strings into safe, injective filenames.
- Reserve sequential identifiers without collisions, using exclusive file claims."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Hashable, Iterable
from pathlib import Path

from .atomic_io import claim_exclusive

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_FILE_NAME = re.compile(r"[A-Za-z0-9._-]+")
_UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_READABLE_CHARS = 80
_MAX_IDENTIFIER_CHARS = 128
_MAX_REPORTED_REPEATS = 10


def is_valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and _IDENTIFIER.fullmatch(value) is not None
        and not value.endswith(".")
    )


def validate_identifier(value: object, kind: str) -> str:
    if not isinstance(value, str) or not is_valid_identifier(value):
        raise ValueError(
            f"{kind} {value!r} is not a valid identifier: it must be 1-128 characters of "
            'letters, digits, ".", "_" or "-", start with a letter or digit, and not end '
            'with ".".'
        )
    return value


def repeated_values(values: Iterable[Hashable]) -> list[Hashable]:
    seen: set[Hashable] = set()
    repeated: dict[Hashable, None] = {}
    for value in values:
        if value in seen:
            repeated[value] = None
        seen.add(value)
    return list(repeated)


def require_unique(values: Iterable[Hashable], label: str) -> None:
    repeated = repeated_values(values)
    if not repeated:
        return
    shown = ", ".join(f'"{value}"' for value in repeated[:_MAX_REPORTED_REPEATS])
    hidden = len(repeated) - _MAX_REPORTED_REPEATS
    more = f" and {hidden} more" if hidden > 0 else ""
    raise ValueError(f"{label} must be unique; repeated: {shown}{more}.")


def file_safe_name(value: str) -> str:
    # Convert a string to a safe filename; if already safe, return it,
    # otherwise, replace unsafe chars, trim, and append a short SHA-256 digest
    if (
        len(value) <= _MAX_IDENTIFIER_CHARS
        and _SAFE_FILE_NAME.fullmatch(value) is not None
        and value not in {".", ".."}
        and not value.endswith(".")
    ):
        return value
    readable = _UNSAFE_CHARACTERS.sub("_", value).strip("._") or "id"
    digest = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return f"{readable[:_MAX_READABLE_CHARS]}~{digest}"


def reserve_sequential_identifier(
    claims: Path, prefix: str, is_taken: Callable[[str], bool], *, start: int = 1
) -> tuple[str, int]:
    # Reserve a unique sequential identifier by 1) skipping taken identifiers and
    # 2) use claim_exclusive to avoid collisions across processes
    number = max(1, start)
    while True:
        candidate = f"{prefix}-{number}"
        number += 1
        if not is_taken(candidate) and claim_exclusive(claims / candidate):
            return candidate, number
