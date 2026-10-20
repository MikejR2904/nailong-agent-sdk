# Copyright (c) 2026 David Michael Indraputra

"""Run one case on one arm and score it: traceability on every run, diagnosis on faulty ones."""

from __future__ import annotations

import tempfile
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .cases import Case, FaultSpec
from .diagnosers import (
    TRIVIAL,
    Diagnoser,
    DiagnosisInput,
    Symptom,
    ledger_scan,
    sdk_shipped_all,
    sdk_shipped_deliverable,
    sdk_shipped_results,
)
from .families import data as data_family
from .families import doc as doc_family
from .families import plan as plan_family
from .metrics.attribution import score_diagnosis
from .metrics.degradation import degradation
from .metrics.traceability import score_run
from .metrics.twins import build_twin
from .schema import Kind, SystemRecords

FAMILIES: dict[str, Callable[[int], Case]] = {
    "doc": doc_family.generate,
    "data": data_family.generate,
    "plan": plan_family.generate,
}
ARMS = ("sdk", "langgraph")


def symptom_for(case: Case, status: str, workspace: Path) -> Symptom:
    if status != "completed":
        return Symptom("run-failed")
    target = workspace / case.deliverable
    if target.is_file() and case.is_defective(case.deliverable, target.read_bytes()):
        return Symptom("artifact-defective", case.deliverable)
    return Symptom("none")


def diagnosers_for(arm: str) -> dict[str, Diagnoser]:
    methods: dict[str, Diagnoser] = dict(TRIVIAL)
    methods["ledger-scan+oracle"] = ledger_scan
    if arm == "sdk":
        methods["sdk-shipped:deliverable"] = sdk_shipped_deliverable
        methods["sdk-shipped:all-artifacts"] = sdk_shipped_all
        methods["sdk-shipped:results+artifacts"] = sdk_shipped_results
    return methods


def generic_diagnosers() -> dict[str, Diagnoser]:
    methods: dict[str, Diagnoser] = dict(TRIVIAL)
    methods["ledger-scan+oracle"] = ledger_scan
    return methods


def twin_outcomes(
    case: Case, records: SystemRecords, symptom: Symptom, truth_step: tuple[str, int]
) -> tuple[dict[str, Any], list[Any] | None]:
    twin = build_twin(records, truth_step, case.case_id)
    if twin is None:
        return {}, None
    outcomes: dict[str, Any] = {}
    for name, diagnoser in generic_diagnosers().items():
        result = diagnoser(DiagnosisInput(case, twin.records, symptom, None))
        outcomes[name] = {
            "steps": [list(step) for step in result.steps],
            "named": bool(result.steps),
            "names_truth": truth_step in result.steps,
            "names_decoy": twin.decoy in result.steps,
        }
    return outcomes, list(twin.decoy)


def _trace_dict(case_id: str, truth: Any, records: SystemRecords, lenient: bool) -> dict[str, Any]:
    traced = score_run(truth, records, lenient=lenient)
    return {
        "calls_complete": traced.calls_complete,
        "counts": {kind.value: asdict(counts) for kind, counts in traced.counts.items()},
        "artifacts": [asdict(outcome) for outcome in traced.outcomes],
    }


def evaluate_case(case: Case, fault: FaultSpec | None, arm: str, base: Path) -> dict[str, Any]:
    started = time.perf_counter()
    if arm == "sdk":
        from .systems.nailong import run_sdk
        from .systems.nailong_records import sdk_records

        run = run_sdk(case, fault, base)
        records = sdk_records(run)
        sdk_run = run
    elif arm == "langgraph":
        from .systems.langgraph_arm import run_langgraph
        from .systems.langgraph_records import langgraph_records

        run = run_langgraph(case, fault, base)
        records = langgraph_records(run)
        sdk_run = None
    else:
        raise ValueError(f'unknown arm "{arm}"; known: {", ".join(ARMS)}.')
    native = None
    if fault is None:
        if arm == "sdk":
            from .systems.nailong_tamper import native_failures
        else:
            from .systems.langgraph_tamper import native_failures
        native = native_failures(run)
    symptom = symptom_for(case, run.status, run.workspace)
    truth_step = case.decisive_step(fault) if fault is not None else None
    diagnoses: dict[str, Any] = {}
    twins: dict[str, Any] = {}
    decoy = None
    if fault is not None and symptom.kind != "none":
        if truth_step is not None:
            twins, decoy = twin_outcomes(case, records, symptom, truth_step)
        inp = DiagnosisInput(case, records, symptom, sdk_run)
        for name, diagnoser in diagnosers_for(arm).items():
            result = diagnoser(inp)
            outcome = score_diagnosis(truth_step, result)
            diagnoses[name] = {
                "steps": [list(step) for step in result.steps],
                "evaluations": result.evaluations,
                **{k: v for k, v in asdict(outcome).items()},
            }
    return {
        "case_id": case.case_id,
        "family": case.family,
        "arm": arm,
        "fault": None
        if fault is None
        else {
            "class": fault.fault_class,
            "operator": fault.operator_id,
            "target": fault.target,
            "label": fault.label,
        },
        "status": run.status,
        "symptom": symptom.kind,
        "truth_step": None if truth_step is None else list(truth_step),
        "events": {kind.value: sum(1 for e in records.events if e.kind is kind) for kind in Kind},
        "trace": _trace_dict(case.case_id, run.truth, records, lenient=False),
        "degradation": degradation(run.truth, records) if fault is None else {},
        "diagnoses": diagnoses,
        "native_check": native,
        "twins": twins,
        "twin_decoy": decoy,
        "notes": records.notes,
        "seconds": round(time.perf_counter() - started, 3),
    }


def evaluate_job(job: tuple[str, int, str, str | None, str, frozenset[str]]) -> dict[str, Any]:
    family, seed, arm, operator_id, _split, operators = job
    warnings.simplefilter("ignore")
    case = FAMILIES[family](seed)
    fault = None
    if operator_id is not None:
        matches = [f for f in case.faults(operators) if f.operator_id == operator_id]
        if not matches:
            raise ValueError(f'{case.case_id} has no fault "{operator_id}".')
        fault = matches[0]
    with tempfile.TemporaryDirectory(prefix="trustbench_") as directory:
        return evaluate_case(case, fault, arm, Path(directory))
