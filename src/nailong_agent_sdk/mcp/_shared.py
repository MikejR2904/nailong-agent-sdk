# Copyright (c) 2026 David Michael Indraputra

"""Shared runtime context and response helpers for every registered MCP tool group."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..agent.orchestrator import Orchestrator
from ..observability.audit_log import AuditTranscriptStore
from ..observability.telemetry_store import TelemetryStore
from ..specifications.gate import Gate1ArtifactStore, SpecificationGate
from ..specifications.git_versioning import GitRepositoryAdapter, SpecificationVersionService
from ..specifications.preprocessing import SpecificationPreprocessor
from ..state.controller_runtime import ControllerRuntime
from ..state.harness_coordinator import HarnessCoordinator
from ..state.planning import PlanValidator
from ..state.project_state_store import FileProjectStateStore


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "location": list(issue["loc"]),
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors()
    ]


@dataclass
class McpContext:
    """Durable services and helpers shared by every tool group registered on the server."""

    run_root: Path
    coordinator: HarnessCoordinator
    telemetry: TelemetryStore
    audit_logs: AuditTranscriptStore
    project_states: FileProjectStateStore
    controller_runtime: ControllerRuntime
    specification_root: Path
    preprocessor: SpecificationPreprocessor
    specification_gate: SpecificationGate
    gate_store: Gate1ArtifactStore
    plan_validator: PlanValidator
    versioning: SpecificationVersionService
    orchestrators: dict[str, Orchestrator] = field(default_factory=dict)

    def repository_for(self, relative_path: str) -> GitRepositoryAdapter:
        candidate = Path(relative_path)
        if candidate.is_absolute() or not relative_path.strip():
            raise ValueError(
                "Git repository paths must be non-empty and relative to the runtime root."
            )
        target = (self.run_root / candidate).resolve()
        try:
            target.relative_to(self.run_root.resolve())
        except ValueError as error:
            raise ValueError("Git repository path escapes the runtime root.") from error
        return GitRepositoryAdapter(target)

    def orchestration_for(self, orchestration_id: str) -> Orchestrator:
        """Recover a policy shell; executable worker bindings remain host-local."""

        if orchestration_id not in self.orchestrators:
            self.orchestrators[orchestration_id] = Orchestrator.resume(
                self.run_root,
                orchestration_id,
                controller_runtime=self.controller_runtime,
                telemetry=self.telemetry,
            )
        return self.orchestrators[orchestration_id]
