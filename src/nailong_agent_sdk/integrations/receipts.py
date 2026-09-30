# Copyright (c) 2026 David Michael Indraputra

"""Durable and test-friendly receipt sinks for external integration operations.

Receipt sinks deliberately persist only structured receipt metadata and digests. They
must never receive raw framework state, prompts, messages, tool payloads, credentials,
or approval material.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetrySeverity,
)
from ..observability.telemetry_store import TelemetryStore
from .contracts import InteropOperationStatus, InteropReceipt

InteropReceiptSink = Callable[[InteropReceipt], Awaitable[None] | None]


@dataclass
class InMemoryInteropReceiptStore:
    """Test/development receipt sink; production hosts must select durable telemetry."""

    receipts: list[InteropReceipt] = field(default_factory=list)

    def __call__(self, receipt: InteropReceipt) -> None:
        self.receipts.append(receipt)


class TelemetryInteropReceiptSink:
    """Persist a digest-only interoperability receipt in the SDK telemetry ledger."""

    def __init__(
        self,
        telemetry: TelemetryStore,
        context_factory: Callable[[InteropReceipt], TelemetryContext],
    ) -> None:
        self._telemetry = telemetry
        self._context_factory = context_factory

    def __call__(self, receipt: InteropReceipt) -> None:
        self._telemetry.emit(
            "interop.external_operation",
            self._context_factory(receipt),
            actor=TelemetryActor(
                kind="external-framework",
                identifier=receipt.provider,
                role="interop-boundary",
            ),
            authority=TelemetryAuthority.SYSTEM,
            status=receipt.status.value,
            severity=(
                TelemetrySeverity.INFO
                if receipt.status is InteropOperationStatus.SUCCEEDED
                else TelemetrySeverity.WARNING
            ),
            payload=receipt.model_dump(mode="json"),
        )
