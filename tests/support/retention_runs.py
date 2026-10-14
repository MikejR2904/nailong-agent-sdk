from datetime import UTC, datetime, timedelta

from nailong_agent_sdk.agent.retention import RetentionPolicy
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.observability.telemetry_models import (
    MetricAvailability,
    MetricObservation,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)

ACTOR = TelemetryActor(kind="system", identifier="test")
POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    return datetime.now(UTC) + timedelta(days=2)


def emit(services, run_id, event_type):
    return services.telemetry.emit(
        event_type,
        TelemetryContext(run_id=run_id),
        actor=ACTOR,
        authority=TelemetryAuthority.DETERMINISTIC,
        status="ok",
    )


def finished_run(services, run_id, *, handles=2, entries=3, terminal=True, metrics=1):
    emit(services, run_id, "agent.run-started")
    for index in range(entries):
        services.audit_logs.append(run_id, "step", {"marker": f"{run_id}-{index}"})
    scoped = services.result_journal.for_run(run_id)
    recorded = [
        scoped.record(
            ToolCall(id=f"{run_id}-c{index}", name="echo", arguments={"i": index}),
            ToolExecutionResult(status="succeeded", output={"run": run_id, "i": index}),
        )
        for index in range(handles)
    ]
    for _ in range(metrics):
        services.telemetry.record_metric(
            MetricObservation(
                metric_id="m",
                run_id=run_id,
                value=1.0,
                unit="count",
                availability=MetricAvailability.AVAILABLE,
                observed_at_utc="2026-01-01T00:00:00+00:00",
            )
        )
    if terminal:
        emit(services, run_id, "agent.terminated")
    return recorded


def journal_files(services):
    return sorted(
        path.name for path in (services.run_root / ".agent-tool-results").glob("result-*.json")
    )
