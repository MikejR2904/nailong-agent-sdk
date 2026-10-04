# Copyright (c) 2026 David Michael Indraputra

"""Reproducible benchmark harnesses for deterministic SDK algorithms.

A benchmark case is an explicit frozen PCKP input, not a synthetic workload
claim. The runner executes exact and greedy solvers against identical inputs and
records both result certificates and local elapsed time.
"""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter_ns

from pydantic import Field

from .atomic_io import replace_atomic, unique_temporary_path
from .contracts import StrictModel
from .optimization import ExactPckpSolver, GreedyPckpBaseline, PckpProblem, PckpSolution


class PckpBenchmarkCase(StrictModel):
    case_id: str = Field(min_length=1)
    problem: PckpProblem
    description: str = Field(min_length=1)


class PckpBenchmarkResult(StrictModel):
    case_id: str
    problem_hash: str
    exact: PckpSolution
    greedy: PckpSolution
    exact_elapsed_ns: int = Field(ge=0)
    greedy_elapsed_ns: int = Field(ge=0)
    retained_utility_gap: int


class PckpBenchmarkReport(StrictModel):
    schema_version: str = "pckp-benchmark-v1"
    results: list[PckpBenchmarkResult]


def load_pckp_cases(path: Path) -> list[PckpBenchmarkCase]:
    """Load version-controlled frozen benchmark cases from JSON."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("PCKP benchmark case file must be a JSON list.")
    return [PckpBenchmarkCase.model_validate(item) for item in payload]


def run_pckp_benchmark(cases: list[PckpBenchmarkCase]) -> PckpBenchmarkReport:
    """Run both algorithms on the same ordered immutable cases."""

    exact_solver = ExactPckpSolver()
    greedy_solver = GreedyPckpBaseline()
    results: list[PckpBenchmarkResult] = []
    for case in sorted(cases, key=lambda item: item.case_id):
        exact_start = perf_counter_ns()
        exact = exact_solver.solve(case.problem)
        exact_elapsed_ns = perf_counter_ns() - exact_start
        greedy_start = perf_counter_ns()
        greedy = greedy_solver.solve(case.problem)
        greedy_elapsed_ns = perf_counter_ns() - greedy_start
        results.append(
            PckpBenchmarkResult(
                case_id=case.case_id,
                problem_hash=exact.problem_hash,
                exact=exact,
                greedy=greedy,
                exact_elapsed_ns=exact_elapsed_ns,
                greedy_elapsed_ns=greedy_elapsed_ns,
                retained_utility_gap=exact.utility - greedy.utility,
            )
        )
    return PckpBenchmarkReport(results=results)


def write_pckp_benchmark_report(report: PckpBenchmarkReport, path: Path) -> None:
    """Write canonical JSON for later comparison and data-analysis reporting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_temporary_path(path)
    temporary.write_text(
        json.dumps(report.model_dump(mode="json"), sort_keys=True, indent=2), encoding="utf-8"
    )
    replace_atomic(temporary, path)
