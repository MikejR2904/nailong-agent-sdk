# Copyright (c) 2026 David Michael Indraputra

"""Small standalone helpers for constructing telemetry timestamps and metric observations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from .telemetry_models import ChainBreak, MetricAvailability, MetricObservation


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def first_chain_break(
    records: Iterable[Any],
    *,
    noun: str,
    previous_attribute: str,
    expected_hash: Callable[[Any], str],
) -> ChainBreak | None:
    previous: str | None = None
    last_sequence = 0
    iterator = iter(records)
    while True:
        try:
            record = next(iterator)
        except StopIteration:
            return None
        except ValueError as error:
            return ChainBreak(
                sequence=last_sequence + 1,
                kind="unreadable-entry",
                message=(
                    f"The {noun} after sequence {last_sequence} cannot be read "
                    f"({type(error).__name__}): it was edited, truncated or corrupted."
                ),
            )
        recomputed = expected_hash(record)
        if record.integrity_hash != recomputed:
            return ChainBreak(
                sequence=record.sequence,
                kind="content-hash-mismatch",
                message=(
                    f"The {noun} at sequence {record.sequence} stores integrity hash "
                    f"{_short(record.integrity_hash)} but its content hashes to "
                    f"{_short(recomputed)}: it was altered after it was written."
                ),
            )
        linked = getattr(record, previous_attribute)
        if linked != previous:
            return ChainBreak(
                sequence=record.sequence,
                kind="previous-hash-mismatch",
                message=(
                    f"The {noun} at sequence {record.sequence} links to previous hash "
                    f"{_short(linked)} but the preceding {noun} has hash {_short(previous)}: "
                    f"an earlier {noun} was removed, reordered or replaced."
                ),
            )
        previous = record.integrity_hash
        last_sequence = record.sequence


def _short(value: str | None) -> str:
    return "none" if value is None else value[:12]


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
