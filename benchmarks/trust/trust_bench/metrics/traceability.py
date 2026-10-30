# Copyright (c) 2026 David Michael Indraputra

"""Traceability scorers: record completeness and precision, recoverability, chain resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..schema import (
    ArtifactTruth,
    Event,
    Kind,
    RunTruth,
    SystemRecords,
    digest_bytes,
    digest_of,
)
from ..wire import WRITE_TOOLS, normalize_path


@dataclass
class ClassCounts:
    total: int = 0
    present: int = 0
    phantom: int = 0
    comparable: int = 0
    agree: int = 0
    recoverable: int = 0

    def add(self, other: ClassCounts) -> None:
        for name in ("total", "present", "phantom", "comparable", "agree", "recoverable"):
            setattr(self, name, getattr(self, name) + getattr(other, name))


def _arguments(event: Event) -> dict[str, object]:
    if event.content is None:
        return {}
    value = json.loads(event.content)
    return dict(value.get("arguments", {})) if isinstance(value, dict) else {}


def _recoverable(truth: Event, found: Event, records: SystemRecords) -> bool:
    if truth.kind is Kind.WRITE:
        blob = records.fetch_blob(str(truth.digest))
        return blob is not None and digest_bytes(blob) == truth.digest
    if found.content is None:
        return False
    return digest_of(json.loads(found.content)) == truth.digest


def score_events(truth: RunTruth, records: SystemRecords) -> dict[Kind, ClassCounts]:
    counts = {kind: ClassCounts() for kind in Kind}
    system = {event.key: event for event in records.events}
    truth_keys = {event.key for event in truth.events}
    for expected in truth.events:
        bucket = counts[expected.kind]
        bucket.total += 1
        found = system.get(expected.key)
        if found is None:
            continue
        bucket.present += 1
        if found.digest is not None and expected.digest is not None:
            bucket.comparable += 1
            bucket.agree += int(found.digest == expected.digest)
        bucket.recoverable += int(_recoverable(expected, found, records))
    for key, event in system.items():
        if key not in truth_keys:
            counts[event.kind].phantom += 1
    return counts


@dataclass(frozen=True)
class Resolution:
    call_id: str | None
    agent_id: str | None
    model_index: int | None
    sources: frozenset[str] | None
    abstained: bool
    reason: str | None = None


def _abstain(reason: str) -> Resolution:
    return Resolution(None, None, None, None, True, reason)


MAX_SOURCE_DEPTH = 4


def _read_sources(records: SystemRecords, write_call_id: str, depth: int = 0) -> frozenset[str]:
    calls = {e.call_id: e for e in records.events if e.kind is Kind.TOOL_CALL}
    results = {e.call_id: e for e in records.events if e.kind is Kind.TOOL_RESULT}
    writer = calls.get(write_call_id)
    agent = None if writer is None else writer.agent_id
    found: set[str] = set()
    for event in records.events:
        if event.kind is not Kind.TOOL_CALL:
            continue
        if event.call_id == write_call_id:
            break
        if agent is not None and event.agent_id != agent:
            continue
        outcome = results.get(event.call_id)
        if event.name != "read_file" or outcome is None or outcome.status != "succeeded":
            continue
        path = _arguments(event).get("path")
        if not isinstance(path, str):
            continue
        normalized = normalize_path(path)
        produced = records.versions.get(normalized)
        if produced and produced[-1].call_id and depth < MAX_SOURCE_DEPTH:
            found |= _read_sources(records, str(produced[-1].call_id), depth + 1)
        else:
            found.add(PurePosixPath(normalized).stem)
    return frozenset(found)


def _guess_writer(records: SystemRecords, path: str) -> Event | None:
    writers = [e for e in records.events if e.kind is Kind.TOOL_CALL and e.name in WRITE_TOOLS]
    matching = [e for e in writers if normalize_path(str(_arguments(e).get("path", ""))) == path]
    candidates = matching or writers
    return candidates[-1] if candidates else None


def resolve(records: SystemRecords, path: str, *, lenient: bool = False) -> Resolution:
    versions = records.versions.get(path)
    if not versions:
        return _abstain("the records hold no version of the path")
    last = versions[-1]
    calls = {e.call_id: e for e in records.events if e.kind is Kind.TOOL_CALL}
    call_id, agent_id = last.call_id, last.agent_id
    if call_id is None:
        if not lenient:
            return _abstain("the write record names no tool call")
        guess = _guess_writer(records, path)
        if guess is None:
            return _abstain("no write call in the ledger to guess from")
        call_id = guess.call_id
    call = calls.get(call_id)
    if call is None and not lenient:
        return _abstain(f'the tool call "{call_id}" named by the write is not in the ledger')
    agent_id = agent_id or (call.agent_id if call else None)
    model_index = call.model_index if call else None
    if agent_id is None:
        return _abstain("no actor is recorded for the write")
    if model_index is None and not lenient:
        return _abstain(f'the model turn that issued "{call_id}" is not in the ledger')
    return Resolution(call_id, agent_id, model_index, _read_sources(records, str(call_id)), False)


@dataclass
class ArtifactOutcome:
    path: str
    outcome: str
    decision_ok: bool
    actor_ok: bool
    source_precision: float
    source_recall: float
    reason: str | None = None


def _source_scores(found: frozenset[str] | None, truth: frozenset[str]) -> tuple[float, float]:
    if not found:
        return 0.0, 0.0
    hit = len(found & truth)
    return hit / len(found), hit / len(truth) if truth else 1.0


def score_resolution(
    truth: RunTruth, records: SystemRecords, *, lenient: bool = False
) -> list[ArtifactOutcome]:
    outcomes = []
    for path, expected in sorted(truth.artifacts.items()):
        resolved = resolve(records, path, lenient=lenient)
        if resolved.abstained:
            outcomes.append(
                ArtifactOutcome(path, "abstain", False, False, 0.0, 0.0, resolved.reason)
            )
            continue
        decision_ok = _decision_matches(resolved, expected)
        actor_ok = resolved.agent_id == expected.agent_id
        precision, recall = _source_scores(resolved.sources, expected.sources)
        label = "correct" if decision_ok and actor_ok else "wrong"
        outcomes.append(ArtifactOutcome(path, label, decision_ok, actor_ok, precision, recall))
    return outcomes


def _decision_matches(resolved: Resolution, expected: ArtifactTruth) -> bool:
    if resolved.call_id != expected.call_id:
        return False
    return resolved.model_index is None or resolved.model_index == expected.model_index


@dataclass
class RunTraceability:
    case_id: str
    counts: dict[Kind, ClassCounts] = field(default_factory=dict)
    outcomes: list[ArtifactOutcome] = field(default_factory=list)

    @property
    def calls_complete(self) -> bool:
        return all(
            self.counts[kind].present == self.counts[kind].total
            for kind in (Kind.MODEL_CALL, Kind.TOOL_CALL)
        )


def score_run(truth: RunTruth, records: SystemRecords, *, lenient: bool = False) -> RunTraceability:
    return RunTraceability(
        truth.case_id,
        score_events(truth, records),
        score_resolution(truth, records, lenient=lenient),
    )
