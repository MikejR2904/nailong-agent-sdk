# Copyright (c) 2026 David Michael Indraputra

"""Insufficient-evidence twins: hide the decisive step and a look-alike, then see who guesses."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from ..schema import Event, Kind, StepRef, SystemRecords


@dataclass(frozen=True)
class Twin:
    records: SystemRecords
    decoy: StepRef


def _step_of(event: Event) -> StepRef | None:
    if event.agent_id is None or event.model_index is None:
        return None
    return (event.agent_id, event.model_index)


def _tools_by_step(records: SystemRecords) -> dict[StepRef, tuple[str, ...]]:
    found: dict[StepRef, list[str]] = {}
    for event in records.events:
        step = _step_of(event) if event.kind is Kind.TOOL_CALL else None
        if step is not None:
            found.setdefault(step, []).append(str(event.name))
    return {step: tuple(names) for step, names in found.items()}


def build_twin(records: SystemRecords, truth: StepRef, seed: str) -> Twin | None:
    tools = _tools_by_step(records)
    kind = tools.get(truth)
    if kind is None:
        return None
    decoys = sorted(step for step, names in tools.items() if names == kind and step != truth)
    if not decoys:
        return None
    decoy = random.Random(seed).choice(decoys)
    hidden = {truth, decoy}
    hidden_calls = {
        event.call_id
        for event in records.events
        if event.kind is Kind.TOOL_CALL and _step_of(event) in hidden
    }
    hidden_digests = {
        event.digest
        for event in records.events
        if event.kind is Kind.WRITE and event.call_id in hidden_calls
    }

    def hides(event: Event) -> bool:
        if event.kind in {Kind.MODEL_CALL, Kind.TOOL_CALL}:
            return _step_of(event) in hidden
        return event.kind is Kind.TOOL_RESULT and event.call_id in hidden_calls

    events = [
        replace(event, content=None, digest=None, status=None, truncated=True)
        if hides(event)
        else event
        for event in records.events
    ]

    def fetch_blob(digest: str) -> bytes | None:
        return None if digest in hidden_digests else records.fetch_blob(digest)

    notes = [*records.notes, f"hid steps {sorted(hidden)}"]
    return Twin(SystemRecords(records.system, events, records.versions, fetch_blob, notes), decoy)
