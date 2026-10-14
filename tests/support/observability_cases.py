from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)

ACTOR = TelemetryActor(kind="agent", identifier="tester")


def emit(store, run_id="run-1", event_type="agent.test", payload=None, status="ok"):
    return store.emit(
        event_type,
        TelemetryContext(run_id=run_id),
        actor=ACTOR,
        authority=TelemetryAuthority.DETERMINISTIC,
        status=status,
        payload=payload or {},
    )
