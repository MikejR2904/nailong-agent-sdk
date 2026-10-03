"""MCP ``run_agent_task``: real model resolution and capability-governed tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from support.doubles import ScriptedModel

from nailong_agent_sdk.agent.model_resolver import ModelResolver
from nailong_agent_sdk.mcp.server import create_mcp_server

DEFINITION = {
    "identity": "Write one note.",
    "instructions": {"version": "v1", "text": "Write the note."},
    "input_schema": {"type": "object"},
    "tools": [
        {
            "name": "write_draft",
            "description": "Write a declared draft.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            "episode_kind": "action",
        },
        {
            "name": "read_file",
            "description": "Read a file.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "episode_kind": "exploratory",
        },
    ],
    "model_binding": {"provider": "scripted", "model": "m"},
    "output_schema": {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    },
    "termination_policy": {"max_iterations": 4, "status_field": "status"},
}
TASK = {
    "id": "note-1",
    "input": {},
    "scope": {"label": "notes"},
    "locked_interface": None,
    "instructions": "Write notes/out.md",
    "acceptance_criteria": ["notes/out.md exists"],
}


def _turns():
    return [
        {
            "type": "tool-call",
            "call": {
                "id": "c1",
                "name": "write_draft",
                "arguments": {"path": "notes/out.md", "content": "hello"},
            },
        },
        {"type": "final", "output": {"status": "complete"}},
    ]


def _call(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    return result.structured_content


@pytest.fixture
def scripted_server(tmp_path: Path):
    models: list[ScriptedModel] = []

    def factory(binding):
        models.append(ScriptedModel(_turns()))
        return models[-1]

    server = create_mcp_server(
        tmp_path, model_resolver=ModelResolver(factories={"scripted": factory})
    )
    return server, models, tmp_path


def test_unconfigured_model_provider_is_reported_not_scripted(tmp_path):
    server = create_mcp_server(tmp_path, model_resolver=ModelResolver())
    reply = _call(server, "run_agent_task", {"definition": DEFINITION, "task": TASK})
    assert reply["ok"] is False
    assert reply["errors"][0]["code"] == "MODEL_PROVIDER_NOT_CONFIGURED"


def test_write_needs_operator_approval_then_succeeds(scripted_server):
    server, models, root = scripted_server
    options = {"declared_output_paths": ["notes/out.md"]}
    first = _call(
        server,
        "run_agent_task",
        {"definition": DEFINITION, "task": TASK, "runtime_options": options},
    )
    assert first["ok"] is True
    assert first["result"]["status"] == "blocked"
    assert "requires a typed approval" in first["result"]["reason"]
    workspace = Path(first["workspace"])
    assert workspace == (root / "agent-tasks" / "note-1").resolve()
    assert not (workspace / "notes/out.md").exists()

    approvals = _call(server, "list_agent_task_approvals", {"task_id": "note-1"})["approvals"]
    assert [a["capability"] for a in approvals] == ["draft.write"]
    decided = _call(
        server,
        "submit_agent_task_approval",
        {"approval_id": approvals[0]["approval_id"], "approved": True, "reason": "ok"},
    )
    assert decided["approval"]["status"] == "approved"

    options["approval_ids"] = {"draft.write": approvals[0]["approval_id"]}
    second = _call(
        server,
        "run_agent_task",
        {"definition": DEFINITION, "task": TASK, "runtime_options": options},
    )
    assert second["result"]["status"] == "completed"
    assert (workspace / "notes/out.md").read_text() == "hello"
    # The verified run marks the exact draft version it wrote as complete.
    artifacts = second["result"]["project_state"]["artifacts"]
    assert [(a["relative_path"], a["status"]) for a in artifacts] == [("notes/out.md", "complete")]


def test_role_without_grant_is_blocked(scripted_server):
    server, models, _ = scripted_server
    reply = _call(
        server,
        "run_agent_task",
        {"definition": DEFINITION, "task": TASK, "runtime_options": {"role": "intruder"}},
    )
    assert reply["ok"] and reply["result"]["status"] == "blocked"
    assert 'Role "intruder" lacks capability' in reply["result"]["reason"]
    assert _call(server, "list_agent_task_approvals", {})["approvals"] == []


def test_unsafe_task_id_is_rejected(scripted_server):
    server, _, _ = scripted_server
    reply = _call(
        server, "run_agent_task", {"definition": DEFINITION, "task": {**TASK, "id": "../escape"}}
    )
    assert reply["ok"] is False and reply["errors"][0]["code"] == "TASK_ID_INVALID"
