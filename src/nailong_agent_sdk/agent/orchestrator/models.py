# Copyright (c) 2026 David Michael Indraputra

"""User-owned orchestration policy, request, and persisted-decision contracts.

The design follows the framework's planning-stage instruction to bind a
read-only source snapshot, a selected skill bundle, and a tool subset before the
agentic workflow starts (systems design, p. 41), and implements the framework's
deterministic complexity route between single- and multi-agent workflows (p. 42).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ...foundations.contracts import ModelBinding, SkillContext, StrictModel
from ...integrations.jev.architecture import JevArchitectureAdvice
from ...state.elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
from ...state.orchestration_models import ComplexityRoutingRules, GapMetadata, WorkflowArchitecture
from ...state.planning import ModelTier, Plan
from ...state.shared_state import SharedSubstrateSnapshot
from ...tools.policy import CapabilityGrant


class OrchestrationStatus(StrEnum):
    PREPARED = "prepared"
    AWAITING_PLAN_APPROVAL = "awaiting-plan-approval"
    APPROVED = "approved"
    DISPATCHED = "dispatched"
    EXECUTED = "executed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class UserModelSelection(StrictModel):
    """A named, user-approved provider/model binding usable for listed tiers."""

    model_key: str = Field(min_length=1)
    binding: ModelBinding
    allowed_tiers: list[ModelTier] = Field(min_length=1)

    @model_validator(mode="after")
    def tiers_are_unique(self) -> UserModelSelection:
        if len(self.allowed_tiers) != len(set(self.allowed_tiers)):
            raise ValueError("A model selection may list each model tier only once.")
        return self


class AgentExecutionProfile(StrictModel):
    """One user-defined worker envelope for a stage and role.

    It names only identifiers.  Executable models, tool callbacks, credentials,
    and verification gates stay in the embedding host and are supplied through
    ``GraphAgentBindingFactory`` after approval.
    """

    profile_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    role: str = Field(min_length=1)
    allowed_skill_ids: list[str] = Field(default_factory=list)
    required_skill_ids: list[str] = Field(default_factory=list)
    allowed_model_keys: list[str] = Field(min_length=1)
    allowed_tool_names: list[str] = Field(default_factory=list)
    capability_grant: CapabilityGrant
    max_instances: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def profile_authority_is_consistent(self) -> AgentExecutionProfile:
        if self.capability_grant.role != self.role:
            raise ValueError("Agent profile role must equal its capability grant role.")
        if len(self.allowed_skill_ids) != len(set(self.allowed_skill_ids)):
            raise ValueError("Agent profile allowed_skill_ids must be unique.")
        if len(self.required_skill_ids) != len(set(self.required_skill_ids)):
            raise ValueError("Agent profile required_skill_ids must be unique.")
        if not set(self.required_skill_ids).issubset(self.allowed_skill_ids):
            raise ValueError("Agent profile required skills must be permitted skills.")
        if len(self.allowed_model_keys) != len(set(self.allowed_model_keys)):
            raise ValueError("Agent profile allowed_model_keys must be unique.")
        if len(self.allowed_tool_names) != len(set(self.allowed_tool_names)):
            raise ValueError("Agent profile allowed_tool_names must be unique.")
        return self


class OrchestrationPolicy(StrictModel):
    """User-owned boundary for skills, models, tools, permissions, and scale."""

    policy_id: str = Field(min_length=1)
    routing_rules: ComplexityRoutingRules
    skills: list[SkillContext] = Field(default_factory=list)
    models: list[UserModelSelection] = Field(min_length=1)
    profiles: list[AgentExecutionProfile] = Field(min_length=1)
    max_total_agents: int = Field(default=8, ge=1)
    max_parallel_agents: int = Field(default=4, ge=1)
    max_repair_attempts: int = Field(default=1, ge=0)
    multi_agent_enabled: bool = True
    max_elastic_depth: int = Field(default=1, ge=0, le=MAX_ELASTIC_DEPTH_LIMIT)
    max_elastic_nodes: int = Field(default=2, ge=0, le=MAX_ELASTIC_NODES_LIMIT)

    @model_validator(mode="after")
    def configuration_ids_are_consistent(self) -> OrchestrationPolicy:
        skill_ids = [skill.id for skill in self.skills]
        model_ids = [model.model_key for model in self.models]
        profile_ids = [profile.profile_id for profile in self.profiles]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("Orchestration skill IDs must be unique.")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Orchestration model keys must be unique.")
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("Orchestration profile IDs must be unique.")
        known_skills = set(skill_ids)
        known_models = set(model_ids)
        for profile in self.profiles:
            if not set(profile.allowed_skill_ids).issubset(known_skills):
                raise ValueError(f'Profile "{profile.profile_id}" names unknown skills.')
            if not set(profile.allowed_model_keys).issubset(known_models):
                raise ValueError(f'Profile "{profile.profile_id}" names unknown models.')
        if self.max_parallel_agents > self.max_total_agents:
            raise ValueError("max_parallel_agents cannot exceed max_total_agents.")
        return self


class OrchestrationRequest(StrictModel):
    """A proposed plan and its explicitly selected runtime envelope."""

    request_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    snapshot: SharedSubstrateSnapshot
    gap_metadata: GapMetadata
    plan: Plan
    selected_skill_ids: list[str] = Field(default_factory=list)
    profile_id_by_task_id: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def selected_skill_ids_are_unique(self) -> OrchestrationRequest:
        if len(self.selected_skill_ids) != len(set(self.selected_skill_ids)):
            raise ValueError("Selected skill IDs must be unique.")
        task_ids = {task.task_id for task in self.plan.tasks}
        unknown_task_ids = set(self.profile_id_by_task_id) - task_ids
        if unknown_task_ids:
            raise ValueError(
                f"Profile overrides reference unknown plan tasks: {sorted(unknown_task_ids)}"
            )
        return self


class WorkerAssignment(StrictModel):
    """One auditable authorization-limited worker assignment in an execution plan."""

    node_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    agent_identity: str = Field(min_length=1)
    model_key: str = Field(min_length=1)
    skill_ids: list[str] = Field(default_factory=list)
    allowed_tool_names: list[str] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)


class OrchestrationRecord(StrictModel):
    """Persisted non-secret orchestration decision and dispatch record."""

    schema_version: str = "agent-sdk-orchestration-v1"
    orchestration_id: str = Field(min_length=1)
    request: OrchestrationRequest
    policy_id: str = Field(min_length=1)
    deterministic_architecture: WorkflowArchitecture
    architecture: WorkflowArchitecture
    routing_advice: JevArchitectureAdvice | None = None
    execution_plan: Plan
    assignments: list[WorkerAssignment] = Field(min_length=1)
    status: OrchestrationStatus = OrchestrationStatus.PREPARED
    controller_id: str | None = None
    graph_run_id: str | None = None
