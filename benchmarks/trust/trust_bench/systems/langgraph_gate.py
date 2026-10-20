# Copyright (c) 2026 David Michael Indraputra

"""The acceptance node a careful LangGraph user adds after the agent."""

from __future__ import annotations

import operator
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage

from ..acceptance import Acceptance

GATE_NODE = "gate"


class GateState(TypedDict):
    history: Annotated[list[BaseMessage], operator.add]
    verdict: dict[str, Any]


def gate_node(
    acceptance: Acceptance, workspace: Path
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def gate(state: dict[str, Any]) -> dict[str, Any]:
        passed, reason = acceptance.check(workspace)
        verdict = {
            "accepted": passed,
            "verifier_id": acceptance.verifier_id,
            "authority": "deterministic",
            "reason": reason,
            "subject_sha256": acceptance.subject_digest(workspace),
        }
        return {"verdict": verdict}

    return gate


def rejection(verdict: dict[str, Any] | None) -> str | None:
    if verdict is None:
        return "VERIFICATION_GATE_NO_DECISION: the acceptance node recorded no verdict"
    if verdict.get("accepted"):
        return None
    return f"VERIFICATION_GATE_REJECTED: {verdict.get('reason')}"
