# Copyright (c) 2026 David Michael Indraputra

"""Sufficiency verdict rates (overclaim, underclaim, sufficient-claim) as DEMM-Bench has them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SufficiencyRates:
    overclaim: float
    underclaim: float
    sufficient_claim: float


def sufficiency_rates(
    claimed_sufficient: Sequence[bool], truly_sufficient: Sequence[bool]
) -> SufficiencyRates:
    if len(claimed_sufficient) != len(truly_sufficient):
        raise ValueError(
            f"verdicts and truths must pair up, got {len(claimed_sufficient)} and "
            f"{len(truly_sufficient)}."
        )
    total = len(claimed_sufficient)
    if total == 0:
        raise ValueError("sufficiency_rates needs at least one verdict.")
    false_sufficient = sum(1 for c, t in zip(claimed_sufficient, truly_sufficient) if c and not t)
    truly = sum(1 for t in truly_sufficient if t)
    false_insufficient = sum(1 for c, t in zip(claimed_sufficient, truly_sufficient) if t and not c)
    return SufficiencyRates(
        overclaim=false_sufficient / total,
        underclaim=false_insufficient / truly if truly else 0.0,
        sufficient_claim=sum(claimed_sufficient) / total,
    )
