"""Tool-call episode payloads shaped exactly as ``BaseAgent`` records them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult


@dataclass(frozen=True)
class EpisodePayload:
    call: ToolCall
    result: ToolExecutionResult
    payload: dict[str, Any]
    summary: str


def build_payload(
    step: Mapping[str, Any], index: int, consumed_episode_ids: Sequence[str]
) -> EpisodePayload:
    outcome = step["outcome"]
    call = ToolCall(
        id=f"call-{index + 1}",
        name=step["tool"],
        arguments=dict(step["arguments"]),
        consumed_episode_ids=list(consumed_episode_ids),
    )
    result = ToolExecutionResult(
        status=outcome["status"], output=outcome.get("output"), error=outcome.get("error")
    )
    return EpisodePayload(
        call=call,
        result=result,
        payload={
            "call": call.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        },
        summary=BaseAgent._episode_summary_text(call, result),
    )
