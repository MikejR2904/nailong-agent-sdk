# Copyright (c) 2026 David Michael Indraputra

"""Typed planning contracts and deterministic DAG validation.

The validator implements the framework's PlanTask, TaskSignalUse, and
DependencyProof rules (systems-design framework, updated PDF, pp. 58-59).
It contains no model calls and never infers an unproven dependency.
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from itertools import combinations
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.dependency_graph import deterministic_cycles
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
        if len(value) != len(set(value)):
            raise ValueError("dependencies must be unique")
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
            raise ValueError("dependency proof endpoints must be distinct")
        if len(self.shared_signal_ids) != len(set(self.shared_signal_ids)):
            raise ValueError("dependency proof signal IDs must be unique")
        return self


class Plan(StrictModel):
    plan_id: str = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)
    dependency_proofs: list[DependencyProof] = Field(default_factory=list)
    max_elastic_depth: int = Field(default=1, ge=0)
    max_elastic_nodes: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def task_ids_are_unique(self) -> Plan:
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("plan task IDs must be unique")
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


class PlanValidator:
    """Recompute task edges from exact signal-use evidence.

    The planner may propose a DAG, but it cannot authoritatively choose its
    dependencies. This validator derives the expected edge union and requires
    the proposal and cited proofs to match it exactly.
    """

    def validate(self, plan: Plan) -> PlanValidationReport:
        errors: list[PlanValidationError] = []
        tasks = {task.task_id: task for task in plan.tasks}
        derived: dict[tuple[str, str, DependencyRule], set[str]] = defaultdict(set)

        for task in plan.tasks:
            unknown = sorted(set(task.dependencies) - set(tasks))
            if unknown:
                errors.append(
                    PlanValidationError(
                        code="UNKNOWN_TASK_DEPENDENCY",
                        message=f'Task "{task.task_id}" depends on unknown tasks.',
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
            undeclared = sorted({use.signal_id for use in task.signal_uses} - task.signal_ids())
            if undeclared:
                errors.append(
                    PlanValidationError(
                        code="SIGNAL_NOT_IN_LOCKED_INTERFACE",
                        message=(
                            f'Task "{task.task_id}" cites signals absent from its locked interface.'
                        ),
                        task_ids=[task.task_id],
                        signal_ids=undeclared,
                    )
                )

        for left, right in combinations(sorted(plan.tasks, key=lambda item: item.task_id), 2):
            for signal_id in sorted(left.signal_ids() & right.signal_ids()):
                self._derive_signal_edge(left, right, signal_id, derived, errors)

        expected_dependencies: dict[str, set[str]] = {task.task_id: set() for task in plan.tasks}
        for (parent, child, _rule), _signals in derived.items():
            expected_dependencies[child].add(parent)

        for task in plan.tasks:
            actual = set(task.dependencies)
            expected = expected_dependencies[task.task_id]
            if actual != expected:
                errors.append(
                    PlanValidationError(
                        code="DEPENDENCY_SET_MISMATCH",
                        message=(
                            f'Task "{task.task_id}" dependencies do not equal '
                            "the recomputed edge set."
                        ),
                        task_ids=[task.task_id, *sorted(actual ^ expected)],
                    )
                )

        self._validate_proofs(plan.dependency_proofs, derived, errors)
        self._validate_cycles(expected_dependencies, errors)

        return PlanValidationReport(
            valid=not errors,
            errors=errors,
            recomputed_dependencies={
                task_id: sorted(dependencies)
                for task_id, dependencies in sorted(expected_dependencies.items())
            },
        )

    def assert_valid(self, plan: Plan) -> PlanValidationReport:
        report = self.validate(plan)
        if not report.valid:
            rendered = "; ".join(f"{error.code}: {error.message}" for error in report.errors)
            raise ValueError(f"Plan validation failed: {rendered}")
        return report

    def _derive_signal_edge(
        self,
        left: PlanTask,
        right: PlanTask,
        signal_id: str,
        derived: dict[tuple[str, str, DependencyRule], set[str]],
        errors: list[PlanValidationError],
    ) -> None:
        left_uses = [use for use in left.signal_uses if use.signal_id == signal_id]
        right_uses = [use for use in right.signal_uses if use.signal_id == signal_id]
        if not left_uses or not right_uses:
            errors.append(
                PlanValidationError(
                    code="MISSING_SIGNAL_CITATION",
                    message="A shared locked-interface signal lacks a cited task role.",
                    task_ids=[left.task_id, right.task_id],
                    signal_ids=[signal_id],
                )
            )
            return

        left_produces = any(
            use.role in {SignalRole.DEFINE, SignalRole.PRODUCE} for use in left_uses
        )
        right_produces = any(
            use.role in {SignalRole.DEFINE, SignalRole.PRODUCE} for use in right_uses
        )
        left_consumes = any(use.role is SignalRole.CONSUME for use in left_uses)
        right_consumes = any(use.role is SignalRole.CONSUME for use in right_uses)

        if left_produces and right_produces:
            errors.append(
                PlanValidationError(
                    code="SIGNAL_OWNERSHIP_AMBIGUITY",
                    message="Two tasks claim to define or produce the same locked signal.",
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
            errors.append(
                PlanValidationError(
                    code="MISSING_SIGNAL_CONSUMER",
                    message="A declared signal producer has no corresponding consumer citation.",
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
        derived: dict[tuple[str, str, DependencyRule], set[str]],
        errors: list[PlanValidationError],
    ) -> None:
        provided: dict[tuple[str, str, DependencyRule], set[str]] = defaultdict(set)
        for proof in proofs:
            key = (proof.parent_task_id, proof.child_task_id, proof.applied_rule)
            provided[key].update(proof.shared_signal_ids)
        mismatched = sorted(
            (key for key in set(derived) | set(provided) if derived.get(key) != provided.get(key)),
            key=lambda key: (key[0], key[1], key[2].value),
        )
        for key in mismatched:
            parent, child, rule = key
            expected_signals = sorted(derived.get(key, set()))
            actual_signals = sorted(provided.get(key, set()))
            edge = f'"{parent}" -> "{child}" ({rule.value})'
            if key not in provided:
                detail = f"no dependency proof was supplied for the derived edge {edge}"
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
                message="The recomputed task graph contains a dependency cycle.",
                task_ids=cycle,
            )
            for cycle in cycles
        )
