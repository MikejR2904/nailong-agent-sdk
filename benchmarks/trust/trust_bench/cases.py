# Copyright (c) 2026 David Michael Indraputra

"""A benchmark case: seeded inputs, a deterministic solver policy, oracles and fault catalog."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .nodes import NodeSpec
from .schema import StepRef
from .wire import Policy


@dataclass(frozen=True)
class FaultSpec:
    fault_class: str
    operator: str
    target: str | None = None
    variant: str | None = None
    row: int | None = None

    @property
    def operator_id(self) -> str:
        return f"{self.operator}:{self.variant}" if self.variant else self.operator

    @property
    def label(self) -> str:
        suffix = f"@{self.target}" if self.target else ""
        return f"{self.fault_class}/{self.operator_id}{suffix}"


class Case(ABC):
    family: str
    case_id: str
    agent_label: str
    task_text: str
    tools: tuple[str, ...]
    output_paths: tuple[str, ...]

    @abstractmethod
    def materialize(self, workspace: Path, fault: FaultSpec | None) -> None:
        """Write the input files, applying an environment fault when the fault is one."""

    @abstractmethod
    def policy(self, fault: FaultSpec | None) -> Policy:
        """The deterministic solver, deviating at the fault's step when the fault is a decision."""

    @abstractmethod
    def faults(self, operators: frozenset[str]) -> list[FaultSpec]:
        """Every fault this case supports whose operator id is in ``operators``."""

    @abstractmethod
    def decisive_step(self, fault: FaultSpec) -> StepRef | None:
        """The (agent, model-call index) a correct diagnosis names; None if no single step does."""

    @abstractmethod
    def sources(self) -> dict[str, frozenset[str]]:
        """For each delivered artifact, the input records its content is derived from."""

    @abstractmethod
    def is_defective(self, path: str, content: bytes) -> bool:
        """The held-out oracle: whether this content of this artifact path violates the spec."""

    @property
    def deliverable(self) -> str:
        return self.output_paths[-1]

    def generic_ok(self, path: str, content: bytes) -> bool:
        """The format check: the content parses as the kind of file its path promises."""
        raise NotImplementedError(f"{self.family} defines no generic check.")

    def visible_ok(self, workspace: Path, path: str, content: bytes) -> bool:
        """The check a host can run from the workspace alone: the content agrees with its data."""
        raise NotImplementedError(f"{self.family} defines no visible check.")

    def reference_text(self) -> str:
        """The held-out reference of the deliverable, as the text a file would hold."""
        raise NotImplementedError(f"{self.family} defines no reference text.")

    def form_faults(self) -> list[FaultSpec]:
        return [FaultSpec("F4", "decision_wrong_form", None, "truncate")]

    def is_defective_result(
        self, tool: str, arguments: dict[str, object], envelope: dict[str, object]
    ) -> bool:
        """The held-out check on one tool result: whether it deviates from the true data."""
        return False

    def max_iterations(self, fault: FaultSpec | None) -> int:
        return self.solver_steps + 2

    def nodes(self) -> tuple[NodeSpec, ...]:
        return (NodeSpec(self.agent_label, self.tools, self.output_paths, (), self.task_text),)

    def node_policy(self, label: str, fault: FaultSpec | None) -> Policy:
        return self.policy(fault)

    def node_max_iterations(self, label: str, fault: FaultSpec | None) -> int:
        return self.max_iterations(fault)

    solver_steps: int = 0
