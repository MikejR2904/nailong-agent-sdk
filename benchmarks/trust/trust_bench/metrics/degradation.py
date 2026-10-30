# Copyright (c) 2026 David Michael Indraputra

"""Degrade a system's own records and see whether artifact resolution abstains or guesses."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import replace

from ..schema import ArtifactVersion, Event, Kind, RunTruth, SystemRecords
from .traceability import score_resolution


def _with(
    records: SystemRecords,
    events: list[Event],
    versions: dict[str, list[ArtifactVersion]] | None = None,
) -> SystemRecords:
    return SystemRecords(
        records.system,
        events,
        records.versions if versions is None else versions,
        records.fetch_blob,
        records.notes,
    )


def drop_events(rate: float) -> Callable[[SystemRecords, str], SystemRecords]:
    def degrade(records: SystemRecords, seed: str) -> SystemRecords:
        generator = random.Random(seed)
        ledger = {Kind.MODEL_CALL, Kind.TOOL_CALL, Kind.TOOL_RESULT}
        kept = [e for e in records.events if e.kind not in ledger or generator.random() >= rate]
        return _with(records, kept)

    return degrade


def strip_writer_links(records: SystemRecords, seed: str) -> SystemRecords:
    versions = {
        path: [replace(v, call_id=None) for v in history]
        for path, history in records.versions.items()
    }
    return _with(records, list(records.events), versions)


def strip_actors(records: SystemRecords, seed: str) -> SystemRecords:
    versions = {
        path: [replace(v, agent_id=None) for v in history]
        for path, history in records.versions.items()
    }
    events = [replace(e, agent_id=None) for e in records.events]
    return _with(records, events, versions)


def cut_payloads(records: SystemRecords, seed: str) -> SystemRecords:
    events = [
        replace(e, content=None, digest=None, truncated=True)
        if e.kind in {Kind.TOOL_CALL, Kind.TOOL_RESULT, Kind.MODEL_CALL}
        else e
        for e in records.events
    ]
    return _with(records, events)


CONDITIONS: dict[str, Callable[[SystemRecords, str], SystemRecords]] = {
    "drop-5%": drop_events(0.05),
    "drop-20%": drop_events(0.20),
    "no-writer-link": strip_writer_links,
    "no-actor": strip_actors,
    "no-payloads": cut_payloads,
}


def degradation(truth: RunTruth, records: SystemRecords) -> dict[str, dict[str, dict[str, int]]]:
    out: dict[str, dict[str, dict[str, int]]] = {}
    for name, degrade in CONDITIONS.items():
        degraded = degrade(records, f"{truth.case_id}-{name}")
        entry: dict[str, dict[str, int]] = {}
        for label, lenient in (("strict", False), ("lenient", True)):
            counts = {"correct": 0, "wrong": 0, "abstain": 0}
            for outcome in score_resolution(truth, degraded, lenient=lenient):
                counts[outcome.outcome] += 1
            entry[label] = counts
        out[name] = entry
    return out
