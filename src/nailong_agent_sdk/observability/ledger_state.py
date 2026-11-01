# Copyright (c) 2026 David Michael Indraputra

"""What a run's two ledgers hold now: length, head hash and the hash at chosen sequences."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from .audit_log import AuditTranscriptStore
from .telemetry_models import ChainBreak
from .telemetry_store import TelemetryStore


@dataclass(frozen=True)
class ChainState:
    failure: ChainBreak | None
    sequence: int
    head: str | None
    hashes: dict[int, str]


@dataclass(frozen=True)
class RunState:
    telemetry: ChainState
    audit: ChainState


def inspect_run(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    *,
    telemetry_sequences: Collection[int] = (),
    audit_sequences: Collection[int] = (),
) -> RunState:
    return RunState(
        telemetry=telemetry_state(telemetry, run_id, telemetry_sequences),
        audit=audit_state(audit, run_id, audit_sequences),
    )


def telemetry_state(telemetry: TelemetryStore, run_id: str, wanted: Collection[int]) -> ChainState:
    failure = telemetry.chain_break(run_id)
    if failure is not None:
        return ChainState(failure, 0, None, {})
    sequence, head, hashes = 0, None, {}
    for event in telemetry.iter_events(run_id):
        sequence, head = event.sequence, event.integrity_hash
        if event.sequence in wanted:
            hashes[event.sequence] = event.integrity_hash
    return ChainState(None, sequence, head, hashes)


def audit_state(audit: AuditTranscriptStore, run_id: str, wanted: Collection[int]) -> ChainState:
    failure = audit.chain_break(run_id)
    if failure is not None:
        return ChainState(failure, 0, None, {})
    sequence, head, hashes = 0, None, {}
    for entry in audit.iter_entries(run_id):
        sequence, head = entry.sequence, entry.integrity_hash
        if entry.sequence in wanted:
            hashes[entry.sequence] = entry.integrity_hash
    return ChainState(None, sequence, head, hashes)


def short_digest(digest: object) -> str:
    return "no entries" if not isinstance(digest, str) else digest[:12]
