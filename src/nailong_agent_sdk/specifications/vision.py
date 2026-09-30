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


class UnconfiguredVisionAdapter:
    async def extract(self, node: DocumentNode) -> VisionProposal:
        del node
        return VisionProposal(
            confidence=0, structure={}, errors=["No vision adapter is configured."]
        )


class ScriptedVisionAdapter:
    def __init__(self, proposals: list[VisionProposal]) -> None:
        self._proposals = list(proposals)

    async def extract(self, node: DocumentNode) -> VisionProposal:
        del node
        if not self._proposals:
            return VisionProposal(
                confidence=0, structure={}, errors=["No scripted proposal remains."]
            )
        return self._proposals.pop(0)
