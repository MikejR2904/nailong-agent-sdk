# Copyright (c) 2026 David Michael Indraputra

"""Serializable BaseAgent contract and task/result types.

The project framework specifies a common BaseAgent contract with identity,
instructions, typed input/output, narrow tools, model binding, memory,
termination, and an optional deterministic verification gate (systems-design
framework, updated PDF, p. 50–51).
"""

from __future__ import annotations

import functools
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import AgentSdkError, sanitize_failure_details


class StrictModel(BaseModel):
    """Reject unknown public contract fields rather than silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class MemoryScope(StrEnum):
    NONE = "none"
    TASK_SCOPED = "task-scoped"
    CROSS_SESSION = "cross-session"


class EscalationTarget(StrEnum):
    NONE = "none"
    CONTROLLER = "controller"
    HUMAN = "human"


class EpisodeKind(StrEnum):
    EXPLORATORY = "exploratory"
    ACTION = "action"


class ToolConcurrency(StrEnum):
    """Whether a tool may run beside other ready calls in one model batch."""

    SERIAL = "serial"
    PARALLEL_SAFE = "parallel-safe"


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VersionedInstructions(StrictModel):
    version: str = Field(min_length=1)
    text: str = Field(min_length=1)


class FallbackModelBinding(StrictModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelBinding(FallbackModelBinding):
    fallbacks: list[FallbackModelBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def fallback_bindings_are_distinct(self) -> ModelBinding:
        seen = {(self.provider, self.model)}
        for fallback in self.fallbacks:
            key = (fallback.provider, fallback.model)
            if key in seen:
                raise ValueError(
                    "model fallback bindings must be distinct and exclude the primary model"
                )
            seen.add(key)
        return self


class TerminationPolicy(StrictModel):
    max_iterations: int = Field(ge=1)
    status_field: str = Field(min_length=1)
    escalation: EscalationTarget = EscalationTarget.CONTROLLER


class ToolDefinition(StrictModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    episode_kind: EpisodeKind
    concurrency: ToolConcurrency = ToolConcurrency.SERIAL

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_json_schema(schema, "tool input_schema")
        return schema


class AgentDefinition(StrictModel):
    """The data-only BaseAgent definition; runtime dependencies are injected separately."""

    identity: str = Field(min_length=1)
    instructions: VersionedInstructions
    input_schema: dict[str, Any]
    tools: list[ToolDefinition] = Field(default_factory=list)
    model_binding: ModelBinding
    output_schema: dict[str, Any]
    memory_scope: MemoryScope = MemoryScope.TASK_SCOPED
    memory_rationale: str | None = None
    termination_policy: TerminationPolicy
    verification_gate_id: str | None = None

    @field_validator("identity")
    @classmethod
    def identity_is_one_line(cls, identity: str) -> str:
        if "\n" in identity or "\r" in identity:
            raise ValueError("identity must be a single line")
        return identity

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_contract_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_json_schema(schema, "agent schema")
        return schema

    @model_validator(mode="after")
    def validate_definition_invariants(self) -> AgentDefinition:
        tool_names = [tool.name for tool in self.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("tool names must be unique")
        if self.memory_scope is MemoryScope.CROSS_SESSION and not self.memory_rationale:
            raise ValueError("cross-session memory requires memory_rationale")
        return self


class TaskScope(StrictModel):
    label: str = Field(min_length=1)
    boundaries: dict[str, Any] = Field(default_factory=dict)


class SkillContext(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ScopedAgentTask(StrictModel):
    """One task-local payload; never a full project transcript."""

    id: str = Field(min_length=1)
    input: dict[str, Any]
    scope: TaskScope
    locked_interface: Any
    instructions: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    skills: list[SkillContext] = Field(default_factory=list)

    @field_validator("acceptance_criteria")
    @classmethod
    def criteria_are_nonempty(cls, criteria: list[str]) -> list[str]:
        if any(not criterion.strip() for criterion in criteria):
            raise ValueError("acceptance_criteria entries must be non-empty")
        return criteria


class ToolCall(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    consumed_episode_ids: list[str] = Field(default_factory=list)
    depends_on_call_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def dependencies_are_unique_and_external(self) -> ToolCall:
        if len(self.depends_on_call_ids) != len(set(self.depends_on_call_ids)):
            raise ValueError("tool-call dependencies must be unique")
        if self.id in self.depends_on_call_ids:
            raise ValueError("tool call cannot depend on itself")
        return self


class AgentFailure(StrictModel):
    """Bounded, redacted causal data for a terminal or tool-contract failure."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details", mode="before")
    @classmethod
    def details_are_safe(cls, details: dict[str, Any] | None) -> dict[str, Any]:
        return sanitize_failure_details(details)

    @classmethod
    def from_sdk_error(cls, error: AgentSdkError) -> AgentFailure:
        return cls(code=error.code, message=error.message, details=error.details)


