# Copyright (c) 2026 David Michael Indraputra

"""Framework-neutral, sanitized interoperability contracts.

The Agent SDK owns authority, evidence, and durable state.  These contracts carry
only a bounded projection and digests across a framework or evaluator boundary.
They deliberately cannot contain credentials, raw transcripts, callback objects,
or executable capabilities.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from ..foundations.contracts import StrictModel
from ._utils import assert_sanitized_interop_value, canonical_digest


class InteropFailureMode(StrEnum):
    """Host-selected result when an optional external operation is unavailable."""

    ESCALATE = "escalate"
    REJECT = "reject"
    FALLBACK_DETERMINISTIC = "fallback-deterministic"


class InteropOperationStatus(StrEnum):
    """Terminal outcome for an external framework or evaluator operation."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class InteropRunEnvelope(StrictModel):
    """Sanitized state crossing an SDK/framework execution boundary.

    ``projection`` may contain only redacted, JSON-compatible values.  Authoritative
    source material remains in SDK-owned stores and is addressed by opaque references.
    """

    schema_version: str = "agent-sdk-interop-envelope-v1"
    run_id: str = Field(min_length=1)
    task_id: str | None = None
    graph_node_id: str | None = None
    external_thread_id: str | None = None
    remaining_turn_budget: int = Field(ge=0)
    state_reference: str | None = None
    projection: dict[str, Any] = Field(default_factory=dict)
    projection_digest: str | None = Field(default=None, min_length=64, max_length=64)
    ledger_event_hash: str | None = None

    @field_validator("projection")
    @classmethod
    def projection_is_safe(cls, projection: dict[str, Any]) -> dict[str, Any]:
        assert_sanitized_interop_value(projection)
        return projection

    @model_validator(mode="after")
    def projection_digest_matches_projection(self) -> InteropRunEnvelope:
        """Derive a digest when absent and reject an incorrect caller-supplied digest."""

        expected = canonical_digest(self.projection)
        if self.projection_digest is None:
            self.projection_digest = expected
            return self
        if self.projection_digest != expected:
            raise ValueError("projection_digest does not match the sanitized projection.")
        return self


class InteropReceipt(StrictModel):
    """Canonical evidence record for an external operation without raw payloads."""

    schema_version: str = "agent-sdk-interop-receipt-v1"
    provider: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    status: InteropOperationStatus
    run_id: str = Field(min_length=1)
    projection_digest: str = Field(min_length=64, max_length=64)
    result_digest: str | None = Field(default=None, min_length=64, max_length=64)
    provider_model: str | None = None
    provider_request_id: str | None = None
    external_checkpoint_id: str | None = None
    external_parent_checkpoint_id: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    detail_code: str | None = None


class ExternalDecisionRequest(StrictModel):
    """Bounded evaluator request with a sanitized state projection and question spec."""

    schema_version: str = "agent-sdk-external-decision-request-v1"
    purpose: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    state: dict[str, Any]
    state_digest: str = Field(min_length=64, max_length=64)
    question_spec_id: str = Field(min_length=1)
    question_spec_version: str = Field(min_length=1)
    question_spec_digest: str = Field(min_length=64, max_length=64)
    model: str = Field(min_length=1)
    deadline_seconds: float = Field(gt=0, le=300)

    @field_validator("state")
    @classmethod
    def state_is_safe(cls, state: dict[str, Any]) -> dict[str, Any]:
        assert_sanitized_interop_value(state)
        return state


class ExternalDecisionResult(StrictModel):
    """Normalized evaluator result suitable only for host policy consumption."""

    schema_version: str = "agent-sdk-external-decision-result-v1"
    status: InteropOperationStatus
    model: str | None = None
    provider_request_id: str | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    response_digest: str | None = Field(default=None, min_length=64, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_ms: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    unavailable_reason: str | None = None

    @field_validator("answers")
    @classmethod
    def answers_are_safe(cls, answers: dict[str, Any]) -> dict[str, Any]:
        assert_sanitized_interop_value(answers)
        return answers


class ExternalDecisionProvider(Protocol):
    """Read-only evaluator protocol; implementations cannot receive authority objects."""

    async def evaluate(self, request: ExternalDecisionRequest) -> ExternalDecisionResult: ...


SanitizedStateProjector = Callable[[Any], dict[str, Any]]
AsyncSanitizedStateProjector = Callable[[Any], Awaitable[dict[str, Any]]]
