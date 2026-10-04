# Copyright (c) 2026 David Michael Indraputra

"""Use Jev for a bounded, conservative single/multi-agent architecture-routing signal."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, field_validator

from ...foundations.contracts import StrictModel
from ..contracts import ExternalDecisionProvider, InteropFailureMode, InteropOperationStatus
from .models import (
    _JEV_ANSWER_ADAPTER,
    JevAnswer,
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevDecisionRequest,
    JevDecisionResult,
    JevQuestionSpec,
)


class JevArchitectureRoutingPolicy(StrictModel):
    """Conservative local policy for an optional architecture-routing advisory.

    The deterministic complexity route is authoritative.  Jev may only lift a
    deterministic ``single-agent`` result to ``multi-agent`` when its bounded
    choice response has sufficient confidence.  It may never reduce a
    deterministic multi-agent route.
    """

    model: str = Field(min_length=1)
    minimum_confidence: float = Field(default=0.8, ge=0, le=1)
    on_unavailable: InteropFailureMode = InteropFailureMode.FALLBACK_DETERMINISTIC
    policy_version: str = "jev-architecture-routing-v1"

    @field_validator("on_unavailable")
    @classmethod
    def on_unavailable_has_a_routing_meaning(cls, mode: InteropFailureMode) -> InteropFailureMode:
        if mode is InteropFailureMode.ESCALATE:
            raise ValueError(
                f'on_unavailable "{mode.value}" is not supported by the architecture router, '
                "which returns advice and has no escalation channel; use "
                f'"{InteropFailureMode.FALLBACK_DETERMINISTIC.value}" or '
                f'"{InteropFailureMode.REJECT.value}".'
            )
        return mode


class JevArchitectureAdvice(StrictModel):
    """Receipt-backed, non-authoritative single/multi-agent route advice."""

    deterministic_architecture: Literal["single-agent", "multi-agent"]
    architecture: Literal["single-agent", "multi-agent"]
    used_deterministic_fallback: bool
    reason: str = Field(min_length=1)
    result: JevDecisionResult | None = None


class JevArchitectureRouter:
    """Use Jev for a bounded, conservative architecture-routing signal.

    The input is a caller-sanitized summary of plan and gap metadata.  The
    evaluator owns receipt persistence; this router owns only the local,
    monotonic policy which maps a typed answer to a route.
    """

    def __init__(
        self,
        evaluator: ExternalDecisionProvider,
        policy: JevArchitectureRoutingPolicy,
    ) -> None:
        self._evaluator = evaluator
        self._policy = policy

    async def advise(
        self,
        *,
        run_id: str,
        deterministic_architecture: Literal["single-agent", "multi-agent"],
        state: dict[str, Any],
        deadline_seconds: float = 20,
    ) -> JevArchitectureAdvice:
        if deterministic_architecture == "multi-agent":
            return JevArchitectureAdvice(
                deterministic_architecture=deterministic_architecture,
                architecture="multi-agent",
                used_deterministic_fallback=True,
                reason="Deterministic route already requires multi-agent execution.",
            )
        request = JevDecisionRequest(
            purpose="routing",
            run_id=run_id,
            state=state,
            question_spec=JevQuestionSpec(
                spec_id="agent-sdk-architecture-routing",
                version="v1",
                questions={
                    "architecture": JevChoiceQuestion(
                        instructions=(
                            "Select the workflow architecture warranted by the provided "
                            "bounded task metadata."
                        ),
                        criteria={
                            "single-agent": "One bounded worker can execute the approved plan.",
                            "multi-agent": (
                                "Parallel or separately scoped workers are warranted by the "
                                "approved plan metadata."
                            ),
                        },
                    )
                },
            ),
            model=self._policy.model,
            deadline_seconds=deadline_seconds,
        )
        result = await self._evaluator.evaluate(request)
        if result.status is not InteropOperationStatus.SUCCEEDED:
            return self._unavailable_advice(result)
        answer = result.answers.get("architecture")
        if isinstance(answer, JevChoiceAnswer):
            parsed: JevAnswer | None = answer
        elif isinstance(answer, Mapping):
            parsed = _JEV_ANSWER_ADAPTER.validate_python(answer)
        else:
            parsed = None
        if not isinstance(parsed, JevChoiceAnswer):
            return self._unavailable_advice(result, "Jev routing response lacks a choice answer.")
        if parsed.choice == "multi-agent" and parsed.confidence >= self._policy.minimum_confidence:
            return JevArchitectureAdvice(
                deterministic_architecture=deterministic_architecture,
                architecture="multi-agent",
                used_deterministic_fallback=False,
                reason=(f"Jev advisory lifted architecture at confidence {parsed.confidence:.3f}."),
                result=result,
            )
        return JevArchitectureAdvice(
            deterministic_architecture=deterministic_architecture,
            architecture="single-agent",
            used_deterministic_fallback=True,
            reason=(
                "Deterministic single-agent route retained because Jev did not provide a "
                "high-confidence multi-agent lift."
            ),
            result=result,
        )

    def _unavailable_advice(
        self,
        result: JevDecisionResult,
        detail: str | None = None,
    ) -> JevArchitectureAdvice:
        if self._policy.on_unavailable is InteropFailureMode.REJECT:
            raise RuntimeError(detail or result.unavailable_reason or "Jev routing is unavailable.")
        return JevArchitectureAdvice(
            deterministic_architecture="single-agent",
            architecture="single-agent",
            used_deterministic_fallback=True,
            reason=detail or result.unavailable_reason or "Jev routing is unavailable.",
            result=result,
        )
