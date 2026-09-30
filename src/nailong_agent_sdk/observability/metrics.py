# Copyright (c) 2026 David Michael Indraputra

"""Registration and recording functions over the standard metric catalogue."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from .metric_definitions import STANDARD_METRIC_DEFINITIONS
from .telemetry_helpers import metric_observation
from .telemetry_models import MetricObservation, TelemetryContext, TelemetryEvent
from .telemetry_store import TelemetryStore


def register_standard_metric_definitions(store: TelemetryStore) -> None:
    for definition in STANDARD_METRIC_DEFINITIONS:
        store.register_metric_definition(definition)


def record_metric_value(
    store: TelemetryStore,
    context: TelemetryContext,
    metric_id: str,
    value: float,
    unit: str,
    *,
    source_event_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> MetricObservation:
    return store.record_metric(
        metric_observation(
            metric_id,
            context.run_id,
            unit=unit,
            value=value,
            source_event_id=source_event_id,
            context=details or {},
        )
    )


def record_metric_unavailable(
    store: TelemetryStore,
    context: TelemetryContext,
    metric_id: str,
    unit: str,
    reason: str,
    *,
    source_event_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> MetricObservation:
    return store.record_metric(
        metric_observation(
            metric_id,
            context.run_id,
            unit=unit,
            value=None,
            unavailable_reason=reason,
            source_event_id=source_event_id,
            context=details or {},
        )
    )


def record_terminal_agent_metrics(
    store: TelemetryStore,
    context: TelemetryContext,
    events: Iterable[TelemetryEvent],
    profile: dict[str, Any],
    *,
    completed: bool,
    terminal_reason: str | None,
    source_event_id: str | None,
) -> None:
    """Reduce observed run events into stable end-of-run count/duration metrics."""

    events = tuple(events)
    counts = Counter(event.event_type for event in events)
    statuses = Counter(
        str(event.payload.get("details", {}).get("status", ""))
        for event in events
        if event.event_type == "agent.tool-completed"
    )
    verification_failures = sum(
        event.event_type == "agent.verification-completed"
        and event.payload.get("details", {}).get("passed") is False
        for event in events
    )
    values = {
        "agent.model_turn_attempt_count": float(counts["agent.turn-started"]),
        "agent.recovery_iteration_count": float(max(counts["agent.turn-started"] - 1, 0)),
        "agent.output_rejection_count": float(counts["agent.output-rejected"]),
        "agent.tool_call_attempt_count": float(counts["agent.tool-requested"]),
        "agent.tool_failure_count": float(statuses["failed"]),
        "agent.tool_blocked_count": float(statuses["blocked"]),
        "agent.verification_failure_count": float(verification_failures),
        "agent.escalation_count": float(counts["agent.escalated"]),
        "agent.unsolved_escalation_count": float(0 if completed else counts["agent.escalated"]),
        "context.deadlock_count": float(
            1 if terminal_reason and "CONTEXT_DEADLOCK" in terminal_reason else 0
        ),
    }
    for metric_id, value in values.items():
        record_metric_value(
            store, context, metric_id, value, "count", source_event_id=source_event_id
        )
    for metric_id, field in (
        ("agent.wall_duration_ms", "wall_duration_ns"),
        ("agent.process_cpu_duration_ms", "process_cpu_duration_ns"),
    ):
        duration = profile.get(field)
        if isinstance(duration, int):
            record_metric_value(
                store,
                context,
                metric_id,
                duration / 1_000_000,
                "milliseconds",
                source_event_id=source_event_id,
            )
        else:
            record_metric_unavailable(
                store,
                context,
                metric_id,
                "milliseconds",
                "Profiler did not complete.",
                source_event_id=source_event_id,
            )
