# Copyright (c) 2026 David Michael Indraputra

"""Controller phase, gap-routing, and record contracts for the deterministic controller."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from .elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
from .planning import Plan, PlanValidationReport
from .shared_state import SharedSubstrateSnapshot


class ControllerPhase(StrEnum):
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting-plan-approval"
    DISPATCH_READY = "dispatch-ready"
    EXECUTING = "executing"
    REPAIR_REQUIRED = "repair-required"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkflowArchitecture(StrEnum):
    SINGLE_AGENT = "single-agent"
    MULTI_AGENT = "multi-agent"


class GapMetadata(StrictModel):
    categories_touched: list[str] = Field(default_factory=list)
    blast_radius: int = Field(default=0, ge=0)
    gap_types: list[str] = Field(default_factory=list)


class ComplexityRoutingRules(StrictModel):
    multi_agent_min_categories: int = Field(ge=1)
    multi_agent_min_blast_radius: int = Field(ge=1)
    multi_agent_gap_types: list[str] = Field(default_factory=list)


class SkillToolProfile(StrictModel):
    stage: str = Field(min_length=1)
    skill_ids: list[str] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    source_snapshot_id: str = Field(min_length=1)
    source_read_only: bool = True


class ControllerEvent(StrictModel):
    sequence: int = Field(ge=1)
    phase: ControllerPhase
    type: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ControllerRecord(StrictModel):
    controller_id: str = Field(min_length=1)
    project_state_id: str = Field(min_length=1)
    phase: ControllerPhase
    architecture: WorkflowArchitecture
    profile: SkillToolProfile
    snapshot: SharedSubstrateSnapshot
    repair_attempts: int = Field(default=0, ge=0)
    max_repair_attempts: int = Field(ge=0)
    elastic_depth_ceiling: int = Field(
        default=MAX_ELASTIC_DEPTH_LIMIT, ge=0, le=MAX_ELASTIC_DEPTH_LIMIT
    )
    elastic_nodes_ceiling: int = Field(
        default=MAX_ELASTIC_NODES_LIMIT, ge=0, le=MAX_ELASTIC_NODES_LIMIT
    )
    plan: Plan | None = None
    plan_validation: PlanValidationReport | None = None
    plan_approved: bool | None = None
    run_id: str | None = None
    escalation_reason: str | None = None
    events: list[ControllerEvent] = Field(default_factory=list)
    events_entry_count: int = Field(default=0, ge=0)
    events_integrity_hash: str | None = None


class ReconcileAction(StrictModel):
    store: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    change: str = Field(min_length=1)


class ReconcileReport(StrictModel):
    subject: str = Field(min_length=1)
    actions: list[ReconcileAction] = Field(default_factory=list)


class ComplexityRouter:
    """Route through configured deterministic thresholds, never an LLM guess."""

    def __init__(self, rules: ComplexityRoutingRules) -> None:
        self._rules = rules

    def route(self, metadata: GapMetadata) -> WorkflowArchitecture:
        if len(set(metadata.categories_touched)) >= self._rules.multi_agent_min_categories:
            return WorkflowArchitecture.MULTI_AGENT
        if metadata.blast_radius >= self._rules.multi_agent_min_blast_radius:
            return WorkflowArchitecture.MULTI_AGENT
        if set(metadata.gap_types) & set(self._rules.multi_agent_gap_types):
            return WorkflowArchitecture.MULTI_AGENT
        return WorkflowArchitecture.SINGLE_AGENT
