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
    clip_evidence_text,
    normalize_verification_return,
)
from ...foundations.contracts import StrictModel
from ...observability.telemetry_models import TelemetryAuthority
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
JEV_ADVISORY_VERIFIER_ID = "jev-advisory"
_EVIDENCE_TEXT_CHARS = 512


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
        return self._apply_policy(result, normalized)

    def _apply_policy(
        self, result: JevDecisionResult, local: VerificationDecision
    ) -> VerificationDecision:
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
        evidence: dict[str, str | float] = {
            "question_id": clip_evidence_text(self._policy.question_id, _EVIDENCE_TEXT_CHARS),
            "noul": parsed.noul,
            "minimum_noul": self._policy.minimum_noul,
            "policy_version": clip_evidence_text(self._policy.policy_version, _EVIDENCE_TEXT_CHARS),
        }
        if local.verifier_id is not None:
            evidence["local_verifier_id"] = local.verifier_id
        if parsed.noul >= self._policy.minimum_noul:
            return VerificationDecision(
                True,
                f"Jev advisory probability {parsed.noul:.3f} met policy.",
                verifier_id=JEV_ADVISORY_VERIFIER_ID,
                authority=TelemetryAuthority.MODEL_MEDIATED,
                evidence=evidence,
            )
        return VerificationDecision(
            False,
            f"Jev advisory probability {parsed.noul:.3f} is below {self._policy.minimum_noul:.3f}.",
            verifier_id=JEV_ADVISORY_VERIFIER_ID,
            authority=TelemetryAuthority.MODEL_MEDIATED,
            evidence=evidence,
        )


def _unavailable_decision(mode: InteropFailureMode, reason: str | None) -> VerificationDecision:
    detail = reason or "Jev advisory evaluation is unavailable."
    evidence = {
        "unavailable": clip_evidence_text(detail, _EVIDENCE_TEXT_CHARS),
        "on_unavailable": mode.value,
    }
    if mode is InteropFailureMode.FALLBACK_DETERMINISTIC:
        return _fallback(True, f"Deterministic gate accepted; Jev fallback: {detail}", evidence)
    if mode is InteropFailureMode.ESCALATE:
        return _fallback(False, f"Escalation required: {detail}", evidence)
    return _fallback(False, f"Rejected because Jev is unavailable: {detail}", evidence)


def _fallback(passed: bool, reason: str, evidence: dict[str, str]) -> VerificationDecision:
    return VerificationDecision(
        passed,
        reason,
        verifier_id=JEV_ADVISORY_VERIFIER_ID,
        authority=TelemetryAuthority.DETERMINISTIC,
        evidence=evidence,
    )
