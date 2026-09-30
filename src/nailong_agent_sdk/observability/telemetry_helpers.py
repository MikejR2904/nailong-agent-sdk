# Copyright (c) 2026 David Michael Indraputra

"""Small standalone helpers for constructing telemetry timestamps and metric observations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .telemetry_models import MetricAvailability, MetricObservation


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def metric_observation(
    metric_id: str,
    run_id: str,
    *,
    unit: str,
    value: float | None,
    unavailable_reason: str | None = None,
    source_event_id: str | None = None,
    source_artifact_id: str | None = None,
    parser_version: str | None = None,
    context: dict[str, Any] | None = None,
) -> MetricObservation:
    availability = (
        MetricAvailability.AVAILABLE if value is not None else MetricAvailability.UNAVAILABLE
    )
    return MetricObservation(
        metric_id=metric_id,
        run_id=run_id,
        value=value,
        unit=unit,
        availability=availability,
        unavailable_reason=unavailable_reason,
        source_event_id=source_event_id,
        source_artifact_id=source_artifact_id,
        parser_version=parser_version,
        context=context or {},
        observed_at_utc=utc_now(),
    )
