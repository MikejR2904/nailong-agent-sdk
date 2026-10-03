# Copyright (c) 2026 David Michael Indraputra

"""Vision-extraction adapters for image and diagram document nodes."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from ..foundations.contracts import StrictModel
from .documents import DocumentNode


class VisionProposal(StrictModel):
    confidence: float = Field(ge=0, le=1)
    structure: dict[str, Any]
    errors: list[str] = Field(default_factory=list)


class VisionAdapter(Protocol):
    async def extract(self, node: DocumentNode) -> VisionProposal: ...