class ToolExecutionResult(StrictModel):
    status: Literal["succeeded", "failed", "blocked"]
    output: Any | None = None
    error: str | None = None
    failure: AgentFailure | None = None


class ToolCallTurn(StrictModel):
    type: Literal["tool-call"] = "tool-call"
    call: ToolCall


class ToolBatchTurn(StrictModel):
    """One model turn with an acyclic batch of correlated tool calls."""

    type: Literal["tool-batch"] = "tool-batch"
    calls: list[ToolCall] = Field(min_length=1)

    @model_validator(mode="after")
    def batch_dependencies_are_declared_and_acyclic(self) -> ToolBatchTurn:
        call_ids = [call.id for call in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool batch call IDs must be unique")
        known = set(call_ids)
        for call in self.calls:
            unknown = set(call.depends_on_call_ids) - known
            if unknown:
                raise ValueError(
                    f'tool call "{call.id}" depends on unknown batch call IDs: {sorted(unknown)}'
                )
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {call.id: call for call in self.calls}

        def visit(call_id: str) -> None:
            if call_id in visited:
                return
            if call_id in visiting:
                raise ValueError("tool batch dependencies must be acyclic")
            visiting.add(call_id)
            for dependency_id in by_id[call_id].depends_on_call_ids:
                visit(dependency_id)
            visiting.remove(call_id)
            visited.add(call_id)

        for call_id in call_ids:
            visit(call_id)
        return self


class FinalTurn(StrictModel):
    type: Literal["final"] = "final"
    output: Any


class BlockedTurn(StrictModel):
    type: Literal["blocked"] = "blocked"
    reason: str = Field(min_length=1)


AgentTurn = Annotated[
    ToolCallTurn | ToolBatchTurn | FinalTurn | BlockedTurn,
    Field(discriminator="type"),
]


class RuntimeOptions(StrictModel):
    """Options intentionally limited to deterministic execution until a model is selected."""

    mode: Literal["deterministic"] = "deterministic"
    scripted_turns: list[AgentTurn] = Field(default_factory=list)
    run_deadline_seconds: float | None = Field(default=None, gt=0, le=86_400)
    model_turn_timeout_seconds: float | None = Field(default=None, gt=0, le=86_400)
    tool_call_timeout_seconds: float | None = Field(default=None, gt=0, le=86_400)
    verification_timeout_seconds: float | None = Field(default=None, gt=0, le=86_400)
    context_token_budget: int | None = Field(default=None, ge=256, le=1_000_000)
    episode_token_budget: int | None = Field(default=None, ge=0, le=1_000_000)
    tool_result_preview_chars: int | None = Field(default=None, ge=32, le=100_000)
    project_state_token_budget: int | None = Field(default=None, ge=128, le=1_000_000)


class PromptSection(StrictModel):
    kind: Literal["identity", "instructions", "task", "skills", "tools"]
    value: Any


class AgentPrompt(StrictModel):
    sections: list[PromptSection]


class EpisodeSummary(StrictModel):
    id: str
    kind: EpisodeKind
    summary: str
    created_at: str
    dependency_ids: list[str] = Field(default_factory=list)


class ModelObservation(StrictModel):
    kind: Literal["tool-result", "agent-error"]
    iteration: int = Field(ge=1)
    message: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    episode_id: str | None = None
    result: ProjectedToolResult | None = None


class ToolResultHandle(StrictModel):
    """Opaque journal reference for complete tool evidence outside the model context."""

    handle_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    byte_count: int = Field(ge=0)
    truncated: bool


class ProjectedToolResult(StrictModel):
    """Bounded tool-result representation safe to include in a model context."""

    status: Literal["succeeded", "failed", "blocked"]
    handle: ToolResultHandle
    preview: Any | None = None
    error: str | None = None


class ContextProjectionMetadata(StrictModel):
    estimated_tokens: int = Field(ge=0)
    context_token_budget: int = Field(ge=1)
    episode_token_budget: int = Field(ge=0)
    compacted_episode_ids: list[str] = Field(default_factory=list)
    omitted_observation_count: int = Field(default=0, ge=0)


class AgentLifecycleEvent(StrictModel):
    type: Literal[
        "run-started",
        "context-assembled",
        "state-loaded",
        "state-updated",
        "context-projected",
        "context-compacted",
        "turn-started",
        "tool-batch-requested",
        "tool-batch-completed",
        "tool-requested",
        "tool-completed",
        "provider-tool-results-forwarded",
        "output-rejected",
        "verification-completed",
        "terminated",
        "escalated",
    ]
    task_id: str
    iteration: int = Field(ge=0)
    at: str
    details: dict[str, Any] = Field(default_factory=dict)


class AgentEscalation(StrictModel):
    target: Literal["controller", "human"]
    reason: str


class AgentResult(StrictModel):
    status: AgentRunStatus
    task_id: str
    iterations: int = Field(ge=0)
    output: Any | None = None
    reason: str | None = None
    failure: AgentFailure | None = None
    escalation: AgentEscalation | None = None
    context: AgentPrompt | None = None
    project_state: dict[str, Any] | None = None
    episodes: list[EpisodeSummary] = Field(default_factory=list)
    projection_history: list[ContextProjectionMetadata] = Field(default_factory=list)
    events: list[AgentLifecycleEvent] = Field(default_factory=list)
    profile: dict[str, Any] | None = None


def validate_task_input(definition: AgentDefinition, task: ScopedAgentTask) -> None:
    """Validate only the invocation payload against the agent's declared schema."""

    _validate_instance(definition.input_schema, task.input, "task input")


def validate_tool_arguments(tool: ToolDefinition, arguments: dict[str, Any]) -> None:
    _validate_instance(tool.input_schema, arguments, f'tool "{tool.name}" arguments')


def validate_candidate_output(definition: AgentDefinition, output: Any) -> None:
    _validate_instance(definition.output_schema, output, "candidate output")


def _validate_json_schema(schema: dict[str, Any], label: str) -> None:
    canonical = _canonical_json_schema(schema)
    try:
        if canonical is None:
            Draft202012Validator.check_schema(schema)
        else:
            _check_schema_cached(canonical)
    except SchemaError as error:
        raise ValueError(
            f"{label} is not a valid Draft 2020-12 JSON Schema: {error.message}"
        ) from error


@functools.lru_cache(maxsize=512)
def _check_schema_cached(canonical_schema: str) -> None:
    Draft202012Validator.check_schema(json.loads(canonical_schema))


@functools.lru_cache(maxsize=512)
def _validator_for(canonical_schema: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(canonical_schema))


def _validate_instance(schema: dict[str, Any], instance: Any, label: str) -> None:
    canonical = _canonical_json_schema(schema)
    try:
        validator = Draft202012Validator(schema) if canonical is None else _validator_for(canonical)
        validator.validate(instance)
    except Exception as error:
        raise AgentSdkError(
            "SCHEMA_VALIDATION_FAILED",
            f"{label} does not match its declared schema.",
            {
                "validation_error": str(error),
            },
        ) from error


def _canonical_json_schema(schema: dict[str, Any]) -> str | None:
    """Return a cache key only when JSON round-tripping preserves the host schema.

    Host-owned JSON Schema declarations may use Python objects accepted by jsonschema,
    such as ``Decimal`` constants. Coercing those objects into strings changes ``const``
    and ``enum`` constraints, so non-JSON schemas deliberately bypass the cache.
    """

    try:
        return json.dumps(schema, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
