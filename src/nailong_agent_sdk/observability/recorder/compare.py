# Copyright (c) 2026 David Michael Indraputra

"""Compares a run's local ledgers with the recorder's copy."""

from __future__ import annotations

from ..audit_log import AuditTranscriptStore
from ..run_checkpoints import CheckpointSigner
from ..telemetry_store import TelemetryStore
from ..transparency import (
    STORES,
    TreeHeadVerdict,
    run_hashes,
    tree_head_name,
    verify_tree_heads,
)
from .models import RecorderComparison, RecorderSource, RecorderVerdict


def compare_with_recorder(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    source: RecorderSource,
    verifier: CheckpointSigner,
) -> dict[str, RecorderComparison]:
    local = run_hashes(telemetry, audit, run_id)
    return {
        store: _compare_store(run_id, store, local[store], source, verifier) for store in STORES
    }


def _compare_store(
    run_id: str,
    store: str,
    local: list[str],
    source: RecorderSource,
    verifier: CheckpointSigner,
) -> RecorderComparison:
    recorded = source.entries(run_id, store, include_records=False)
    held = len(recorded)
    if not recorded:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.NO_RECORD,
            recorded=0,
            local=len(local),
            detail=f'The recorder holds no {store} record of run "{run_id}".',
        )
    heads = verify_tree_heads(
        [item.hash for item in recorded],
        source.head_sink(),
        tree_head_name(run_id, store),
        verifier,
    )
    if heads.verdict not in {TreeHeadVerdict.MATCHES, TreeHeadVerdict.ADVANCED}:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.RECORDER_UNVERIFIED,
            recorded=held,
            local=len(local),
            detail=f"The recorder's own signed heads do not vouch for its copy: {heads.detail}",
        )
    shared = min(held, len(local))
    differing = [index for index in range(shared) if recorded[index].hash != local[index]]
    if differing:
        first = recorded[differing[0]].sequence
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.DIVERGED,
            recorded=held,
            local=len(local),
            first_difference=first,
            differing=len(differing),
            detail=f"{len(differing)} of the first {shared} local {store} records differ from "
            f"the recorder's copy, the first at sequence {first}: the ledger was edited, "
            "had a record removed from the middle, or was resealed after the records were sent.",
        )
    if len(local) < held:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.TRUNCATED,
            recorded=held,
            local=len(local),
            first_difference=len(local) + 1,
            removed_tail=held - len(local),
            detail=f"The local {store} ledger holds {len(local)} records but the recorder "
            f"received {held}: the newest {held - len(local)} were removed.",
        )
    if len(local) > held:
        return RecorderComparison(
            store=store,
            verdict=RecorderVerdict.ADVANCED,
            recorded=held,
            local=len(local),
            detail=f"The local {store} ledger extends the recorder's {held} records by "
            f"{len(local) - held} that it has not received.",
        )
    return RecorderComparison(
        store=store,
        verdict=RecorderVerdict.MATCHES,
        recorded=held,
        local=len(local),
        detail=f"The local {store} ledger and the recorder's copy agree on all {held} records.",
    )
