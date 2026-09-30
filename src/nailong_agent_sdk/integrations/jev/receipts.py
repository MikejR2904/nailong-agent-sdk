# Copyright (c) 2026 David Michael Indraputra

"""Durable and test-friendly receipt sinks for Jev evaluation operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ...observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetrySeverity,
)
from ...observability.telemetry_store import TelemetryStore
from ..contracts import InteropOperationStatus
from .models import JevDecisionReceipt

JevReceiptSink = Callable[[JevDecisionReceipt], Awaitable[None] | None]


@dataclass
class InMemoryJevReceiptStore:
    """Test/development sink; production hosts should bind receipts to a durable ledger."""

    receipts: list[JevDecisionReceipt] = field(default_factory=list)

    def __call__(self, receipt: JevDecisionReceipt) -> None:
        self.receipts.append(receipt)


class TelemetryJevReceiptSink:
    """Emit receipt metadata only; submitted state and questions never enter telemetry."""

    def __init__(
        self,
        telemetry: TelemetryStore,
        context_factory: Callable[[JevDecisionReceipt], TelemetryContext],
    ) -> None:
        self._telemetry = telemetry
        self._context_factory = context_factory

    def __call__(self, receipt: JevDecisionReceipt) -> None:
        self._telemetry.emit(
            "interop.jev.decision",
            self._context_factory(receipt),
            actor=TelemetryActor(kind="evaluator", identifier="typesafe-jev", role="advisory"),
            authority=TelemetryAuthority.TOOL,
            status=receipt.status.value,
            severity=(
                TelemetrySeverity.INFO
                if receipt.status is InteropOperationStatus.SUCCEEDED
                else TelemetrySeverity.WARNING
            ),
            payload=receipt.model_dump(mode="json"),
        )
