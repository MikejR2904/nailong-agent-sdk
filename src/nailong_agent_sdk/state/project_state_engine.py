# Copyright (c) 2026 David Michael Indraputra

"""Deterministic projection and reduction over ``ProjectState``.

``ProjectStateProjector`` selects a token-bounded current-state view.
``ProjectStateReducer`` is a mechanical reducer: tool outcomes and controller
events, never model claims.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..foundations.canonical import canonical_json, estimate_tokens
from ..foundations.contracts import ToolCall, ToolExecutionResult, ToolResultHandle
from ..foundations.errors import AgentSdkError
from .project_state_models import (
    MAX_ARTIFACTS,
    MAX_BLOCKERS,
    MAX_DECISIONS,
    MAX_OPEN_QUESTIONS,
    MAX_WORK_ITEMS,
    ArtifactStatus,
    DecisionStatus,
    OpenQuestion,
    ProjectArtifactState,
    ProjectBlocker,
    ProjectDecision,
    ProjectState,
    ProjectStateProjectionPolicy,
    ProjectStateView,
    ProjectWorkItem,
    QuestionOwner,
    StageStateField,
    StageStateSchema,
    StateAction,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
    WorkItemStatus,
    make_project_state,
)


class ProjectStateProjector:
    """Deterministically select current-state entries without transcript replay."""

    def __init__(self, policy: ProjectStateProjectionPolicy | None = None) -> None:
        self._policy = policy or ProjectStateProjectionPolicy()

    @property
    def policy(self) -> ProjectStateProjectionPolicy:
        return self._policy

    def project(self, state: ProjectState) -> ProjectStateView:
        core = {
            "project_id": state.project_id,
            "revision": state.revision,
            "stage_schema": state.stage_schema,
            "stage_fields": state.stage_fields,
            "decisions": state.decisions,
            "open_questions": state.open_questions,
            "blocked": state.blocked,
            "last_action": state.last_action,
            "step_count": state.step_count,
            "state_hash": state.state_hash,
        }
        used = estimate_tokens(core, models=True)
        artifacts: list[ProjectArtifactState] = []
        work_items: list[ProjectWorkItem] = []
        if used <= self._policy.token_budget:
            for artifact in reversed(state.artifacts):
                cost = estimate_tokens(artifact, models=True)
                if used + cost > self._policy.token_budget:
                    continue
                artifacts.append(artifact)
                used += cost
            for work_item in reversed(state.work_items):
                cost = estimate_tokens(work_item, models=True)
                if used + cost > self._policy.token_budget:
                    continue
                work_items.append(work_item)
                used += cost
        artifacts.reverse()
        work_items.reverse()
        return ProjectStateView(
            **core,
            artifacts=artifacts,
            work_items=work_items,
            estimated_tokens=used,
            token_budget=self._policy.token_budget,
            omitted_artifact_count=len(state.artifacts) - len(artifacts),
            omitted_work_item_count=len(state.work_items) - len(work_items),
            over_budget=used > self._policy.token_budget,
        )


class ProjectStateReducer:
    """Mechanical reducer: tool outcomes and controller events, never model claims."""

    @staticmethod
    def tool_transition(
        call: ToolCall,
        result: ToolExecutionResult,
        handle: ToolResultHandle,
        *,
        output_summary_max_chars: int = 256,
    ) -> StateTransition:
        artifact = _artifact_from_result(call, result)
        payload: dict[str, Any] = {
            "tool_call_id": call.id,
            "tool_name": call.name,
            "status": result.status,
            "error": _bounded_text(result.error, 1_024),
            "output_summary": _bounded_value(result.output, output_summary_max_chars),
        }
        if artifact is not None:
            payload["artifact"] = artifact
        return StateTransition(
            kind=StateTransitionKind.TOOL_OUTCOME,
            actor=StateAuthority.HARNESS,
            action_id=call.id,
            payload=payload,
            evidence=[
                StateEvidence(
                    evidence_id=handle.handle_id,
                    kind="tool-result-handle",
                    content_hash=handle.content_hash,
                )
            ],
        )

    @staticmethod
    def agent_result_transition(
        task_id: str,
        status: str,
        result_hash: str,
        *,
        artifact_ids: Sequence[str] = (),
    ) -> StateTransition:
        """Record a run's terminal status.

        ``artifact_ids`` names the artifact versions the run wrote. A run reaches
        ``completed`` only after its verification gate accepted the output, so
        those exact versions become ``ArtifactStatus.COMPLETE``.
        """

        payload: dict[str, Any] = {"task_id": task_id, "status": status}
        if artifact_ids:
            payload["artifact_ids"] = sorted(set(artifact_ids))
        return StateTransition(
            kind=StateTransitionKind.AGENT_RESULT,
            actor=StateAuthority.HARNESS,
            action_id=f"agent-result:{task_id}",
            payload=payload,
            evidence=[
                StateEvidence(
                    evidence_id=f"agent-result:{task_id}",
                    kind="agent-result",
                    content_hash=result_hash,
                )
            ],
        )

    @staticmethod
    def apply(
        current: ProjectState,
        transition: StateTransition,
        *,
        summary_max_chars: int = 2_048,
    ) -> ProjectState:
        artifacts = list(current.artifacts)
        blocked = list(current.blocked)
        work_items = list(current.work_items)
        decisions = list(current.decisions)
        questions = list(current.open_questions)
        stage_schema = current.stage_schema
        stage_fields = list(current.stage_fields)
        payload = transition.payload
        action_status = str(payload.get("status", "recorded"))

        if transition.kind is StateTransitionKind.TOOL_OUTCOME:
            artifact = payload.get("artifact")
            if isinstance(artifact, dict):
                artifacts = _upsert(
                    artifacts,
                    "relative_path",
                    ProjectArtifactState.model_validate(
                        {
                            **artifact,
                            "evidence": transition.evidence,
                        }
                    ),
                )
            if action_status == "blocked":
                blocked = _upsert(
                    blocked,
                    "blocker_id",
                    ProjectBlocker(
                        blocker_id=f"tool:{transition.action_id}",
                        subject_id=str(payload.get("tool_name", transition.action_id)),
                        reason=str(payload.get("error") or "Tool reported a blocked requirement."),
                        evidence=transition.evidence,
                    ),
                )

        elif transition.kind is StateTransitionKind.AGENT_RESULT:
            status = str(payload["status"])
            work_items = _upsert(
                work_items,
                "work_item_id",
                ProjectWorkItem(
                    work_item_id=str(payload["task_id"]),
                    status=WorkItemStatus.COMPLETED
                    if status == "completed"
                    else WorkItemStatus.BLOCKED
                    if status == "blocked"
                    else WorkItemStatus.CANCELLED
                    if status == "cancelled"
                    else WorkItemStatus.FAILED,
                    owner="base-agent",
                    evidence=transition.evidence,
                ),
            )
            if status == "completed":
                verified = set(payload.get("artifact_ids") or ())
                artifacts = [
                    artifact.model_copy(
                        update={
                            "status": ArtifactStatus.COMPLETE,
                            "evidence": [*artifact.evidence, *transition.evidence][-32:],
                        }
                    )
                    if artifact.artifact_id in verified
                    and artifact.status is ArtifactStatus.IN_PROGRESS
                    else artifact
                    for artifact in artifacts
                ]

        elif transition.kind is StateTransitionKind.HUMAN_DECISION:
            if transition.actor is not StateAuthority.HUMAN:
                raise ValueError("Only a human authority may record a human decision.")
            decisions = _upsert(
                decisions,
                "decision_id",
                ProjectDecision(
                    decision_id=str(payload["decision_id"]),
                    content=str(payload["content"]),
                    status=DecisionStatus(payload["status"]),
                    authority=StateAuthority.HUMAN,
                    evidence=transition.evidence,
                ),
            )

        elif transition.kind is StateTransitionKind.QUESTION_OPENED:
            questions = _upsert(
                questions,
                "question_id",
                OpenQuestion(
                    question_id=str(payload["question_id"]),
                    content=str(payload["content"]),
                    owner=QuestionOwner(payload["owner"]),
                    evidence=transition.evidence,
                ),
            )

        elif transition.kind is StateTransitionKind.WORK_ITEM_UPDATED:
            if transition.actor not in {StateAuthority.CONTROLLER, StateAuthority.HARNESS}:
                raise ValueError("Only controller or harness authority may update a work item.")
            work_items = _upsert(
                work_items,
                "work_item_id",
                ProjectWorkItem(
                    work_item_id=str(payload["work_item_id"]),
                    status=WorkItemStatus(payload["status"]),
                    owner=str(payload["owner"]),
                    evidence=transition.evidence,
                ),
            )

        elif transition.kind is StateTransitionKind.STAGE_CHANGED:
            if transition.actor not in {StateAuthority.CONTROLLER, StateAuthority.HUMAN}:
                raise ValueError("Only controller or human authority may change project stage.")
            stage_schema = StageStateSchema.model_validate(payload["stage_schema"])
            stage_fields = [
                StageStateField.model_validate(item) for item in payload.get("stage_fields", [])
            ]

        elif transition.kind is StateTransitionKind.ARTIFACT_STATUS_CHANGED:
            # Every StateAuthority (harness, controller, human) may record a review;
            # models never hold one.
            path = str(payload["relative_path"])
            existing = next((a for a in artifacts if a.relative_path == path), None)
            if existing is None:
                raise ValueError(f'Artifact "{path}" is not recorded in project state.')
            expected_id = payload.get("artifact_id")
            if expected_id is not None and expected_id != existing.artifact_id:
                raise ValueError(
                    f'Artifact "{path}" changed since it was reviewed '
                    f"(recorded {existing.artifact_id}, reviewed {expected_id})."
                )
            artifacts = _upsert(
                artifacts,
                "relative_path",
                existing.model_copy(
                    update={
                        "status": ArtifactStatus(payload["status"]),
                        "evidence": transition.evidence,
                    }
                ),
            )

        elif transition.kind is StateTransitionKind.BLOCKER_RESOLVED:
            if transition.actor not in {StateAuthority.CONTROLLER, StateAuthority.HUMAN}:
                raise ValueError("Only controller or human authority may resolve a blocker.")
            blocked = _remove(blocked, "blocker_id", str(payload["blocker_id"]), "Blocker")

        elif transition.kind is StateTransitionKind.QUESTION_RESOLVED:
            if transition.actor not in {StateAuthority.CONTROLLER, StateAuthority.HUMAN}:
                raise ValueError("Only controller or human authority may resolve a question.")
            question_id = str(payload["question_id"])
            question = next((q for q in questions if q.question_id == question_id), None)
            if (
                question is not None
                and question.owner is QuestionOwner.HUMAN
                and (transition.actor is not StateAuthority.HUMAN)
            ):
                raise ValueError("A question owned by a human must be resolved by a human.")
            questions = _remove(questions, "question_id", question_id, "Open question")

        else:
            raise ValueError(f"Unsupported state transition {transition.kind.value}.")

        project_id = current.project_id
        artifacts = _within_capacity(
            project_id, "artifacts", artifacts, MAX_ARTIFACTS, "relative_path", _always_closed
        )
        blocked = _within_capacity(
            project_id, "blocked", blocked, MAX_BLOCKERS, "blocker_id", _never_closed
        )
        work_items = _within_capacity(
            project_id, "work_items", work_items, MAX_WORK_ITEMS, "work_item_id", _is_closed_work
        )
        decisions = _within_capacity(
            project_id, "decisions", decisions, MAX_DECISIONS, "decision_id", _is_superseded
        )
        questions = _within_capacity(
            project_id,
            "open_questions",
            questions,
            MAX_OPEN_QUESTIONS,
            "question_id",
            _never_closed,
        )

        return make_project_state(
            project_id=current.project_id,
            revision=current.revision + 1,
            stage_schema=stage_schema,
            stage_fields=stage_fields,
            artifacts=artifacts,
            decisions=decisions,
            open_questions=questions,
            blocked=blocked,
            work_items=work_items,
            last_action=StateAction(
                action_id=transition.action_id,
                kind=transition.kind.value,
                status=action_status,
                evidence=transition.evidence,
                summary=_bounded_value(payload, summary_max_chars),
            ),
            step_count=current.step_count + 1,
        )


def _artifact_from_result(
    call: ToolCall,
    result: ToolExecutionResult,
) -> dict[str, Any] | None:
    if call.name != "write_draft" or result.status != "succeeded":
        return None
    if not isinstance(result.output, dict):
        return None
    artifact_id = result.output.get("artifact_id")
    relative_path = result.output.get("relative_path")
    if not isinstance(artifact_id, str) or not isinstance(relative_path, str):
        return None
    return {
        "relative_path": relative_path,
        "artifact_id": artifact_id,
        "status": ArtifactStatus.IN_PROGRESS.value,
    }


def _upsert[T](items: list[T], key: str, value: T) -> list[T]:
    value_key = getattr(value, key)
    replacement = [item for item in items if getattr(item, key) != value_key]
    return [*replacement, value]


def _remove[T](items: list[T], key: str, value: str, noun: str) -> list[T]:
    remaining = [item for item in items if getattr(item, key) != value]
    if len(remaining) == len(items):
        raise ValueError(f'{noun} "{value}" is not recorded in project state.')
    return remaining


_CLOSED_WORK_STATUSES = frozenset(
    {WorkItemStatus.COMPLETED, WorkItemStatus.FAILED, WorkItemStatus.CANCELLED}
)


def _always_closed(_item: object) -> bool:
    return True


def _never_closed(_item: object) -> bool:
    return False


def _is_closed_work(item: ProjectWorkItem) -> bool:
    return item.status in _CLOSED_WORK_STATUSES


def _is_superseded(decision: ProjectDecision) -> bool:
    return decision.status is DecisionStatus.SUPERSEDED


def _within_capacity[T](
    project_id: str,
    collection: str,
    items: list[T],
    limit: int,
    key: str,
    is_closed: Callable[[T], bool],
) -> list[T]:
    overflow = len(items) - limit
    if overflow <= 0:
        return items
    evictable = [index for index, item in enumerate(items[:-1]) if is_closed(item)]
    if len(evictable) < overflow:
        raise AgentSdkError(
            "PROJECT_STATE_CAPACITY_EXCEEDED",
            f'Project state "{project_id}" cannot record {collection} entry '
            f'"{getattr(items[-1], key)}": it already holds {limit} {collection} entries and '
            f"only {len(evictable)} of them are closed and eligible for eviction. Close, "
            "supersede, or cancel existing entries, or record unrelated work under a new "
            "project_id.",
            {
                "project_id": project_id,
                "collection": collection,
                "limit": limit,
                "evictable_entries": len(evictable),
            },
        )
    dropped = set(evictable[:overflow])
    return [item for index, item in enumerate(items) if index not in dropped]


def _bounded_value(value: Any, max_chars: int) -> Any:
    encoded = canonical_json(value, models=True)
    if len(encoded) <= max_chars:
        return value
    return {
        "kind": "truncated-state-summary",
        "preview": encoded[:max_chars],
        "truncated": True,
    }


def _bounded_text(value: str | None, max_chars: int) -> str | None:
    if value is None or len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}… [truncated]"
