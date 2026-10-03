# Copyright (c) 2026 David Michael Indraputra

"""State-based working memory contracts with harness-owned, provenance-linked transitions.

`ProjectState` is the normal working memory for an agent invocation. Episode
records, raw tool output, and state-change events remain durable audit evidence;
they are not replayed as the agent's ordinary reasoning context.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from ..foundations.canonical import canonical_json
from ..foundations.contracts import StrictModel

MAX_STAGE_FIELDS = 128
MAX_ARTIFACTS = 1_024
MAX_DECISIONS = 256
MAX_OPEN_QUESTIONS = 256
MAX_BLOCKERS = 256
MAX_WORK_ITEMS = 1_024


class StateAuthority(StrEnum):
    HARNESS = "harness"
    CONTROLLER = "controller"
    HUMAN = "human"


class DecisionStatus(StrEnum):
    OPEN = "open"
    LOCKED = "locked"
    SUPERSEDED = "superseded"


class WorkItemStatus(StrEnum):
    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactStatus(StrEnum):
    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    COMPLETE = "complete"
    INVALID = "invalid"
    BLOCKED = "blocked"


class QuestionOwner(StrEnum):
    HUMAN = "human"
    CONTROLLER = "controller"


class StateEvidence(StrictModel):
    """Small provenance reference; never raw terminal output or a transcript."""

    evidence_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    content_hash: str | None = None
    source_spans: list[str] = Field(default_factory=list)


class StageStateSchema(StrictModel):
    """Declared stage schema; no model may invent a stage-state field."""

    schema_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    required_field_ids: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("required_field_ids")
    @classmethod
    def field_ids_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("required stage field IDs must be unique")
        return values


class StageStateField(StrictModel):
    field_id: str = Field(min_length=1)
    value: Any
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class ProjectDecision(StrictModel):
    decision_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4_096)
    status: DecisionStatus
    authority: StateAuthority
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class OpenQuestion(StrictModel):
    question_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4_096)
    owner: QuestionOwner
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class ProjectBlocker(StrictModel):
    blocker_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=4_096)
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class ProjectArtifactState(StrictModel):
    relative_path: str = Field(min_length=1)
    status: ArtifactStatus
    artifact_id: str | None = None
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class ProjectWorkItem(StrictModel):
    work_item_id: str = Field(min_length=1)
    status: WorkItemStatus
    owner: str = Field(min_length=1)
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class StateAction(StrictModel):
    action_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: str = Field(min_length=1)
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def summary_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(canonical_json(value, models=True)) > 4_096:
            raise ValueError("state action summary exceeds the 4,096-character bound")
        return value


class ProjectState(StrictModel):
    """The bounded, current-state object used as normal agent working memory."""

    schema_version: str = "project-state-v1"
    project_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    stage_schema: StageStateSchema
    stage_fields: list[StageStateField] = Field(default_factory=list, max_length=MAX_STAGE_FIELDS)
    artifacts: list[ProjectArtifactState] = Field(default_factory=list, max_length=MAX_ARTIFACTS)
    decisions: list[ProjectDecision] = Field(default_factory=list, max_length=MAX_DECISIONS)
    open_questions: list[OpenQuestion] = Field(default_factory=list, max_length=MAX_OPEN_QUESTIONS)
    blocked: list[ProjectBlocker] = Field(default_factory=list, max_length=MAX_BLOCKERS)
    work_items: list[ProjectWorkItem] = Field(default_factory=list, max_length=MAX_WORK_ITEMS)
    last_action: StateAction | None = None
    step_count: int = Field(default=0, ge=0)
    state_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def state_is_well_formed(self) -> ProjectState:
        _ensure_unique(self.stage_fields, "field_id", "stage field IDs")
        _ensure_unique(self.artifacts, "relative_path", "artifact paths")
        _ensure_unique(self.decisions, "decision_id", "decision IDs")
        _ensure_unique(self.open_questions, "question_id", "question IDs")
        _ensure_unique(self.blocked, "blocker_id", "blocker IDs")
        _ensure_unique(self.work_items, "work_item_id", "work-item IDs")
        if self.stage_schema.stage != self.stage:
            raise ValueError("stage schema must describe the current project stage")
        field_ids = {field.field_id for field in self.stage_fields}
        missing = set(self.stage_schema.required_field_ids) - field_ids
        if missing:
            raise ValueError(f"project state is missing required stage fields: {sorted(missing)}")
        if self.state_hash != project_state_hash(self):
            raise ValueError("project state hash does not match canonical state fields")
        return self

    @property
    def stage(self) -> str:
        return self.stage_schema.stage

    def model_view(self) -> dict[str, Any]:
        """Return the bounded, current-state-only view intended for an agent model."""

        return self.model_dump(mode="json")


class ProjectStateProjectionPolicy(StrictModel):
    """Explicit bound for the project state supplied to one model turn."""

    token_budget: int = Field(default=2_000, ge=128, le=1_000_000)
    action_output_summary_chars: int = Field(default=256, ge=32, le=1_000_000)


class ProjectStateView(StrictModel):
    """Bounded current-state view, deliberately excluding event and transcript history."""

    project_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    stage_schema: StageStateSchema
    stage_fields: list[StageStateField] = Field(default_factory=list)
    artifacts: list[ProjectArtifactState] = Field(default_factory=list)
    decisions: list[ProjectDecision] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    blocked: list[ProjectBlocker] = Field(default_factory=list)
    work_items: list[ProjectWorkItem] = Field(default_factory=list)
    last_action: StateAction | None = None
    step_count: int = Field(ge=0)
    state_hash: str = Field(min_length=1)
    estimated_tokens: int = Field(ge=0)
    token_budget: int = Field(ge=128)
    omitted_artifact_count: int = Field(ge=0)
    omitted_work_item_count: int = Field(ge=0)
    over_budget: bool = False


class StateTransitionKind(StrEnum):
    TOOL_OUTCOME = "tool-outcome"
    AGENT_RESULT = "agent-result"
    HUMAN_DECISION = "human-decision"
    QUESTION_OPENED = "question-opened"
    WORK_ITEM_UPDATED = "work-item-updated"
    STAGE_CHANGED = "stage-changed"


class StateTransition(StrictModel):
    """A typed state mutation request accepted only by the deterministic store."""

    kind: StateTransitionKind
    actor: StateAuthority
    action_id: str = Field(min_length=1)
    payload: dict[str, Any]
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)


class ProjectStateEvent(StrictModel):
    schema_version: str = "project-state-event-v1"
    project_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    kind: StateTransitionKind
    actor: StateAuthority
    action_id: str = Field(min_length=1)
    evidence: list[StateEvidence] = Field(min_length=1, max_length=32)
    previous_state_hash: str = Field(min_length=1)
    state_hash: str = Field(min_length=1)
    event_hash: str = Field(min_length=1)
    # Replay data for crash recovery. Outside ``event_hash`` so earlier events
    # stay valid; a replay is trusted only if it reproduces ``state_hash``.
    payload: dict[str, Any] | None = None
    summary_max_chars: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def event_hash_matches(self) -> ProjectStateEvent:
        expected = _event_hash(
            self.project_id,
            self.revision,
            self.kind,
            self.actor,
            self.action_id,
            self.evidence,
            self.previous_state_hash,
            self.state_hash,
        )
        if self.event_hash != expected:
            raise ValueError("project state event hash does not match canonical fields")
        return self


class ProjectStateRepository(Protocol):
    def ensure(self, project_id: str, stage_schema: StageStateSchema) -> ProjectState: ...

    def load(self, project_id: str) -> ProjectState: ...

    def apply(self, project_id: str, transition: StateTransition) -> ProjectState: ...


def make_project_state(
    *,
    project_id: str,
    revision: int,
    stage_schema: StageStateSchema,
    stage_fields: list[StageStateField] | None = None,
    artifacts: list[ProjectArtifactState] | None = None,
    decisions: list[ProjectDecision] | None = None,
    open_questions: list[OpenQuestion] | None = None,
    blocked: list[ProjectBlocker] | None = None,
    work_items: list[ProjectWorkItem] | None = None,
    last_action: StateAction | None = None,
    step_count: int = 0,
) -> ProjectState:
    payload = {
        "schema_version": "project-state-v1",
        "project_id": project_id,
        "revision": revision,
        "stage_schema": stage_schema,
        "stage_fields": stage_fields or [],
        "artifacts": artifacts or [],
        "decisions": decisions or [],
        "open_questions": open_questions or [],
        "blocked": blocked or [],
        "work_items": work_items or [],
        "last_action": last_action,
        "step_count": step_count,
        "state_hash": "pending",
    }
    state_hash = project_state_hash_from_payload(payload)
    return ProjectState.model_validate({**payload, "state_hash": state_hash})


def project_state_hash(state: ProjectState) -> str:
    return project_state_hash_from_payload(state.model_dump(mode="json"))


def project_state_hash_from_payload(payload: dict[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key != "state_hash"}
    return hashlib.sha256(canonical_json(stable, models=True).encode("utf-8")).hexdigest()


def _event_hash(
    project_id: str,
    revision: int,
    kind: StateTransitionKind,
    actor: StateAuthority,
    action_id: str,
    evidence: list[StateEvidence],
    previous_state_hash: str,
    state_hash: str,
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "project_id": project_id,
                "revision": revision,
                "kind": kind.value,
                "actor": actor.value,
                "action_id": action_id,
                "evidence": [item.model_dump(mode="json") for item in evidence],
                "previous_state_hash": previous_state_hash,
                "state_hash": state_hash,
            },
            models=True,
        ).encode("utf-8")
    ).hexdigest()


def _ensure_unique[T](items: list[T], key: str, label: str) -> None:
    values = [getattr(item, key) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
