# Copyright (c) 2026 David Michael Indraputra

"""Write approval lifecycle events into the telemetry ledger, which is hash-chained."""

from __future__ import annotations

from collections.abc import Collection

from ..observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetrySeverity,
)
from ..observability.telemetry_store import TelemetryStore
from .approvals import ApprovalEvent, ApprovalEventKind

_SEVERITY = {
    ApprovalEventKind.REVOKED: TelemetrySeverity.WARNING,
    ApprovalEventKind.DENIED: TelemetrySeverity.WARNING,
    ApprovalEventKind.EXPIRED: TelemetrySeverity.WARNING,
}


class TelemetryApprovalSink:
    """One ``approval.<kind>`` telemetry event per approval event, under the run it belongs to.

    ``kinds`` limits which events are written, for a caller that already records some of them
    through its own events. The event's authority is the decider's when the approval names one
    (a human who granted or revoked it) and the system's otherwise.
    """

    def __init__(
        self,
        telemetry: TelemetryStore,
        *,
        kinds: Collection[ApprovalEventKind] | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._kinds = None if kinds is None else frozenset(kinds)

    def record(self, event: ApprovalEvent) -> None:
        if self._kinds is not None and event.kind not in self._kinds:
            return
        self._telemetry.emit(
            f"approval.{event.kind.value}",
            TelemetryContext(run_id=event.run_id, node_id=event.node_id, task_id=event.node_id),
            actor=TelemetryActor(
                kind="approval-registry", identifier="approval-registry", role="runtime"
            ),
            authority=event.decision_authority or TelemetryAuthority.SYSTEM,
            status=event.kind.value,
            severity=_SEVERITY.get(event.kind, TelemetrySeverity.INFO),
            payload=event.model_dump(mode="json", exclude_none=True),
        )
