"""Scripted model double for the app's tests (the SDK ships no test doubles)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nailong_agent_sdk.foundations.contracts import AgentTurn
from nailong_agent_sdk.foundations.errors import AgentSdkError
from pydantic import TypeAdapter

_TURNS: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)


class ScriptedModel:
    """Returns pre-written turns by iteration and records each context it saw."""

    def __init__(self, turns: Sequence[AgentTurn | dict[str, Any]]) -> None:
        self._turns = [_TURNS.validate_python(turn) for turn in turns]
        self.calls: list[Any] = []

    async def next_turn(self, context: Any) -> AgentTurn:
        self.calls.append(context)
        index = context.iteration - 1
        if index >= len(self._turns):
            raise AgentSdkError("SCRIPTED_MODEL_EXHAUSTED", "No scripted turn remains.")
        return self._turns[index]
