# Copyright (c) 2026 David Michael Indraputra

"""Debuggability scorers: top-1 and top-k attribution, abstention and unsupported answers."""

from __future__ import annotations

from dataclasses import dataclass

from ..diagnosers import Diagnosis
from ..schema import StepRef


@dataclass(frozen=True)
class AttributionOutcome:
    has_truth: bool
    top1: bool
    top3: bool
    reciprocal_rank: float
    agent_ok: bool
    abstained: bool
    unsupported: bool


def score_diagnosis(truth: StepRef | None, diagnosis: Diagnosis) -> AttributionOutcome:
    named = diagnosis.steps
    abstained = not named
    if truth is None:
        return AttributionOutcome(False, False, False, 0.0, False, abstained, not abstained)
    rank = named.index(truth) + 1 if truth in named else 0
    return AttributionOutcome(
        has_truth=True,
        top1=bool(named) and named[0] == truth,
        top3=truth in named[:3],
        reciprocal_rank=1 / rank if rank else 0.0,
        agent_ok=bool(named) and named[0][0] == truth[0],
        abstained=abstained,
        unsupported=False,
    )
