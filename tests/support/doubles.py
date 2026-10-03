"""Deterministic test doubles for SDK tests. Not part of the SDK package.

These replace an external model, vision service or tool backend inside tests
only; production code never imports them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter

from nailong_agent_sdk.agent.model import ModelContext
from nailong_agent_sdk.foundations.contracts import AgentTurn, ToolDefinition, ToolExecutionResult
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.specifications.documents import DocumentNode
from nailong_agent_sdk.specifications.vision import VisionProposal
from nailong_agent_sdk.tools.tools import ToolInvocationContext

_TURNS: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)


class ScriptedModel:
    """Returns pre-written turns by iteration; records every context it was given."""

    def __init__(self, turns: Sequence[AgentTurn | dict[str, Any]]) -> None:
        self._turns = [_TURNS.validate_python(turn) for turn in turns]
        self.calls: list[ModelContext] = []

    async def next_turn(self, context: ModelContext) -> AgentTurn:
        self.calls.append(context)
        index = context.iteration - 1
        if index >= len(self._turns):
            raise AgentSdkError("SCRIPTED_MODEL_EXHAUSTED", "No scripted turn remains.")
        return self._turns[index]


class ScriptedVisionAdapter:
    def __init__(self, proposals: list[VisionProposal]) -> None:
        self._proposals = list(proposals)

    async def extract(self, node: DocumentNode) -> VisionProposal:
        del node
        if not self._proposals:
            return VisionProposal(confidence=0, structure={}, errors=["No proposal remains."])
        return self._proposals.pop(0)


@dataclass
class RecordingToolExecutor:
    """Answers each tool with a configured result and records the call."""

    results_by_name: dict[str, ToolExecutionResult]
    calls: list[ToolInvocationContext] = field(default_factory=list)

    async def execute(
        self, tool: ToolDefinition, context: ToolInvocationContext
    ) -> ToolExecutionResult:
        self.calls.append(context)
        return self.results_by_name.get(
            tool.name,
            ToolExecutionResult(status="failed", error=f'No result configured for "{tool.name}".'),
        )
