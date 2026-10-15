import asyncio
import json

import pytest

from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.mcp.authority import McpOperation
from nailong_agent_sdk.mcp.authority_matrix import (
    AUTHORITY_MATRIX_SCHEMA,
    authority_matrix,
    registered_tool_operations,
)
from nailong_agent_sdk.mcp.server import create_mcp_server
from nailong_agent_sdk.observability.telemetry_models import TelemetryAuthority
from nailong_agent_sdk.state.project_state_engine import ProjectStateReducer
from nailong_agent_sdk.state.project_state_models import (
    StateAuthority,
    StateTransitionKind,
    make_project_state,
)
from tests.support.mcp_authority import tool_classification
from tests.support.project_state import SCHEMA, T, transition

OPEN_QUESTION = {"question_id": "q", "content": "why?"}


def empty_state():
    return make_project_state(
        project_id="p",
        revision=0,
        stage_schema=SCHEMA,
        stage_fields=[],
        artifacts=[],
        decisions=[],
        open_questions=[],
        blocked=[],
        work_items=[],
        last_action=None,
        step_count=0,
    )


def reduced(state, kind, payload, actor=StateAuthority.HARNESS, action="seed"):
    return ProjectStateReducer.apply(state, transition(kind, payload, actor, action))


def with_blocker():
    return reduced(
        empty_state(),
        T.TOOL_OUTCOME,
        {"tool_call_id": "c1", "tool_name": "x", "status": "blocked", "error": "needs"},
        action="c1",
    )


def with_artifact():
    return reduced(
        empty_state(),
        T.TOOL_OUTCOME,
        {
            "tool_call_id": "c2",
            "tool_name": "write_draft",
            "status": "succeeded",
            "artifact": {
                "relative_path": "a.txt",
                "artifact_id": "art-1",
                "status": "in-progress",
            },
        },
        action="c2",
    )


def with_question(owner):
    return reduced(empty_state(), T.QUESTION_OPENED, {**OPEN_QUESTION, "owner": owner})


CASES = {
    T.TOOL_OUTCOME: (
        empty_state,
        {"tool_call_id": "c", "tool_name": "x", "status": "succeeded"},
    ),
    T.AGENT_RESULT: (empty_state, {"task_id": "t", "status": "completed"}),
    T.HUMAN_DECISION: (
        empty_state,
        {"decision_id": "d", "content": "c", "status": "locked"},
    ),
    T.QUESTION_OPENED: (empty_state, {**OPEN_QUESTION, "owner": "human"}),
    T.BLOCKER_CLEARED: (with_blocker, {"blocker_id": "tool:c1", "reason": "done"}),
    T.WORK_ITEM_UPDATED: (
        empty_state,
        {"work_item_id": "w", "status": "completed", "owner": "o"},
    ),
    T.STAGE_CHANGED: (empty_state, {"stage_schema": SCHEMA.model_dump()}),
    T.ARTIFACT_STATUS_UPDATED: (
        with_artifact,
        {"relative_path": "a.txt", "artifact_id": "art-1", "status": "complete"},
    ),
}


def accepted(state, kind, payload, actor):
    try:
        reduced(state, kind, payload, actor, action="probe")
    except ValueError as error:
        assert "authority" in str(error), f"{kind.value} by {actor.value}: {error}"
        return False
    return True


@pytest.mark.parametrize("kind", list(CASES))
def test_the_reducer_accepts_exactly_the_actors_the_matrix_lists(kind):
    build, payload = CASES[kind]
    listed = authority_matrix()["state_transitions"][kind.value]["actors"]
    outcomes = {actor.value for actor in StateAuthority if accepted(build(), kind, payload, actor)}
    assert outcomes == set(listed)


@pytest.mark.parametrize("owner", ["human", "controller"])
def test_question_resolution_follows_the_question_owner_the_matrix_lists(owner):
    row = authority_matrix()["state_transitions"][T.QUESTION_RESOLVED.value]
    payload = {"question_id": "q", "resolution": "because"}
    outcomes = {
        actor.value
        for actor in StateAuthority
        if accepted(with_question(owner), T.QUESTION_RESOLVED, payload, actor)
    }
    assert outcomes == set(row["actors_by_question_owner"][owner])


def test_every_transition_kind_has_a_row_and_a_probe():
    rows = authority_matrix()["state_transitions"]
    assert set(rows) == {kind.value for kind in StateTransitionKind}
    assert set(CASES) | {T.QUESTION_RESOLVED} == set(StateTransitionKind)


def test_the_restricted_rows_keep_their_refusal_wording():
    expected = {
        T.HUMAN_DECISION: (StateAuthority.HARNESS, "Only a human authority may record"),
        T.BLOCKER_CLEARED: (
            StateAuthority.HARNESS,
            "Only controller or human authority may clear a blocker.",
        ),
        T.WORK_ITEM_UPDATED: (
            StateAuthority.HUMAN,
            "Only controller or harness authority may update a work item.",
        ),
        T.STAGE_CHANGED: (
            StateAuthority.HARNESS,
            "Only controller or human authority may change project stage.",
        ),
        T.ARTIFACT_STATUS_UPDATED: (
            StateAuthority.HARNESS,
            "Only controller or human authority may change the status of an artifact.",
        ),
    }
    for kind, (actor, message) in expected.items():
        build, payload = CASES[kind]
        with pytest.raises(ValueError, match="Only") as raised:
            reduced(build(), kind, payload, actor)
        assert str(raised.value).startswith(message), (kind, str(raised.value))


def test_the_matrix_lists_exactly_the_tools_a_server_registers(tmp_path):
    server = create_mcp_server(tmp_path / "listed")
    listed = {tool.name for tool in asyncio.run(server.list_tools())}
    classified = {
        name: operation.value for name, operation in tool_classification(tmp_path / "spy").items()
    }
    tools = authority_matrix()["mcp"]["tools"]
    assert set(tools) == listed
    assert tools == classified
    assert registered_tool_operations() == {
        name: McpOperation(value) for name, value in classified.items()
    }


def test_the_credential_rows_state_what_each_policy_does():
    policies = authority_matrix()["mcp"]["credential_policies"]
    assert set(policies) == {
        "approver-credential-configured",
        "host-credential-may-approve",
        "approvals-disabled",
    }
    configured = policies["approver-credential-configured"]
    assert configured["approver"] == {
        "read": "allowed",
        "host": "OperationNotPermitted",
        "approval": "allowed",
    }
    assert configured["host"]["approval"] == "OperationNotPermitted"
    assert policies["approvals-disabled"]["host"]["approval"] == "ApprovalsDisabled"
    assert policies["host-credential-may-approve"]["host"]["approval"] == "allowed"
    for policy in policies.values():
        assert set(policy["local"].values()) == {"allowed"}
        assert policy["host"]["read"] == "allowed" and policy["host"]["host"] == "allowed"


def test_the_matrix_names_both_authority_vocabularies():
    matrix = authority_matrix()
    assert matrix["schema_version"] == AUTHORITY_MATRIX_SCHEMA
    assert matrix["telemetry_authorities"] == [item.value for item in TelemetryAuthority]
    assert matrix["state_authorities"] == [item.value for item in StateAuthority]
    assert json.loads(json.dumps(matrix)) == matrix


def test_the_developer_command_prints_the_same_matrix(capsys):
    assert main(["authority-matrix"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == authority_matrix()
