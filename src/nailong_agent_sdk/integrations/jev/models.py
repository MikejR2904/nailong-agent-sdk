# Copyright (c) 2026 David Michael Indraputra

"""Jev question, answer, request, and receipt contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, TypeAdapter, field_validator

from ...foundations.contracts import StrictModel
from .._utils import canonical_digest, checked_interop_value, content_digest
from ..contracts import InteropOperationStatus, InteropReceipt

MAX_CHOICE_CRITERIA = 255


class JevQuestionKind(StrEnum):
    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


class JevNoulQuestion(StrictModel):
    type: Literal[JevQuestionKind.NOUL] = JevQuestionKind.NOUL
    instructions: str | dict[str, Any] | list[Any]
    criteria: dict[Literal[True, False], str | dict[str, Any] | list[Any]] | None = None


class JevChoiceQuestion(StrictModel):
    type: Literal[JevQuestionKind.CHOICE] = JevQuestionKind.CHOICE
    instructions: str | dict[str, Any] | list[Any]
    criteria: dict[str, str | dict[str, Any] | list[Any] | None] = Field(
        min_length=2,
        max_length=MAX_CHOICE_CRITERIA,
    )


class JevScoreQuestion(StrictModel):
    type: Literal[JevQuestionKind.SCORE] = JevQuestionKind.SCORE
    instructions: str | dict[str, Any] | list[Any]
    criteria: list[str | dict[str, Any] | list[Any]] = Field(min_length=2, max_length=10)


JevQuestion = Annotated[
    JevNoulQuestion | JevChoiceQuestion | JevScoreQuestion,
    Field(discriminator="type"),
]


class JevQuestionSpec(StrictModel):
    """Host-owned, versioned questions permitted for one Jev purpose."""

    spec_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    questions: dict[str, JevQuestion] = Field(min_length=1, max_length=64)

    @field_validator("questions")
    @classmethod
    def question_ids_are_nonempty(cls, questions: dict[str, JevQuestion]) -> dict[str, JevQuestion]:
        if any(not question_id.strip() for question_id in questions):
            raise ValueError("Jev question identifiers must be non-empty.")
        return questions

    @property
    def digest(self) -> str:
        return content_digest(self.model_dump(mode="json"))


class JevDecisionRequest(StrictModel):
    """One bounded, redacted Jev evaluation request."""

    schema_version: str = "agent-sdk-jev-request-v1"
    purpose: Literal["verification", "exploration", "routing"]
    run_id: str = Field(min_length=1)
    state: dict[str, Any]
    question_spec: JevQuestionSpec
    model: str = Field(min_length=1)
    deadline_seconds: float = Field(default=20, gt=0, le=300)
    max_attempts: int = Field(default=1, ge=1, le=3)

    @field_validator("state")
    @classmethod
    def state_is_redacted_json(cls, state: dict[str, Any]) -> dict[str, Any]:
        return checked_interop_value(state)

    @property
    def state_digest(self) -> str:
        return canonical_digest(self.state)


class JevNoulAnswer(StrictModel):
    type: Literal[JevQuestionKind.NOUL] = JevQuestionKind.NOUL
    noul: float = Field(ge=0, le=1)


class JevChoiceAnswer(StrictModel):
    type: Literal[JevQuestionKind.CHOICE] = JevQuestionKind.CHOICE
    choice: str = Field(min_length=1)
    probabilities: dict[str, float] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class JevScoreAnswer(StrictModel):
    type: Literal[JevQuestionKind.SCORE] = JevQuestionKind.SCORE
    score: float
    legend: dict[str, str] = Field(min_length=1)
    probabilities: dict[str, float] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


JevAnswer = Annotated[
    JevNoulAnswer | JevChoiceAnswer | JevScoreAnswer,
    Field(discriminator="type"),
]
_JEV_ANSWER_ADAPTER: TypeAdapter[JevAnswer] = TypeAdapter(JevAnswer)


class JevDecisionResult(StrictModel):
    """Typed, remote-evaluator result without its submitted state or question text."""

    schema_version: str = "agent-sdk-jev-result-v1"
    status: InteropOperationStatus
    model: str | None = None
    provider_request_id: str | None = None
    answers: dict[str, JevAnswer] = Field(default_factory=dict)
    state_digest: str = Field(min_length=64, max_length=64)
    question_spec_digest: str = Field(min_length=64, max_length=64)
    response_digest: str | None = Field(default=None, min_length=64, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_ms: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    unavailable_reason: str | None = None

    @classmethod
    def unavailable(
        cls,
        request: JevDecisionRequest,
        reason: str,
        *,
        duration_ms: float,
    ) -> JevDecisionResult:
        return cls(
            status=InteropOperationStatus.UNAVAILABLE,
            state_digest=request.state_digest,
            question_spec_digest=request.question_spec.digest,
            duration_ms=duration_ms,
            unavailable_reason=reason,
        )


class JevDecisionProvider(Protocol):
    """Read-only Jev evaluator protocol; implementations cannot receive authority objects."""

    async def evaluate(self, request: JevDecisionRequest) -> JevDecisionResult: ...


class JevDecisionReceipt(InteropReceipt):
    """Receipt that binds a Jev evaluation to an SDK hash-ledger event externally."""

    schema_version: str = "agent-sdk-jev-receipt-v1"
    question_spec_id: str = Field(min_length=1)
    question_spec_version: str = Field(min_length=1)
    question_spec_digest: str = Field(min_length=64, max_length=64)
    policy_version: str = Field(min_length=1)
    policy_outcome: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
