# Copyright (c) 2026 David Michael Indraputra

"""Score what Jev answered like any other method, and merge it into the rows of the report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from ..diagnosers import Diagnosis
from ..metrics.attribution import score_diagnosis
from ..schema import StepRef
from .projection import parse_step
from .specs import NONE_CHOICE

METHOD = "jev-judge"
ACCEPT_AT = 0.5
UNAVAILABLE_CLASS = "H"
UNAVAILABLE_RULE = "model-unavailable"


def _answer(record: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if record is None or not record.get("succeeded"):
        return None
    return record.get("answer")


def ranked(answer: Mapping[str, Any] | None) -> tuple[list[StepRef], bool]:
    """The decisions in order of probability, and whether the answer named one at all."""
    if answer is None or answer.get("choice") == NONE_CHOICE:
        return [], False
    probabilities = answer["probabilities"]
    order = sorted(
        (k for k in probabilities if k != NONE_CHOICE), key=lambda k: (-probabilities[k], k)
    )
    first = answer["choice"]
    order = [first, *(k for k in order if k != first)]
    return [parse_step(k) for k in order], True


def _entry(
    truth: StepRef | None, steps: list[StepRef], record: Mapping[str, Any] | None
) -> dict[str, Any]:
    outcome = score_diagnosis(truth, Diagnosis(METHOD, tuple(steps), 0, llm_calls=1))
    answer = _answer(record)
    return {
        "steps": [list(step) for step in steps],
        "evaluations": 0,
        **asdict(outcome),
        "unavailable": answer is None,
        "confidence": None if answer is None else answer.get("confidence"),
        "cached": bool(record and record.get("cached")),
        "input_tokens": None if record is None else record.get("input_tokens"),
    }


def diagnosis_rows(
    items: Sequence[Mapping[str, Any]], answers: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for item in items:
        meta = item["meta"]
        key = (meta["case_id"], meta["arm"], meta["fault"]["label"])
        groups[key][meta["variant"]] = item
    rows = []
    for variants in groups.values():
        original = variants["original"]
        meta = original["meta"]
        truth = (
            None if meta["truth_step"] is None else (meta["truth_step"][0], meta["truth_step"][1])
        )
        steps, _named = ranked(_answer(answers.get(original["id"])))
        row: dict[str, Any] = {
            "kind": "judge-diagnosis",
            "case_id": meta["case_id"],
            "family": meta["family"],
            "arm": meta["arm"],
            "fault": meta["fault"],
            "diagnoses": {METHOD: _entry(truth, steps, answers.get(original["id"]))},
            "twins": {},
            "twin_decoy": None,
        }
        twin = variants.get("twin")
        if twin is not None and truth is not None:
            record = answers.get(twin["id"])
            top, named = ranked(_answer(record))
            first = top[0] if named else None
            decoy = twin["meta"]["decoy"]
            row["twins"][METHOD] = {
                "steps": [] if first is None else [list(first)],
                "named": first is not None,
                "names_truth": first == truth,
                "names_decoy": first is not None and decoy is not None and list(first) == decoy,
                "unavailable": _answer(record) is None,
            }
            row["twin_decoy"] = decoy
        rows.append(row)
    return rows


def acceptance_rows(
    items: Sequence[Mapping[str, Any]], answers: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        meta = item["meta"]
        answer = _answer(answers.get(item["id"]))
        noul = None if answer is None else float(answer["noul"])
        rows.append(
            {
                "kind": "judge-acceptance",
                "case_id": meta["case_id"],
                "family": meta["family"],
                "fault": None if meta["fault"] is None else meta["fault"]["label"],
                "quiet": meta["quiet"],
                "noul": noul,
                "accepted": None if noul is None else noul >= ACCEPT_AT,
            }
        )
    return rows


def allocation_table(
    items: Sequence[Mapping[str, Any]], answers: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, dict[str, str]], int]:
    table: dict[str, dict[str, str]] = {}
    unavailable = 0
    for item in items:
        meta = item["meta"]
        answer = _answer(answers.get(item["id"]))
        key = f"{meta['case_id']}/{meta['ticket']}"
        if answer is None:
            unavailable += 1
            table[key] = {"class": UNAVAILABLE_CLASS, "rule": UNAVAILABLE_RULE}
        else:
            table[key] = {"class": answer["choice"], "rule": "model"}
    return table, unavailable


def merge_diagnosis(
    results: Sequence[Mapping[str, Any]], judged: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Add the judge's diagnoses and twin answers to the run rows they belong to."""
    by_key = {(r["case_id"], r["arm"], r["fault"]["label"]): r for r in judged}
    merged = []
    for row in results:
        copy = dict(row)
        fault = row["fault"]
        match = None if fault is None else by_key.get((row["case_id"], row["arm"], fault["label"]))
        if match is not None:
            copy["diagnoses"] = {**row["diagnoses"], **match["diagnoses"]}
            copy["twins"] = {**row.get("twins", {}), **match["twins"]}
        merged.append(copy)
    return merged


def merge_acceptance(
    verify: Sequence[Mapping[str, Any]], judged: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_key = {(r["case_id"], r["fault"]): r for r in judged}
    merged = []
    for row in verify:
        copy = dict(row)
        label = None if row["fault"] is None else row["fault"]["label"]
        match = by_key.get((row["case_id"], label))
        if match is not None:
            copy["judge"] = {"accepted": match["accepted"], "noul": match["noul"]}
        merged.append(copy)
    return merged
