# Copyright (c) 2026 David Michael Indraputra

"""
Serializable BaseAgent contract models and validation.
This module defines the structured types that describe how agents run:
- Agent definitions (identity, instructions, schemas, tools, model binding).
- Task payloads, tool calls, turns, results, and lifecycle events.
- Enums for memory scope, escalation, concurrency, run status, episode kind.
- Validation helpers for JSON Schema, ensuring inputs/outputs match contracts.
- Failure types with safe redaction so secrets and hidden reasoning never leak.
Together these models form the durable contract between agent runtime and storage.
"""

from __future__ import annotations

import functools
import json
from enum import StrEnum
from typing import Annotated, Any, Literal, Never

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema_specifications import REGISTRY as _SCHEMA_SPECIFICATIONS
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

from .dependency_graph import deterministic_cycles
from .errors import AgentSdkError, sanitize_failure_details
from .identifiers import require_unique
from .json_limits import assert_json_depth


class StrictModel(BaseModel):
    """Reject unknown public contract fields rather than silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class MemoryScope(StrEnum):
    # Task-scoped is a temporary memory for just one task
    # Cross-session is a persistent memory shared across tasks and sessions
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
                    f'model fallback binding "{fallback.provider}/{fallback.model}" '
                    "must be distinct from the primary model and from every other fallback"
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

    # identity: unique agent name, must be one line
    # instructions: versioned text
    # input_schema/output_schema: JSON Schema for inputs/outputs
    # tools: list of tool definitions
    # model_binding: primary model + fallbacks
    # memory_scope/rationale: how memory is used
    # termination_policy: max iterations, escalation target
    # verification_gate_id: optional deterministic check

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
        require_unique(tool_names, "tool names")
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

    @field_validator("arguments")
    @classmethod
    def arguments_are_bounded(cls, arguments: dict[str, Any]) -> dict[str, Any]:
        return assert_json_depth(arguments, "tool call arguments")

    @model_validator(mode="after")
    def dependencies_are_unique_and_external(self) -> ToolCall:
        require_unique(self.depends_on_call_ids, "tool-call dependencies")
        if self.id in self.depends_on_call_ids:
            raise ValueError(f'tool call "{self.id}" cannot depend on itself')
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

    @field_validator("output")
    @classmethod
    def output_is_bounded(cls, output: Any) -> Any:
        return assert_json_depth(output, "tool result output")


class ToolCallTurn(StrictModel):
    type: Literal["tool-call"] = "tool-call"
    call: ToolCall

    @field_validator("call")
    @classmethod
    def call_declares_no_batch_dependencies(cls, call: ToolCall) -> ToolCall:
        if call.depends_on_call_ids:
            raise ValueError(
                f'tool call "{call.id}" lists depends_on_call_ids {call.depends_on_call_ids} '
                "but a tool-call turn holds one call; use a tool-batch turn to declare dependencies"
            )
        return call


class ToolBatchTurn(StrictModel):
    """One model turn with an acyclic batch of correlated tool calls."""

    type: Literal["tool-batch"] = "tool-batch"
    calls: list[ToolCall] = Field(min_length=1)

    @model_validator(mode="after")
    def batch_dependencies_are_declared_and_acyclic(self) -> ToolBatchTurn:
        call_ids = [call.id for call in self.calls]
        require_unique(call_ids, "tool batch call IDs")
        known = set(call_ids)
        for call in self.calls:
            unknown = set(call.depends_on_call_ids) - known
            if unknown:
                raise ValueError(
                    f'tool call "{call.id}" depends on unknown batch call IDs: {sorted(unknown)}'
                )
        edges = [
            (call.id, dependency) for call in self.calls for dependency in call.depends_on_call_ids
        ]
        if deterministic_cycles(call_ids, edges):
            raise ValueError("tool batch dependencies must be acyclic")
        return self


class FinalTurn(StrictModel):
    type: Literal["final"] = "final"
    output: Any

    @field_validator("output")
    @classmethod
    def output_is_bounded(cls, output: Any) -> Any:
        return assert_json_depth(output, "final output")


class BlockedTurn(StrictModel):
    type: Literal["blocked"] = "blocked"
    reason: str = Field(min_length=1)


AgentTurn = Annotated[
    ToolCallTurn | ToolBatchTurn | FinalTurn | BlockedTurn,
    Field(discriminator="type"),
]


class RuntimeOptions(StrictModel):
    """Per-run limits and budgets; scripted turns drive a run that has no model endpoint."""

    mode: Literal["deterministic"] | None = None
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


class CompactedEpisodeStub(StrictModel):
    """Minimal summary of a compacted episode.
    Keeps a one-line residue (ID, kind, summary, optional tool info and status) so
    the model can still see the episode's existence without storing the full transcript.
    """

    episode_id: str = Field(min_length=1)
    kind: EpisodeKind
    summary: str
    tool_name: str | None = None
    status: Literal["succeeded", "failed", "blocked"] | None = None
    iteration: int | None = Field(default=None, ge=1)
    handle_id: str | None = None


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
    omitted_compacted_count: int = Field(default=0, ge=0)


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
        "model-turn-retrying",
        "stream-listener-failed",
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


def _refuse_retrieval(uri: str) -> Never:
    raise NoSuchResource(ref=uri)


_LOCAL_REFERENCES_ONLY = _SCHEMA_SPECIFICATIONS.combine(Registry(retrieve=_refuse_retrieval))


def _new_validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema, registry=_LOCAL_REFERENCES_ONLY)


@functools.lru_cache(maxsize=512)
def _validator_for(canonical_schema: str) -> Draft202012Validator:
    return _new_validator(json.loads(canonical_schema))


def _validate_instance(schema: dict[str, Any], instance: Any, label: str) -> None:
    canonical = _canonical_json_schema(schema)
    validator = _new_validator(schema) if canonical is None else _validator_for(canonical)
    try:
        validator.validate(instance)
    except Unresolvable as error:
        raise AgentSdkError(
            "SCHEMA_REFERENCE_UNRESOLVABLE",
            f'{label} could not be checked: its schema references "{error.ref}", which is not '
            "inside the schema itself. Only references inside the schema itself (for example "
            '"#/$defs/name" or its own $id) and the JSON Schema metaschemas are supported, and '
            "no document is ever fetched from a URL or file.",
            {"reference": error.ref},
        ) from error
    except JsonSchemaValidationError as error:
        location = error.json_path if error.path else f"{error.json_path} (root)"
        raise AgentSdkError(
            "SCHEMA_VALIDATION_FAILED",
            f"{label} does not match its declared schema at {location}: {error.message}.",
            {
                "json_path": error.json_path,
                "schema_rule": str(error.validator),
                "failed_value": sanitize_failure_details({"value": error.instance}),
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
