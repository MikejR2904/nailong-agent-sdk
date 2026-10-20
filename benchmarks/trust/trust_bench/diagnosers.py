# Copyright (c) 2026 David Michael Indraputra

"""Diagnosers: each names the step of a run that a failure traces to, or abstains."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cases import Case
from .schema import Event, Kind, StepRef, SystemRecords
from .wire import normalize_path

if TYPE_CHECKING:
    from .systems.nailong import SdkRun


@dataclass(frozen=True)
class Symptom:
    kind: str
    path: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    method: str
    steps: tuple[StepRef, ...] = ()
    evaluations: int = 0
    llm_calls: int = 0
    note: str = ""


@dataclass
class DiagnosisInput:
    case: Case
    records: SystemRecords
    symptom: Symptom
    sdk_run: SdkRun | None = None


Diagnoser = Callable[[DiagnosisInput], Diagnosis]


def _calls(records: SystemRecords) -> dict[str | None, Event]:
    return {e.call_id: e for e in records.events if e.kind is Kind.TOOL_CALL}


def _step(call: Event | None) -> StepRef | None:
    if call is None or call.agent_id is None or call.model_index is None:
        return None
    return (call.agent_id, call.model_index)


def _arguments(event: Event | None) -> dict[str, object]:
    if event is None or event.content is None:
        return {}
    value = json.loads(event.content)
    return dict(value.get("arguments", {})) if isinstance(value, dict) else {}


def _failed_results(records: SystemRecords) -> list[Event]:
    return [
        e
        for e in records.events
        if e.kind is Kind.TOOL_RESULT and e.status is not None and e.status != "succeeded"
    ]


def _steps_of(records: SystemRecords) -> list[StepRef]:
    found = {
        (e.agent_id, e.model_index)
        for e in records.events
        if e.kind is Kind.MODEL_CALL and e.agent_id is not None and e.model_index is not None
    }
    return sorted(found, key=lambda item: (item[0], item[1]))


def first_error(inp: DiagnosisInput) -> Diagnosis:
    failed = _failed_results(inp.records)
    step = _step(_calls(inp.records).get(failed[0].call_id)) if failed else None
    return Diagnosis("first-error", (step,) if step else ())


def last_error(inp: DiagnosisInput) -> Diagnosis:
    failed = _failed_results(inp.records)
    step = _step(_calls(inp.records).get(failed[-1].call_id)) if failed else None
    return Diagnosis("last-error", (step,) if step else ())


def _writes(records: SystemRecords) -> list[Event]:
    return [e for e in records.events if e.kind is Kind.WRITE]


def first_write(inp: DiagnosisInput) -> Diagnosis:
    writes = _writes(inp.records)
    step = _step(_calls(inp.records).get(writes[0].call_id)) if writes else None
    return Diagnosis("first-write", (step,) if step else ())


def last_write(inp: DiagnosisInput) -> Diagnosis:
    writes = _writes(inp.records)
    step = _step(_calls(inp.records).get(writes[-1].call_id)) if writes else None
    return Diagnosis("last-write", (step,) if step else ())


def first_step(inp: DiagnosisInput) -> Diagnosis:
    steps = _steps_of(inp.records)
    return Diagnosis("first-step", tuple(steps[:1]))


def last_step(inp: DiagnosisInput) -> Diagnosis:
    steps = _steps_of(inp.records)
    return Diagnosis("last-step", tuple(steps[-1:]))


def random_step(inp: DiagnosisInput) -> Diagnosis:
    steps = _steps_of(inp.records)
    if not steps:
        return Diagnosis("random-step")
    chosen = random.Random(f"{inp.case.case_id}-random").choice(steps)
    return Diagnosis("random-step", (chosen,))


def _ranks(case: Case) -> dict[str, int]:
    nodes = {node.label: node for node in case.nodes()}
    ranks: dict[str, int] = {}

    def depth(label: str) -> int:
        if label not in ranks:
            node = nodes.get(label)
            ranks[label] = (
                0 if node is None else max((depth(d) + 1 for d in node.dependencies), default=0)
            )
        return ranks[label]

    for label in nodes:
        depth(label)
    return ranks


def ledger_scan(inp: DiagnosisInput) -> Diagnosis:
    records, case = inp.records, inp.case
    calls = _calls(records)
    ranks = _ranks(case)
    candidates: list[tuple[int, int, int, StepRef]] = []
    unevaluated: list[tuple[int, int, int]] = []
    evaluations = 0
    for event in records.events:
        call = calls.get(event.call_id)
        step = _step(call)
        if step is None:
            continue
        rank = ranks.get(step[0], 0)
        if event.kind is Kind.TOOL_RESULT:
            if event.content is None:
                unevaluated.append((rank, step[1], 0))
                continue
            evaluations += 1
            if case.is_defective_result(
                str(event.name), _arguments(call), json.loads(event.content)
            ):
                candidates.append((rank, step[1], 0, step))
        elif event.kind is Kind.WRITE:
            blob = records.fetch_blob(str(event.digest))
            if blob is None:
                unevaluated.append((rank, step[1], 1))
                continue
            evaluations += 1
            if case.is_defective(normalize_path(str(event.path)), blob):
                candidates.append((rank, step[1], 1, step))
    note = f"{len(unevaluated)} events could not be evaluated" if unevaluated else ""
    if not candidates:
        return Diagnosis("ledger-scan+oracle", (), evaluations, note=note)
    first = min(candidates)
    if any(key < first[:3] for key in unevaluated):
        note += "; some precede the first defect, so its origin is ambiguous"
        return Diagnosis("ledger-scan+oracle", (), evaluations, note=note)
    return Diagnosis("ledger-scan+oracle", (first[3],), evaluations, note=note)


def _decision_steps(attribution: object, labels: dict[str, str], agent: str) -> list[StepRef]:
    steps: list[StepRef] = []
    graph = getattr(attribution, "graph", None)
    refs = []
    if graph is not None:
        for root in graph.roots:
            label = labels.get(str(root.agent_run_id), agent)
            refs.append((label, root.agent_terminal_decision))
    else:
        refs.append((agent, getattr(attribution, "first_error_decision", None)))
        refs.append((agent, getattr(attribution, "terminal_decision", None)))
    for label, ref in refs:
        if ref is not None and ref.relation != "unlinked" and ref.iteration is not None:
            step = (label, ref.iteration - 1)
            if step not in steps:
                steps.append(step)
    return steps


def _result_candidates(
    inp: DiagnosisInput, runs: dict[str, str]
) -> tuple[list[tuple[int, int, int, StepRef]], int]:
    from nailong_agent_sdk.developer_tools.attribute_result import attribute_result_defect
    from nailong_agent_sdk.memory.context_projection import FileToolResultJournal

    run = inp.sdk_run
    assert run is not None
    ranks = _ranks(inp.case)
    journal = FileToolResultJournal(run.run_root, read_only=True)
    found: list[tuple[int, int, int, StepRef]] = []
    evaluations = 0
    for run_id, label in runs.items():
        if not journal.handles_of(run_id):
            continue
        outcome = attribute_result_defect(
            run.run_root, run_id, inp.case.is_defective_result, check="the case's result oracle"
        )
        evaluations += outcome.evaluations
        first = outcome.first_defective
        if first is not None and first.decision.iteration is not None:
            step = (label, first.decision.iteration - 1)
            found.append((ranks.get(label, 0), step[1], 0, step))
    return found, evaluations


def _sdk_shipped(inp: DiagnosisInput, scope: str) -> Diagnosis:
    from nailong_agent_sdk.developer_tools.attribute_defect import attribute_defect
    from nailong_agent_sdk.developer_tools.attribute_failure import attribute_failure
    from nailong_agent_sdk.tools.artifacts import ArtifactStore

    method = f"sdk-shipped:{scope}"
    run = inp.sdk_run
    if run is None:
        return Diagnosis(method, note="not an SDK run")
    agent = inp.case.agent_label
    labels = {
        rid: node.label for rid, node in zip(run.agent_run_ids, inp.case.nodes(), strict=False)
    }
    attribution = attribute_failure(run.run_root, run.run_id)
    steps = _decision_steps(attribution, labels, agent)
    evaluations = 0
    if not steps and inp.symptom.kind == "artifact-defective" and inp.symptom.path:
        paths = [inp.symptom.path]
        if scope != "deliverable":
            paths = list(ArtifactStore(run.workspace, read_only=True).paths())
        introductions = []
        for path in paths:

            def defective(content: bytes, version: object, path: str = path) -> bool:
                return inp.case.is_defective(path, content)

            found = attribute_defect(run.workspace, path, defective)
            evaluations += found.evaluations
            writer = found.introduced_by
            if writer is not None and writer.iteration is not None:
                introductions.append(
                    (writer.write_sequence, writer.agent_identity or agent, writer.iteration)
                )
        if scope == "results+artifacts":
            ranks = _ranks(inp.case)
            candidates, spent = _result_candidates(inp, labels or {run.run_id: agent})
            evaluations += spent
            candidates += [
                (ranks.get(label, 0), iteration - 1, 1, (label, iteration - 1))
                for _, label, iteration in introductions
            ]
            if candidates:
                steps.append(min(candidates)[3])
        elif introductions:
            _, label, iteration = min(introductions)
            steps.append((label, iteration - 1))
    return Diagnosis(method, tuple(steps), evaluations, note=attribution.summary[:160])


def sdk_shipped_deliverable(inp: DiagnosisInput) -> Diagnosis:
    return _sdk_shipped(inp, "deliverable")


def sdk_shipped_all(inp: DiagnosisInput) -> Diagnosis:
    return _sdk_shipped(inp, "all-artifacts")


def sdk_shipped_results(inp: DiagnosisInput) -> Diagnosis:
    return _sdk_shipped(inp, "results+artifacts")


TRIVIAL: dict[str, Diagnoser] = {
    "first-error": first_error,
    "last-error": last_error,
    "first-write": first_write,
    "last-write": last_write,
    "first-step": first_step,
    "last-step": last_step,
    "random-step": random_step,
}
