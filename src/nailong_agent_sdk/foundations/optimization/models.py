# Copyright (c) 2026 David Michael Indraputra

"""PCKP problem, item, and solution contracts.

The general model is a precedence-constrained knapsack problem (PCKP): selecting
an item requires selecting every immediate prerequisite.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..contracts import StrictModel
from ..dependency_graph import deterministic_cycles
from ..identifiers import require_unique


class PckpStatus(StrEnum):
    """Whether an exact PCKP solution was proven or only bounded."""

    OPTIMAL = "optimal"
    BEST_EFFORT = "best-effort"
    INFEASIBLE_MANDATORY = "infeasible-mandatory"


class PckpItem(StrictModel):
    """One additive-utility item with direct prerequisite identifiers."""

    item_id: str = Field(min_length=1)
    token_cost: int = Field(ge=0)
    utility: int = Field(ge=0)
    prerequisites: list[str] = Field(default_factory=list)
    mandatory: bool = False

    @model_validator(mode="after")
    def prerequisites_are_unique_and_external(self) -> PckpItem:
        require_unique(self.prerequisites, f'PCKP item "{self.item_id}" prerequisites')
        if self.item_id in self.prerequisites:
            raise ValueError(f'PCKP item "{self.item_id}" cannot require itself')
        return self


class PckpProblem(StrictModel):
    """A closed-set PCKP instance with integer costs and utilities."""

    token_budget: int = Field(ge=0)
    items: list[PckpItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_problem_graph(self) -> PckpProblem:
        item_ids = [item.item_id for item in self.items]
        require_unique(item_ids, "PCKP item IDs")
        known = set(item_ids)
        for item in self.items:
            unknown = set(item.prerequisites) - known
            if unknown:
                raise ValueError(
                    f'PCKP item "{item.item_id}" has unknown prerequisites: {sorted(unknown)}'
                )
        _assert_acyclic({item.item_id: set(item.prerequisites) for item in self.items})
        return self


class PckpSolution(StrictModel):
    """A reproducible solver certificate for one PCKP instance."""

    status: PckpStatus
    selected_item_ids: list[str] = Field(default_factory=list)
    mandatory_item_ids: list[str] = Field(default_factory=list)
    token_cost: int = Field(ge=0)
    utility: int = Field(ge=0)
    upper_bound: str = Field(min_length=1)
    optimality_gap: str = Field(min_length=1)
    branch_nodes: int = Field(ge=0)
    solver: str = Field(min_length=1)
    problem_hash: str = Field(min_length=1)
    diagnostics: list[str] = Field(default_factory=list)


def _assert_acyclic(dependencies: dict[str, set[str]]) -> None:
    edges = [
        (item_id, prerequisite)
        for item_id, values in dependencies.items()
        for prerequisite in values
    ]
    cycles = deterministic_cycles(dependencies, edges)
    if cycles:
        raise ValueError(f'PCKP dependencies contain a cycle at "{cycles[0][0]}".')
