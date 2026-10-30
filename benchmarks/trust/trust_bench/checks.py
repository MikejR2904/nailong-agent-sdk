# Copyright (c) 2026 David Michael Indraputra

"""Quietness of a defect: which layer of checking is the first to reject a deliverable."""

from __future__ import annotations

from pathlib import Path

from .cases import Case

LEVELS = (0, 1, 2)
LEVEL_NAMES = {0: "q0 fails the format check", 1: "q1 fails the visible check", 2: "q2 quiet"}


def quiet_level(case: Case, workspace: Path, path: str, content: bytes) -> int | None:
    """None for a correct deliverable, else 0 (malformed), 1 (inconsistent) or 2 (quiet)."""
    if not case.is_defective(path, content):
        return None
    if not case.generic_ok(path, content):
        return 0
    if not case.visible_ok(workspace, path, content):
        return 1
    return 2


def deliverable_bytes(case: Case, workspace: Path) -> bytes | None:
    target = workspace / case.deliverable
    return target.read_bytes() if target.is_file() else None
