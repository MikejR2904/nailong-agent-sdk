# Copyright (c) 2026 David Michael Indraputra

"""Compose deterministic verification with an optional non-authoritative Jev signal."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping

from pydantic import Field

from ...agent.verification import (
    VerificationContext,
    VerificationDecision,
    VerificationGate,
    VerificationReturn,
    normalize_verification_return,
)
from ...foundations.contracts import StrictModel
from ..contracts import InteropFailureMode, InteropOperationStatus
from .models import (
    _JEV_ANSWER_ADAPTER,
    JevAnswer,
    JevDecisionProvider,
    JevDecisionRequest,
    JevDecisionResult,
    JevNoulAnswer,
)


class JevAdvisoryPolicy(StrictModel):
    """Fixed local policy for converting one Jev probability into a gate outcome."""

    question_id: str = Field(min_length=1)
    minimum_noul: float = Field(ge=0, le=1)
    on_unavailable: InteropFailureMode = InteropFailureMode.ESCALATE
    policy_version: str = "jev-advisory-v1"


JevRequestFactory = Callable[[VerificationContext], JevDecisionRequest]


class JevAdvisoryVerificationGate:
    """Compose deterministic verification with an optional non-authoritative Jev signal.

    The local deterministic gate runs first.  Jev is queried only after that gate accepts.
    A provider decision may lower confidence and request rejection/escalation; it can never
    turn a failed local verification decision into an accepted result.
    """

    def __init__(
        self,
        deterministic_gate: VerificationGate,
        evaluator: JevDecisionProvider,
        request_factory: JevRequestFactory,
        policy: JevAdvisoryPolicy,
    ) -> None:
        self._deterministic_gate = deterministic_gate
        self._evaluator = evaluator
        self._request_factory = request_factory
        self._policy = policy

    async def verify(self, context: VerificationContext) -> VerificationReturn:
        local = self._deterministic_gate.verify(context)
        if inspect.isawaitable(local):
            local = await local
        normalized = normalize_verification_return(local)
        if not normalized.passed:
            return normalized
        request = self._request_factory(context)
        result = await self._evaluator.evaluate(request)
        return self._apply_policy(result)

    def _apply_policy(self, result: JevDecisionResult) -> VerificationDecision:
        if result.status is not InteropOperationStatus.SUCCEEDED:
            return _unavailable_decision(self._policy.on_unavailable, result.unavailable_reason)
        answer = result.answers.get(self._policy.question_id)
        if isinstance(answer, JevNoulAnswer):
            parsed: JevAnswer = answer
        elif isinstance(answer, Mapping):
            parsed = _JEV_ANSWER_ADAPTER.validate_python(answer)
        else:
            return _unavailable_decision(
                self._policy.on_unavailable,
                f'Jev result lacks question "{self._policy.question_id}".',
            )
        if not isinstance(parsed, JevNoulAnswer):
            return _unavailable_decision(
                self._policy.on_unavailable,
                f'Jev question "{self._policy.question_id}" must return a noul answer.',
            )
        if parsed.noul >= self._policy.minimum_noul:
            return VerificationDecision(
                True,
                f"Jev advisory probability {parsed.noul:.3f} met policy.",
            )
        return VerificationDecision(
            False,
            f"Jev advisory probability {parsed.noul:.3f} is below {self._policy.minimum_noul:.3f}.",
        )


def _unavailable_decision(mode: InteropFailureMode, reason: str | None) -> VerificationDecision:
    detail = reason or "Jev advisory evaluation is unavailable."
    if mode is InteropFailureMode.FALLBACK_DETERMINISTIC:
        return VerificationDecision(True, f"Deterministic gate accepted; Jev fallback: {detail}")
    if mode is InteropFailureMode.ESCALATE:
        return VerificationDecision(False, f"Escalation required: {detail}")
    return VerificationDecision(False, f"Rejected because Jev is unavailable: {detail}")
