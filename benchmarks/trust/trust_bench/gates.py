# Copyright (c) 2026 David Michael Indraputra

"""Reproduction gates: the harness must reproduce published numbers before anything is compared."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics.sufficiency import sufficiency_rates
from .tamper import LinkOnlyChain, positions, tamper_lines

AFR = "Agent Flight Recorder, arXiv 2609.01931, 'chain' configuration, 10 positions"
DEMM = "DEMM-Bench, arXiv 2606.20634, Tables 4 and 5, 64 cases of which 8 are sufficient"


@dataclass(frozen=True)
class GateResult:
    name: str
    source: str
    passed: bool
    expected: dict[str, float]
    observed: dict[str, float]


def gate_link_only_chain(entries: int = 1_000) -> GateResult:
    chain = LinkOnlyChain(entries)
    grid = positions(entries)
    observed: dict[str, float] = {}
    for label, klass in (
        ("edit", "edit"),
        ("delete", "delete"),
        ("reorder", "reorder"),
        ("fork", "reseal"),
    ):
        detected = 0
        for position in grid:
            tampered = tamper_lines(
                chain.lines, klass, position, LinkOnlyChain.mutate, LinkOnlyChain.reseal
            )
            assert tampered is not None
            detected += int(not LinkOnlyChain.verify(tampered))
        observed[label] = detected / len(grid)
    observed["false_positive"] = float(not LinkOnlyChain.verify(chain.lines))
    expected = {"edit": 0.9, "delete": 0.9, "reorder": 1.0, "fork": 0.0, "false_positive": 0.0}
    return GateResult(
        "link-only-chain tamper pattern", AFR, observed == expected, expected, observed
    )


def gate_sufficiency_arithmetic() -> GateResult:
    truth = [True] * 8 + [False] * 56
    counts = {
        "trace-present": 56,
        "ledger-present": 40,
        "checklist": 8,
        "always-insufficient": 0,
        "always-sufficient": 64,
    }
    observed: dict[str, float] = {}
    for name, claimed in counts.items():
        verdicts = [True] * claimed + [False] * (64 - claimed)
        rates = sufficiency_rates(sorted(verdicts, reverse=True), truth)
        observed[f"{name}:overclaim"] = rates.overclaim
        observed[f"{name}:sufficient_claim"] = rates.sufficient_claim
    expected = {
        "trace-present:overclaim": 0.75,
        "trace-present:sufficient_claim": 0.875,
        "ledger-present:overclaim": 0.5,
        "ledger-present:sufficient_claim": 0.625,
        "checklist:overclaim": 0.0,
        "checklist:sufficient_claim": 0.125,
        "always-insufficient:overclaim": 0.0,
        "always-insufficient:sufficient_claim": 0.0,
        "always-sufficient:overclaim": 0.875,
        "always-sufficient:sufficient_claim": 1.0,
    }
    return GateResult("sufficiency rate arithmetic", DEMM, observed == expected, expected, observed)


def run_gates() -> list[GateResult]:
    return [gate_link_only_chain(), gate_sufficiency_arithmetic()]
