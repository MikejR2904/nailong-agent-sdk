# Copyright (c) 2026 David Michael Indraputra

"""Build the questions for Jev from runs of the harness, offline: nothing here calls a model."""

from __future__ import annotations

import tempfile
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..authority.policy import CLASSES, POLICY, describe
from ..authority.tickets import generate as generate_tickets
from ..cases import Case, FaultSpec
from ..checks import quiet_level
from ..evaluate import FAMILIES, symptom_for
from ..metrics.twins import build_twin
from ..schema import SystemRecords
from ..splits import Split, families_for, operators_for
from ..verify_jobs import verify_operators
from . import projection, specs

DIAGNOSIS, ACCEPTANCE, ALLOCATION = "diagnosis", "acceptance", "allocation"
TASKS = (DIAGNOSIS, ACCEPTANCE, ALLOCATION)
ARMS = ("sdk", "langgraph")
CLASS_TEXT = dict(POLICY["classes"])


def _fault_dict(fault: FaultSpec | None) -> dict[str, Any] | None:
    if fault is None:
        return None
    return {
        "class": fault.fault_class,
        "operator": fault.operator_id,
        "target": fault.target,
        "label": fault.label,
    }


def _pick(case: Case, operators: frozenset[str], operator_id: str) -> FaultSpec:
    matches = [f for f in case.faults(operators) if f.operator_id == operator_id]
    if not matches:
        raise ValueError(f'{case.case_id} has no fault "{operator_id}".')
    return matches[0]


def _records(
    arm: str, case: Case, fault: FaultSpec | None, base: Path
) -> tuple[Any, SystemRecords]:
    if arm == "sdk":
        from ..systems.nailong import run_sdk
        from ..systems.nailong_records import sdk_records

        run = run_sdk(case, fault, base)
        return run, sdk_records(run)
    from ..systems.langgraph_arm import run_langgraph
    from ..systems.langgraph_records import langgraph_records

    run = run_langgraph(case, fault, base)
    return run, langgraph_records(run)


def _item(
    identifier: str,
    task: str,
    state: dict[str, Any],
    meta: dict[str, Any],
    candidates: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "task": task,
        "state": state,
        "candidates": [list(pair) for pair in candidates or []],
        "meta": meta,
    }


def spec_of(item: dict[str, Any]) -> Any:
    """The question an input is put under, rebuilt from the frozen wording and its options."""
    if item["task"] == DIAGNOSIS:
        return specs.diagnosis_spec([(identifier, text) for identifier, text in item["candidates"]])
    if item["task"] == ACCEPTANCE:
        return specs.acceptance_spec()
    return specs.allocation_spec(CLASS_TEXT)


def diagnosis_items(
    family: str, seed: int, arm: str, operator_id: str, operators: frozenset[str]
) -> list[dict[str, Any]]:
    warnings.simplefilter("ignore")
    case = FAMILIES[family](seed)
    fault = _pick(case, operators, operator_id)
    with tempfile.TemporaryDirectory(prefix="trustbench_judge_") as directory:
        run, records = _records(arm, case, fault, Path(directory))
        symptom = symptom_for(case, run.status, run.workspace)
        if symptom.kind == "none":
            return []
        truth = case.decisive_step(fault)
        views = [("original", records, None)]
        if truth is not None:
            twin = build_twin(records, truth, case.case_id)
            if twin is not None:
                views.append(("twin", twin.records, list(twin.decoy)))
    items = []
    for variant, view, decoy in views:
        state, candidates = projection.diagnosis_input(case, view, symptom)
        meta = {
            "family": family,
            "case_id": case.case_id,
            "arm": arm,
            "fault": _fault_dict(fault),
            "symptom": symptom.kind,
            "truth_step": None if truth is None else list(truth),
            "variant": variant,
            "decoy": decoy,
            "candidates": [identifier for identifier, _ in candidates],
        }
        identifier = f"{DIAGNOSIS}/{case.case_id}/{arm}/{fault.operator_id}/{variant}"
        items.append(_item(identifier, DIAGNOSIS, state, meta, candidates))
    return items


def acceptance_items(
    family: str, seed: int, operator_id: str | None, operators: frozenset[str]
) -> list[dict[str, Any]]:
    warnings.simplefilter("ignore")
    case = FAMILIES[family](seed)
    fault = None if operator_id is None else _pick(case, operators, operator_id)
    with tempfile.TemporaryDirectory(prefix="trustbench_judge_") as directory:
        run, _records_ = _records("langgraph", case, fault, Path(directory))
        target = run.workspace / case.deliverable
        if not target.is_file():
            return []
        level = quiet_level(case, run.workspace, case.deliverable, target.read_bytes())
        state = projection.acceptance_input(case, run.workspace)
    label = "clean" if fault is None else fault.operator_id
    meta = {
        "family": family,
        "case_id": case.case_id,
        "fault": _fault_dict(fault),
        "quiet": level,
    }
    return [_item(f"{ACCEPTANCE}/{case.case_id}/{label}", ACCEPTANCE, state, meta)]


def allocation_items(seed: int) -> list[dict[str, Any]]:
    case = generate_tickets(seed)
    text = describe()
    items = []
    for ticket in case.tickets:
        state = projection.allocation_input(text, ticket.view)
        meta = {
            "case_id": case.case_id,
            "ticket": ticket.id,
            "label": ticket.label.cls,
            "classes": list(CLASSES),
        }
        items.append(_item(f"{ALLOCATION}/{case.case_id}/{ticket.id}", ALLOCATION, state, meta))
    return items


def prepare_jobs(
    task: str, split: Split, families: Iterable[str], arms: Iterable[str], seeds: Iterable[int]
) -> list[tuple[Any, ...]]:
    """The jobs that make the questions of one task; each yields one or two questions."""
    if task not in TASKS:
        raise ValueError(f'unknown judge task "{task}"; known: {", ".join(TASKS)}.')
    seeds = list(seeds)
    if task == ALLOCATION:
        return [(ALLOCATION, seed) for seed in seeds]
    allowed = families_for(split, frozenset(FAMILIES))
    jobs: list[tuple[Any, ...]] = []
    for family in families:
        if family not in allowed:
            raise ValueError(
                f'family "{family}" is not available in the {split.value} split; '
                f"available: {sorted(allowed)}."
            )
        for seed in seeds:
            case = FAMILIES[family](seed)
            if task == ACCEPTANCE:
                operators = verify_operators(split)
                ids = [
                    None,
                    *(f.operator_id for f in case.faults(operators) if f.fault_class != "F8"),
                ]
                jobs.extend((ACCEPTANCE, family, seed, op, operators) for op in ids)
                continue
            operators = operators_for(split)
            for arm in arms:
                if arm not in ARMS:
                    raise ValueError(f'unknown arm "{arm}"; known: {", ".join(ARMS)}.')
                jobs.extend(
                    (DIAGNOSIS, family, seed, arm, f.operator_id, operators)
                    for f in case.faults(operators)
                )
    return jobs


def run_prepare_job(job: tuple[Any, ...]) -> list[dict[str, Any]]:
    kind = job[0]
    if kind == ALLOCATION:
        return allocation_items(job[1])
    if kind == ACCEPTANCE:
        return acceptance_items(job[1], job[2], job[3], job[4])
    return diagnosis_items(job[1], job[2], job[3], job[4], job[5])
