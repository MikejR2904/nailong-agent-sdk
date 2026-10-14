import pytest

from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult, ToolResultHandle
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.orchestration_models import (
    ComplexityRoutingRules,
    GapMetadata,
    SkillToolProfile,
)
from nailong_agent_sdk.state.project_state_engine import ProjectStateReducer
from nailong_agent_sdk.state.project_state_models import (
    ArtifactStatus,
    StageStateSchema,
    StateAuthority,
)
from nailong_agent_sdk.state.project_state_store import InMemoryProjectStateStore
from nailong_agent_sdk.state.shared_state import SharedSubstrateSnapshot
from tests.support.plans import simple_plan
from tests.support.project_state import SCHEMA, T, evidence, transition


def test_reducer_authority_rules_and_payload_errors():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    with pytest.raises(ValueError, match="Only a human authority"):
        store.apply(
            "p",
            transition(
                T.HUMAN_DECISION,
                {"decision_id": "d", "content": "c", "status": "locked"},
                StateAuthority.HARNESS,
            ),
        )
    state = store.apply(
        "p",
        transition(
            T.HUMAN_DECISION,
            {"decision_id": "d", "content": "c", "status": "locked"},
            StateAuthority.HUMAN,
        ),
    )
    assert state.decisions[0].authority is StateAuthority.HUMAN and state.revision == 1
    with pytest.raises(ValueError, match="Only controller or harness"):
        store.apply(
            "p",
            transition(
                T.WORK_ITEM_UPDATED,
                {"work_item_id": "w", "status": "completed", "owner": "o"},
                StateAuthority.HUMAN,
            ),
        )
    with pytest.raises(ValueError, match="Only controller or human"):
        store.apply(
            "p",
            transition(
                T.STAGE_CHANGED, {"stage_schema": SCHEMA.model_dump()}, StateAuthority.HARNESS
            ),
        )
    try:
        store.apply("p", transition(T.HUMAN_DECISION, {"decision_id": "d2"}, StateAuthority.HUMAN))
        outcome = "ok"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome.startswith("ValueError"), outcome
    with pytest.raises(ValueError, match="unknown"):
        store.load("nope")
    assert store.events("p")[0].previous_state_hash != store.events("p")[0].state_hash


def test_stage_change_requires_fields_and_gate_semantics():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    new_schema = StageStateSchema(schema_id="s2", stage="build", required_field_ids=["target"])
    with pytest.raises(ValueError, match="missing required stage fields"):
        store.apply(
            "p",
            transition(
                T.STAGE_CHANGED,
                {"stage_schema": new_schema.model_dump(), "stage_fields": []},
                StateAuthority.CONTROLLER,
            ),
        )
    field = {"field_id": "target", "value": 1, "evidence": [e.model_dump() for e in evidence()]}
    state = store.apply(
        "p",
        transition(
            T.STAGE_CHANGED,
            {"stage_schema": new_schema.model_dump(), "stage_fields": [field]},
            StateAuthority.CONTROLLER,
        ),
    )
    assert state.stage == "build"


def test_capacity_eviction_rules_and_error_message():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    for i in range(1_030):
        store.apply(
            "p",
            transition(
                T.TOOL_OUTCOME,
                {
                    "tool_call_id": f"c{i}",
                    "tool_name": "write_draft",
                    "status": "succeeded",
                    "artifact": {
                        "relative_path": f"f{i}.txt",
                        "artifact_id": f"sha256:{i}",
                        "status": "in-progress",
                    },
                },
                action=f"c{i}",
            ),
        )
    state = store.load("p")
    assert len(state.artifacts) == 1_024 and state.artifacts[-1].relative_path == "f1029.txt"
    assert state.artifacts[0].relative_path == "f6.txt"
    for i in range(257):
        try:
            store.apply(
                "p",
                transition(
                    T.QUESTION_OPENED,
                    {"question_id": f"q{i}", "content": "?", "owner": "human"},
                    action=f"q{i}",
                ),
            )
        except AgentSdkError as error:
            assert (
                error.code == "PROJECT_STATE_CAPACITY_EXCEEDED"
                and "question" in str(error)
                and "q256" in str(error)
            )
            break
    else:
        pytest.fail("question capacity was never enforced")


