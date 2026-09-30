# Copyright (c) 2026 David Michael Indraputra

"""Typed stage-completeness gates for deterministic controller decisions.

ProjectState already enforces declared required stage fields.  This module adds
a reusable gate over explicitly required artifact and work-item terminal states.
It returns evidence-backed reasons rather than inferring semantic completeness.
"""

from __future__ import annotations

from pydantic import Field

from ..foundations.contracts import StrictModel
from .project_state_models import ArtifactStatus, ProjectState, WorkItemStatus


class StageCompletenessPolicy(StrictModel):
    policy_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    required_artifact_paths: list[str] = Field(default_factory=list)
    required_work_item_ids: list[str] = Field(default_factory=list)
    reject_open_questions: bool = True
    reject_blockers: bool = True


class StageCompletenessDecision(StrictModel):
    complete: bool
    policy_id: str
    stage: str
    reasons: list[str] = Field(default_factory=list)
    missing_field_ids: list[str] = Field(default_factory=list)
    incomplete_artifact_paths: list[str] = Field(default_factory=list)
    incomplete_work_item_ids: list[str] = Field(default_factory=list)
    open_question_ids: list[str] = Field(default_factory=list)
    blocker_ids: list[str] = Field(default_factory=list)


class StageCompletenessGate:
    """Check finite declared requirements without interpreting free text."""

    def evaluate(
        self,
        state: ProjectState,
        policy: StageCompletenessPolicy,
    ) -> StageCompletenessDecision:
        reasons: list[str] = []
        if state.stage != policy.stage:
            return StageCompletenessDecision(
                complete=False,
                policy_id=policy.policy_id,
                stage=state.stage,
                reasons=[
                    f'Policy stage "{policy.stage}" does not match project stage "{state.stage}".'
                ],
            )
        field_ids = {field.field_id for field in state.stage_fields}
        missing_fields = sorted(set(state.stage_schema.required_field_ids) - field_ids)
        if missing_fields:
            reasons.append("Required stage fields are missing.")
        artifacts = {artifact.relative_path: artifact for artifact in state.artifacts}
        incomplete_artifacts = sorted(
            path
            for path in policy.required_artifact_paths
            if path not in artifacts or artifacts[path].status is not ArtifactStatus.COMPLETE
        )
        if incomplete_artifacts:
            reasons.append("Required artifacts are not complete.")
        work_items = {item.work_item_id: item for item in state.work_items}
        incomplete_work_items = sorted(
            item_id
            for item_id in policy.required_work_item_ids
            if item_id not in work_items
            or work_items[item_id].status is not WorkItemStatus.COMPLETED
        )
        if incomplete_work_items:
            reasons.append("Required work items are not completed.")
        question_ids = sorted(question.question_id for question in state.open_questions)
        if policy.reject_open_questions and question_ids:
            reasons.append("Open questions require disposition before this stage gate passes.")
        blocker_ids = sorted(blocker.blocker_id for blocker in state.blocked)
        if policy.reject_blockers and blocker_ids:
            reasons.append("Recorded blockers require disposition before this stage gate passes.")
        return StageCompletenessDecision(
            complete=not reasons,
            policy_id=policy.policy_id,
            stage=state.stage,
            reasons=reasons,
            missing_field_ids=missing_fields,
            incomplete_artifact_paths=incomplete_artifacts,
            incomplete_work_item_ids=incomplete_work_items,
            open_question_ids=question_ids if policy.reject_open_questions else [],
            blocker_ids=blocker_ids if policy.reject_blockers else [],
        )
