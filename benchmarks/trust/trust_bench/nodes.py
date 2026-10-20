# Copyright (c) 2026 David Michael Indraputra

"""One agent node of a multi-agent case."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeSpec:
    label: str
    tools: tuple[str, ...]
    outputs: tuple[str, ...]
    dependencies: tuple[str, ...]
    task_text: str
