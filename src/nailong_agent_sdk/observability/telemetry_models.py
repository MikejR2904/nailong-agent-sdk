# Copyright (c) 2026 David Michael Indraputra

"""Telemetry event, context, actor, and metric contracts.

These records deliberately capture only structured outcomes, timings, authority,
evidence links, and hashes; hidden model reasoning is rejected at validation time.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator

from ..foundations.contracts import StrictModel
from ..foundations.errors import assert_no_hidden_reasoning


class TelemetryAuthority(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_MEDIATED = "model-mediated"
    HUMAN = "human"
    TOOL = "tool"
    SYSTEM = "system"


class TelemetrySeverity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MetricAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class TelemetryActor(StrictModel):
    kind: str = Field(min_length=1)
    identifier: str = Field(min_length=1)
    role: str | None = None


class TelemetryContext(StrictModel):
    project_id: str | None = None
    experiment_id: str | None = None
    cohort_id: str | None = None
    run_id: str = Field(min_length=1)
    attempt_id: str | None = None
    controller_id: str | None = None
    node_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    stage: str | None = None
    environment_id: str | None = None


class TelemetryEvent(StrictModel):
    schema_version: str = "telemetry-event-v1"
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sequence: int = Field(default=0, ge=0)
    event_type: str = Field(min_length=1)
    occurred_at_utc: str
    occurred_at_monotonic_ns: int = Field(ge=0)
    context: TelemetryContext
    actor: TelemetryActor
    authority: TelemetryAuthority
    status: str = Field(min_length=1)
    severity: TelemetrySeverity = TelemetrySeverity.INFO
    links: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_event_hash: str | None = None
    integrity_hash: str = ""

    @field_validator("payload", "links")
    @classmethod
    def reject_hidden_reasoning(cls, value: dict[str, Any]) -> dict[str, Any]:
        assert_no_hidden_reasoning(value)
        return value


class MetricDefinition(StrictModel):
    metric_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    formula: str = Field(min_length=1)
    numerator: str | None = None
    denominator: str | None = None
    aggregation: str = Field(min_length=1)
    missing_data_rule: str = Field(min_length=1)
    source_description: str = Field(min_length=1)
    schema_version: str = "metric-definition-v1"


class MetricObservation(StrictModel):
    observation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    metric_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    value: float | None = None
    unit: str = Field(min_length=1)
    availability: MetricAvailability
    unavailable_reason: str | None = None
    source_event_id: str | None = None
    source_artifact_id: str | None = None
    parser_version: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    observed_at_utc: str


class TelemetryRunSummary(StrictModel):
    run_id: str
    event_count: int = Field(ge=0)
    started_at: str | None = None
    last_event_at: str | None = None
    statuses: dict[str, int] = Field(default_factory=dict)
    event_types: dict[str, int] = Field(default_factory=dict)


class ChainBreak(StrictModel):
    sequence: int = Field(ge=1)
    kind: Literal["content-hash-mismatch", "previous-hash-mismatch", "unreadable-entry"]
    message: str = Field(min_length=1)
