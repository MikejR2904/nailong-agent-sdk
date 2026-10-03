# Copyright (c) 2026 David Michael Indraputra

"""Artifact completion and closing blockers/questions through the reducer."""

from __future__ import annotations

import pytest

from nailong_agent_sdk.foundations.contracts import (
    ToolCall,
    ToolExecutionResult,
    ToolResultHandle,
)
from nailong_agent_sdk.state.project_state_engine import ProjectStateReducer
from nailong_agent_sdk.state.project_state_models import (
    ArtifactStatus,
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
    make_project_state,
)
from nailong_agent_sdk.state.stage_gates import StageCompletenessGate, StageCompletenessPolicy

_EVIDENCE = [StateEvidence(evidence_id="e1", kind="test")]
_SCHEMA = StageStateSchema(schema_id="s", stage="draft")


def _state():
    return make_project_state(project_id="p", revision=0, stage_schema=_SCHEMA)


def _write(state, artifact_id: str, call_id: str = "c1"):
    call = ToolCall(id=call_id, name="write_draft", arguments={})
    result = ToolExecutionResult(
        status="succeeded", output={"artifact_id": artifact_id, "relative_path": "out.md"}
    )
    handle = ToolResultHandle(
        handle_id=f"result-{call_id}", content_hash="h", byte_count=1, truncated=False
    )
    return ProjectStateReducer.apply(
        state, ProjectStateReducer.tool_transition(call, result, handle)
    )


def _transition(kind, actor, payload):
    return StateTransition(
        kind=kind, actor=actor, action_id=f"{kind.value}:1", payload=payload, evidence=_EVIDENCE
    )


def test_completed_run_marks_only_the_versions_it_wrote_complete() -> None:
    state = _write(_state(), "v1")
    done = ProjectStateReducer.apply(
        state,
        ProjectStateReducer.agent_result_transition("t", "completed", "h", artifact_ids=["v2"]),
    )
    assert done.artifacts[0].status is ArtifactStatus.IN_PROGRESS
    done = ProjectStateReducer.apply(
        state,
        ProjectStateReducer.agent_result_transition("t", "completed", "h", artifact_ids=["v1"]),
    )
    assert done.artifacts[0].status is ArtifactStatus.COMPLETE
    policy = StageCompletenessPolicy(
        policy_id="p", stage="draft", required_artifact_paths=["out.md"]
    )
    assert StageCompletenessGate().evaluate(done, policy).complete
    # A rewrite reopens it.
    assert _write(done, "v3", "c2").artifacts[0].status is ArtifactStatus.IN_PROGRESS


def test_failed_run_does_not_complete_artifacts() -> None:
    state = _write(_state(), "v1")
    failed = ProjectStateReducer.apply(
        state, ProjectStateReducer.agent_result_transition("t", "failed", "h", artifact_ids=["v1"])
    )
    assert failed.artifacts[0].status is ArtifactStatus.IN_PROGRESS


def test_reviewed_artifact_status_requires_the_reviewed_version() -> None:
    state = _write(_state(), "v1")
    kind = StateTransitionKind.ARTIFACT_STATUS_CHANGED
    with pytest.raises(ValueError, match="changed since it was reviewed"):
        ProjectStateReducer.apply(
            state,
            _transition(
                kind,
                StateAuthority.HUMAN,
                {"relative_path": "out.md", "status": "complete", "artifact_id": "v0"},
            ),
        )
    reviewed = ProjectStateReducer.apply(
        state,
        _transition(
            kind,
            StateAuthority.HUMAN,
            {"relative_path": "out.md", "status": "complete", "artifact_id": "v1"},
        ),
    )
    assert reviewed.artifacts[0].status is ArtifactStatus.COMPLETE


def test_blockers_and_questions_can_be_closed() -> None:
    call = ToolCall(id="c9", name="run_tool", arguments={})
    blocked_result = ToolExecutionResult(status="blocked", error="needs approval")
    handle = ToolResultHandle(handle_id="r9", content_hash="h", byte_count=1, truncated=False)
    state = ProjectStateReducer.apply(
        _state(), ProjectStateReducer.tool_transition(call, blocked_result, handle)
    )
    state = ProjectStateReducer.apply(
        state,
        _transition(
            StateTransitionKind.QUESTION_OPENED,
            StateAuthority.HARNESS,
            {"question_id": "q1", "content": "Which clock?", "owner": "human"},
        ),
    )
    policy = StageCompletenessPolicy(
        policy_id="p", stage="draft", reject_blockers=True, reject_open_questions=True
    )
    assert not StageCompletenessGate().evaluate(state, policy).complete

    with pytest.raises(ValueError, match="resolved by a human"):
        ProjectStateReducer.apply(
            state,
            _transition(
                StateTransitionKind.QUESTION_RESOLVED,
                StateAuthority.CONTROLLER,
                {"question_id": "q1"},
            ),
        )
    state = ProjectStateReducer.apply(
        state,
        _transition(
            StateTransitionKind.QUESTION_RESOLVED, StateAuthority.HUMAN, {"question_id": "q1"}
        ),
    )
    state = ProjectStateReducer.apply(
        state,
        _transition(
            StateTransitionKind.BLOCKER_RESOLVED,
            StateAuthority.CONTROLLER,
            {"blocker_id": "tool:c9"},
        ),
    )
    assert StageCompletenessGate().evaluate(state, policy).complete
    with pytest.raises(ValueError, match="not recorded"):
        ProjectStateReducer.apply(
            state,
            _transition(
                StateTransitionKind.BLOCKER_RESOLVED,
                StateAuthority.HUMAN,
                {"blocker_id": "tool:c9"},
            ),
        )
