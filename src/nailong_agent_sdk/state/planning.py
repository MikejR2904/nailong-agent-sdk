# Copyright (c) 2026 David Michael Indraputra

"""Typed planning contracts and deterministic DAG validation.

The validator implements the framework's PlanTask, TaskSignalUse, and
DependencyProof rules (systems-design framework, updated PDF, pp. 58-59).
It contains no model calls and never infers an unproven dependency.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from enum import StrEnum
from itertools import combinations
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.dependency_graph import deterministic_cycles
from ..foundations.identifiers import require_unique
from .elastic import (
    DEFAULT_MAX_ELASTIC_DEPTH,
    DEFAULT_MAX_ELASTIC_NODES,
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
)
from .shared_state import DiscoveryRoutingRefs


class ModelTier(StrEnum):
    CHEAP = "cheap"
    STANDARD = "standard"
    STRONG = "strong"


class SignalRole(StrEnum):
    DEFINE = "DEFINE"
    PRODUCE = "PRODUCE"
    CONSUME = "CONSUME"
    CONSTRAINT_REFERENCE = "CONSTRAINT_REFERENCE"


class DependencyRule(StrEnum):
    PRODUCER_TO_CONSUMER = "PRODUCER_TO_CONSUMER"
    GENERALITY_FALLBACK = "GENERALITY_FALLBACK"


class TaskSignalUse(StrictModel):
    task_id: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    role: SignalRole
    source_spans: list[str] = Field(min_length=1)
    scope_pointer: str = Field(min_length=1)

    @field_validator("source_spans")
    @classmethod
    def source_spans_are_nonempty(cls, value: list[str]) -> list[str]:
        if any(not span.strip() for span in value):
            raise ValueError("source_spans entries must be non-empty")
        return value


class PlanTask(StrictModel):
    """One bounded work unit copied from the locked specification."""

    task_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    locked_interface: dict[str, Any]
    instructions: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: str = Field(min_length=1)
    model_tier: ModelTier
    signal_uses: list[TaskSignalUse] = Field(default_factory=list)
    generality_rank: int = Field(default=0, ge=0)
    authorized_artifact_ids: list[str] = Field(default_factory=list)
    routing_refs: DiscoveryRoutingRefs = Field(default_factory=DiscoveryRoutingRefs)

    @field_validator("dependencies")
    @classmethod
    def dependencies_are_unique(cls, value: list[str]) -> list[str]:
        require_unique(value, "dependencies")
        return value

    @model_validator(mode="after")
    def signal_uses_belong_to_task(self) -> PlanTask:
        foreign = [signal.task_id for signal in self.signal_uses if signal.task_id != self.task_id]
        if foreign:
            raise ValueError("every signal_uses entry must reference this task_id")
        return self

    def signal_ids(self) -> set[str]:
        """Return exact identifiers from the verbatim locked-interface copy."""

        signals = self.locked_interface.get("signals", [])
        result: set[str] = set()
        for signal in signals:
            if isinstance(signal, str) and signal:
                result.add(signal)
            elif isinstance(signal, dict) and isinstance(signal.get("id"), str) and signal["id"]:
                result.add(signal["id"])
        return result


class DependencyProof(StrictModel):
    parent_task_id: str = Field(min_length=1)
    child_task_id: str = Field(min_length=1)
    shared_signal_ids: list[str] = Field(min_length=1)
    applied_rule: DependencyRule
    source_spans: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def proof_has_distinct_endpoints(self) -> DependencyProof:
        if self.parent_task_id == self.child_task_id:
            raise ValueError(
                f'dependency proof endpoints must be distinct; both are "{self.parent_task_id}"'
            )
        require_unique(self.shared_signal_ids, "dependency proof signal IDs")
        return self


class Plan(StrictModel):
    plan_id: str = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    dependency_proofs: list[DependencyProof] = Field(default_factory=list)
    max_elastic_depth: int = Field(
        default=DEFAULT_MAX_ELASTIC_DEPTH, ge=0, le=MAX_ELASTIC_DEPTH_LIMIT
    )
    max_elastic_nodes: int = Field(
        default=DEFAULT_MAX_ELASTIC_NODES, ge=0, le=MAX_ELASTIC_NODES_LIMIT
    )

    @model_validator(mode="after")
    def task_ids_are_unique(self) -> Plan:
        ids = [task.task_id for task in self.tasks]
        require_unique(ids, "plan task IDs")
        return self


class PlanValidationError(StrictModel):
    code: str
    message: str
    task_ids: list[str] = Field(default_factory=list)
    signal_ids: list[str] = Field(default_factory=list)


class PlanValidationReport(StrictModel):
    valid: bool
    errors: list[PlanValidationError] = Field(default_factory=list)
    recomputed_dependencies: dict[str, list[str]] = Field(default_factory=dict)


_MAX_RENDERED_ERRORS = 10
_PRODUCING_ROLES = frozenset({SignalRole.DEFINE, SignalRole.PRODUCE})

_EdgeKey = tuple[str, str, DependencyRule]


class PlanValidator:
    """Recompute task edges from exact signal-use evidence.

    The planner may propose a DAG, but it cannot authoritatively choose its
    dependencies. Every declared dependency must be derived from shared
    signals and carry a matching proof, and every derived ordering must be
    implied by the declared dependencies, directly or through other tasks.
    """

    def validate(self, plan: Plan) -> PlanValidationReport:
        errors: list[PlanValidationError] = []
        tasks = {task.task_id: task for task in plan.tasks}
        signal_sets = {task.task_id: task.signal_ids() for task in plan.tasks}

        for task in plan.tasks:
            unknown = sorted(set(task.dependencies) - set(tasks))
            if unknown:
                errors.append(
                    PlanValidationError(
                        code="UNKNOWN_TASK_DEPENDENCY",
                        message=f'Task "{task.task_id}" depends on unknown tasks {unknown}.',
                        task_ids=[task.task_id, *unknown],
                    )
                )
            if task.task_id in task.dependencies:
                errors.append(
                    PlanValidationError(
                        code="SELF_DEPENDENCY",
                        message=f'Task "{task.task_id}" may not depend on itself.',
                        task_ids=[task.task_id],
                    )
                )
            undeclared = sorted(
                {use.signal_id for use in task.signal_uses} - signal_sets[task.task_id]
            )
            if undeclared:
                errors.append(
                    PlanValidationError(
                        code="SIGNAL_NOT_IN_LOCKED_INTERFACE",
                        message=(
                            f'Task "{task.task_id}" cites signals {undeclared} absent from its '
                            "locked interface."
                        ),
                        task_ids=[task.task_id],
                        signal_ids=undeclared,
                    )
                )

        derived = self._derive_edges(plan.tasks, signal_sets, errors)
        derived_parents: dict[str, set[str]] = {task.task_id: set() for task in plan.tasks}
        for parent, child, _rule in derived:
            derived_parents[child].add(parent)
        declared = {task.task_id: set(task.dependencies) for task in plan.tasks}
        self._validate_dependency_sets(declared, derived_parents, errors)
        self._validate_proofs(plan.dependency_proofs, derived, declared, errors)
        self._validate_cycles(derived_parents, errors)

        return PlanValidationReport(
            valid=not errors,
            errors=errors,
            recomputed_dependencies={
                task_id: sorted(dependencies)
                for task_id, dependencies in sorted(derived_parents.items())
            },
        )

    def assert_valid(self, plan: Plan) -> PlanValidationReport:
        report = self.validate(plan)
        if not report.valid:
            raise ValueError(f"Plan validation failed: {render_plan_errors(report.errors)}")
        return report

    def _derive_edges(
        self,
        tasks: list[PlanTask],
        signal_sets: Mapping[str, set[str]],
        errors: list[PlanValidationError],
    ) -> dict[_EdgeKey, set[str]]:
        by_id = {task.task_id: task for task in tasks}
        holders: dict[str, list[str]] = defaultdict(list)
        for task_id in sorted(by_id):
            for signal_id in signal_sets[task_id]:
                holders[signal_id].append(task_id)
        roles = _signal_roles(tasks)
        pairs = sorted(
            (left, right, signal_id)
            for signal_id, group in holders.items()
            for left, right in combinations(group, 2)
        )
        derived: dict[_EdgeKey, set[str]] = defaultdict(set)
        for left_id, right_id, signal_id in pairs:
            self._derive_signal_edge(
                by_id[left_id], by_id[right_id], signal_id, roles, derived, errors
            )
        return derived

    @staticmethod
    def _validate_dependency_sets(
        declared: Mapping[str, set[str]],
        derived_parents: Mapping[str, set[str]],
        errors: list[PlanValidationError],
    ) -> None:
        candidates = {
            task_id: derived_parents[task_id] - parents for task_id, parents in declared.items()
        }
        ancestors = _ancestor_bitsets(declared) if any(candidates.values()) else {}
        index = {task_id: position for position, task_id in enumerate(sorted(declared))}
        for task_id, parents in declared.items():
            unjustified = parents - derived_parents[task_id]
            unordered = {
                parent
                for parent in candidates[task_id]
                if ancestors is None or not (ancestors[task_id] >> index[parent]) & 1
            }
            if not unjustified and not unordered:
                continue
            details = []
            if unjustified:
                details.append(
                    f"declares dependencies {sorted(unjustified)} that no shared locked-interface "
                    "signal derives"
                )
            if unordered:
                details.append(
                    f"does not depend, directly or transitively, on {sorted(unordered)}, which "
                    "its shared signals require"
                )
            errors.append(
                PlanValidationError(
                    code="DEPENDENCY_SET_MISMATCH",
                    message=f'Task "{task_id}" {" and ".join(details)}.',
                    task_ids=[task_id, *sorted(unjustified | unordered)],
                )
            )

    def _derive_signal_edge(
        self,
        left: PlanTask,
        right: PlanTask,
        signal_id: str,
        roles: Mapping[tuple[str, str], tuple[bool, bool]],
        derived: dict[_EdgeKey, set[str]],
        errors: list[PlanValidationError],
    ) -> None:
        left_roles = roles.get((left.task_id, signal_id))
        right_roles = roles.get((right.task_id, signal_id))
        if left_roles is None or right_roles is None:
            uncited = [
                task.task_id
                for task, cited in ((left, left_roles), (right, right_roles))
                if cited is None
            ]
            errors.append(
                PlanValidationError(
                    code="MISSING_SIGNAL_CITATION",
                    message=(
                        f'Tasks "{left.task_id}" and "{right.task_id}" share locked-interface '
                        f'signal "{signal_id}" but {uncited} cite no role for it.'
                    ),
                    task_ids=[left.task_id, right.task_id],
                    signal_ids=[signal_id],
                )
            )
            return

        left_produces, left_consumes = left_roles
        right_produces, right_consumes = right_roles

        if left_produces and right_produces:
            errors.append(
                PlanValidationError(
                    code="SIGNAL_OWNERSHIP_AMBIGUITY",
                    message=(
                        f'Tasks "{left.task_id}" and "{right.task_id}" both define or produce '
                        f'locked signal "{signal_id}".'
                    ),
                    task_ids=[left.task_id, right.task_id],
                    signal_ids=[signal_id],
                )
            )
            return

        if left_produces and right_consumes:
            derived[(left.task_id, right.task_id, DependencyRule.PRODUCER_TO_CONSUMER)].add(
                signal_id
            )
            return
        if right_produces and left_consumes:
            derived[(right.task_id, left.task_id, DependencyRule.PRODUCER_TO_CONSUMER)].add(
                signal_id
            )
            return
        if left_produces or right_produces:
            producer, other = (left, right) if left_produces else (right, left)
            errors.append(
                PlanValidationError(
                    code="MISSING_SIGNAL_CONSUMER",
                    message=(
                        f'Task "{producer.task_id}" produces signal "{signal_id}" but '
                        f'"{other.task_id}" shares it without a consumer citation.'
                    ),
                    task_ids=[left.task_id, right.task_id],
                    signal_ids=[signal_id],
                )
            )
            return

        parent, child = self._fallback_order(left, right)
        derived[(parent.task_id, child.task_id, DependencyRule.GENERALITY_FALLBACK)].add(signal_id)

    @staticmethod
    def _fallback_order(left: PlanTask, right: PlanTask) -> tuple[PlanTask, PlanTask]:
        left_key = (left.generality_rank, left.task_id)
        right_key = (right.generality_rank, right.task_id)
        return (left, right) if left_key <= right_key else (right, left)

    @staticmethod
    def _validate_proofs(
        proofs: list[DependencyProof],
        derived: dict[_EdgeKey, set[str]],
        declared: Mapping[str, set[str]],
        errors: list[PlanValidationError],
    ) -> None:
        provided: dict[_EdgeKey, set[str]] = defaultdict(set)
        for proof in proofs:
            key = (proof.parent_task_id, proof.child_task_id, proof.applied_rule)
            provided[key].update(proof.shared_signal_ids)
        declared_edges = {
            (parent, child) for child, parents in declared.items() for parent in parents
        }
        mismatched = sorted(
            (
                key
                for key in set(derived) | set(provided)
                if derived.get(key) != provided.get(key)
                and (key in provided or (key[0], key[1]) in declared_edges)
            ),
            key=lambda key: (key[0], key[1], key[2].value),
        )
        for key in mismatched:
            parent, child, rule = key
            expected_signals = sorted(derived.get(key, set()))
            actual_signals = sorted(provided.get(key, set()))
            edge = f'"{parent}" -> "{child}" ({rule.value})'
            if key not in provided:
                detail = f"no dependency proof was supplied for the declared edge {edge}"
            elif key not in derived:
                detail = (
                    f"the proof for {edge} has no matching edge derived from the locked interfaces"
                )
            else:
                detail = (
                    f"the proof for {edge} cites signals {actual_signals} "
                    f"but the locked interfaces derive {expected_signals}"
                )
            errors.append(
                PlanValidationError(
                    code="DEPENDENCY_PROOF_MISMATCH",
                    message=f"Dependency proofs do not match the derived edges: {detail}.",
                    task_ids=[parent, child],
                    signal_ids=sorted(set(expected_signals) | set(actual_signals)),
                )
            )

    @staticmethod
    def _validate_cycles(
        dependencies: dict[str, set[str]], errors: list[PlanValidationError]
    ) -> None:
        cycles = deterministic_cycles(
            dependencies,
            [
                (task_id, dependency)
                for task_id, task_dependencies in dependencies.items()
                for dependency in task_dependencies
            ],
        )
        errors.extend(
            PlanValidationError(
                code="DEPENDENCY_CYCLE",
                message=f"The recomputed task graph contains the dependency cycle {cycle}.",
                task_ids=cycle,
            )
            for cycle in cycles
        )


def render_plan_errors(errors: list[PlanValidationError]) -> str:
    rendered = [_render_plan_error(error) for error in errors[:_MAX_RENDERED_ERRORS]]
    omitted = len(errors) - len(rendered)
    if omitted > 0:
        rendered.append(f"{omitted} more errors omitted")
    return "; ".join(rendered)


def _render_plan_error(error: PlanValidationError) -> str:
    scope = []
    if error.task_ids:
        scope.append(f"tasks {error.task_ids}")
    if error.signal_ids:
        scope.append(f"signals {error.signal_ids}")
    suffix = f" ({', '.join(scope)})" if scope else ""
    return f"{error.code}: {error.message}{suffix}"


def _signal_roles(tasks: list[PlanTask]) -> dict[tuple[str, str], tuple[bool, bool]]:
    produces: dict[tuple[str, str], bool] = {}
    consumes: dict[tuple[str, str], bool] = {}
    for task in tasks:
        for use in task.signal_uses:
            key = (task.task_id, use.signal_id)
            produces[key] = produces.get(key, False) or use.role in _PRODUCING_ROLES
            consumes[key] = consumes.get(key, False) or use.role is SignalRole.CONSUME
    return {key: (produces[key], consumes[key]) for key in produces}


def _ancestor_bitsets(dependencies: Mapping[str, set[str]]) -> dict[str, int] | None:
    index = {task_id: position for position, task_id in enumerate(sorted(dependencies))}
    children: dict[str, list[str]] = defaultdict(list)
    remaining: dict[str, int] = {}
    for task_id, parents in dependencies.items():
        known = [parent for parent in parents if parent in index]
        remaining[task_id] = len(known)
        for parent in known:
            children[parent].append(task_id)
    ancestors = dict.fromkeys(dependencies, 0)
    ready = deque(sorted(task_id for task_id, count in remaining.items() if count == 0))
    visited = 0
    while ready:
        current = ready.popleft()
        visited += 1
        contribution = ancestors[current] | (1 << index[current])
        for child in children[current]:
            ancestors[child] |= contribution
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    return ancestors if visited == len(dependencies) else None
