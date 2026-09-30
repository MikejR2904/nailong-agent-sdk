# Copyright (c) 2026 David Michael Indraputra

"""PCKP problem, item, and solution contracts.

The general model is a precedence-constrained knapsack problem (PCKP): selecting
an item requires selecting every immediate prerequisite.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..contracts import StrictModel


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
        if len(self.prerequisites) != len(set(self.prerequisites)):
            raise ValueError("PCKP prerequisites must be unique")
        if self.item_id in self.prerequisites:
            raise ValueError("A PCKP item cannot require itself")
        return self


class PckpProblem(StrictModel):
    """A closed-set PCKP instance with integer costs and utilities."""

    token_budget: int = Field(ge=0)
    items: list[PckpItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_problem_graph(self) -> PckpProblem:
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("PCKP item IDs must be unique")
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
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            raise ValueError(f'PCKP dependencies contain a cycle at "{item_id}".')
        if item_id in visited:
            return
        visiting.add(item_id)
        for prerequisite in sorted(dependencies[item_id]):
            visit(prerequisite)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in sorted(dependencies):
        visit(item_id)
