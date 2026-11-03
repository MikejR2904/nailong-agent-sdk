# Copyright (c) 2026 David Michael Indraputra

"""Skills as files: a markdown file becomes task context the model is given."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..foundations.contracts import SkillContext


def skill(path: Path, *, skill_id: str | None = None) -> SkillContext:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(
            f"Skill file {path} is empty; a skill needs content for the model to read."
        )
    return SkillContext(
        id=skill_id or path.stem,
        version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        content=text,
    )