def test_tool_outcome_tracks_artifacts_from_every_write_tool():
    handle = ToolResultHandle(handle_id="handle-1", content_hash="h", byte_count=1, truncated=False)
    results = {
        "write_draft": ToolExecutionResult(
            status="succeeded", output={"artifact_id": "sha256:a", "relative_path": "a.md"}
        ),
        "edit_draft": ToolExecutionResult(
            status="succeeded",
            output={
                "replacements": 1,
                "artifact": {"artifact_id": "sha256:b", "relative_path": "a.md"},
            },
        ),
        "notebook_edit": ToolExecutionResult(
            status="succeeded",
            output={
                "cell_index": 0,
                "artifact": {"artifact_id": "sha256:c", "relative_path": "n.ipynb"},
            },
        ),
    }
    tracked = {}
    for name, result in results.items():
        transition_ = ProjectStateReducer.tool_transition(
            ToolCall(id=f"c-{name}", name=name), result, handle
        )
        tracked[name] = "artifact" in transition_.payload
    assert all(tracked.values()), tracked


def draft_outcome(relative_path="a.md", artifact_id="sha256:a", status="in-progress"):
    return transition(
        T.TOOL_OUTCOME,
        {
            "tool_call_id": f"c-{artifact_id}",
            "tool_name": "write_draft",
            "status": "succeeded",
            "artifact": {
                "relative_path": relative_path,
                "artifact_id": artifact_id,
                "status": status,
            },
        },
        action=f"c-{artifact_id}",
    )


def artifact_status_update(
    status="complete", artifact_id="sha256:a", actor=StateAuthority.CONTROLLER
):
    return transition(
        T.ARTIFACT_STATUS_UPDATED,
        {"relative_path": "a.md", "artifact_id": artifact_id, "status": status},
        actor,
        action=f"status-{status}-{artifact_id}",
    )


def test_a_tool_outcome_cannot_mark_an_artifact_complete():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    with pytest.raises(ValueError, match='Artifact "a.md" cannot be recorded as complete'):
        store.apply("p", draft_outcome(status="complete"))


def test_the_controller_can_complete_the_artifact_it_names_by_id():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome())
    store.apply("p", artifact_status_update())
    assert {a.status for a in store.load("p").artifacts} == {ArtifactStatus.COMPLETE}


def test_artifact_status_updates_need_controller_or_human_authority():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome())
    with pytest.raises(ValueError, match="Only controller or human authority"):
        store.apply("p", artifact_status_update(actor=StateAuthority.HARNESS))


def test_a_completion_decision_for_an_older_artifact_version_is_rejected():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome(artifact_id="sha256:new"))
    with pytest.raises(ValueError, match="recorded as sha256:new, but the status update names"):
        store.apply("p", artifact_status_update(artifact_id="sha256:old"))


def test_a_completion_decision_for_an_unrecorded_artifact_is_rejected():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    with pytest.raises(ValueError, match='Artifact "a.md" is not recorded'):
        store.apply("p", artifact_status_update())


def test_editing_a_completed_artifact_returns_it_to_in_progress():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome())
    store.apply("p", artifact_status_update())
    store.apply("p", draft_outcome(artifact_id="sha256:edited"))
    (artifact,) = store.load("p").artifacts
    assert artifact.status is ArtifactStatus.IN_PROGRESS and artifact.artifact_id == "sha256:edited"


def test_the_controller_completes_a_recorded_artifact_and_refuses_an_unrecorded_one(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    snapshot = SharedSubstrateSnapshot(snapshot_id="snap", version="1", content_hash="h")
    profile = SkillToolProfile(stage="design", source_snapshot_id="snap")
    rules = ComplexityRoutingRules(multi_agent_min_categories=3, multi_agent_min_blast_radius=5)
    controller = runtime.create_controller(
        snapshot, profile, rules, GapMetadata(), max_repair_attempts=2
    )
    runtime.submit_plan(controller.controller_id, simple_plan())
    runtime.approve_plan(controller.controller_id, True)
    runtime.dispatch(controller.controller_id)
    runtime._project_state_store.apply("snap", draft_outcome())
    (before,) = runtime.project_state(controller.controller_id).artifacts
    assert before.relative_path == "a.md" and before.status is not ArtifactStatus.COMPLETE
    runtime.set_artifact_status(
        controller.controller_id, "a.md", ArtifactStatus.COMPLETE, reason="reviewed"
    )
    (after,) = runtime.project_state(controller.controller_id).artifacts
    assert after.status is ArtifactStatus.COMPLETE
    with pytest.raises(ValueError, match='Artifact "missing.md" is not recorded'):
        runtime.set_artifact_status(
            controller.controller_id, "missing.md", ArtifactStatus.COMPLETE, reason="x"
        )
