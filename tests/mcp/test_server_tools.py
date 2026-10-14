import asyncio

from tests.support.agents import definition, final, task, tool, tool_call

EXPECTED_TOOLS = {
    "approve_controller_plan",
    "approve_orchestration",
    "assemble_initial_context",
    "cancel_agent_task",
    "cancel_controller",
    "cancel_orchestration",
    "cancel_run",
    "clear_project_blocker",
    "complete_controller",
    "create_controller",
    "create_telemetry_report",
    "decide_task_approval",
    "decline_elastic_requests",
    "dispatch_controller",
    "get_audit_log",
    "get_controller_state",
    "get_orchestration",
    "get_project_state",
    "get_run_state",
    "get_shared_state",
    "get_telemetry_events",
    "get_telemetry_metrics",
    "grant_elastic_capacity",
    "initialize_project_state",
    "inventory_retention",
    "list_metric_definitions",
    "list_run_approvals",
    "list_task_approvals",
    "list_task_files",
    "list_telemetry_runs",
    "open_project_question",
    "prepare_orchestration",
    "process_specification_manifest",
    "prune_runs",
    "publish_exploratory_discovery",
    "read_task_file",
    "reconcile_controller",
    "reconcile_orchestration",
    "record_controller_node_result",
    "record_controller_stage_failure",
    "record_human_project_decision",
    "record_metric_observation",
    "register_metric_definition",
    "render_audit_transcript",
    "request_lateral_dependency",
    "resolve_project_question",
    "resume_run",
    "run_agent_task",
    "start_run",
    "submit_approval",
    "submit_controller_plan",
    "submit_orchestration_for_approval",
    "validate_agent_definition",
    "validate_plan",
    "verify_provenance_contract",
}


def test_server_exposes_exactly_the_documented_tools(server):
    async def listing():
        async with server.session() as session:
            return (await session.list_tools()).tools

    tools = asyncio.run(listing())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS and len(tools) == 55
    assert all(t.description and t.input_schema for t in tools)


def test_agent_tools(server):
    good = definition().model_dump(mode="json")
    valid = server.call("validate_agent_definition", {"definition": good})
    assert valid["valid"] is True and valid["tool_names"] == []
    invalid = server.call("validate_agent_definition", {"definition": {"identity": ""}})
    assert invalid["valid"] is False and any("identity" in e["location"] for e in invalid["errors"])
    context = server.call(
        "assemble_initial_context", {"definition": good, "task": task().model_dump(mode="json")}
    )
    assert context["ok"] and [s["kind"] for s in context["context"]["sections"]][:2] == [
        "identity",
        "instructions",
    ]
    bad_task = server.call("assemble_initial_context", {"definition": good, "task": {"id": "x"}})
    assert bad_task["ok"] is False and bad_task["errors"]
    run = server.call(
        "run_agent_task",
        {
            "definition": good,
            "task": task("mcp-run-1").model_dump(mode="json"),
            "runtime_options": {"scripted_turns": [final()]},
        },
    )
    assert run["ok"] and run["result"]["status"] == "completed" and run["result"]["iterations"] == 1
    undeclared = server.call(
        "run_agent_task",
        {
            "definition": good,
            "task": task("mcp-run-2").model_dump(mode="json"),
            "runtime_options": {"scripted_turns": [tool_call("c1", "ghost")]},
        },
    )
    assert undeclared["ok"] and undeclared["result"]["status"] == "failed"
    echo_def = definition(tools=[tool("echo")]).model_dump(mode="json")
    echoed = server.call(
        "run_agent_task",
        {
            "definition": echo_def,
            "task": task("mcp-run-3").model_dump(mode="json"),
            "runtime_options": {"scripted_turns": [tool_call("c1", "echo", {"a": 1}), final()]},
        },
    )
    assert echoed["result"]["status"] == "completed"
    wrong_options = server.call(
        "run_agent_task",
        {
            "definition": good,
            "task": task("mcp-run-4").model_dump(mode="json"),
            "runtime_options": {"mode": "live"},
        },
    )
    assert wrong_options["ok"] is False


def test_project_state_tools(server):
    schema = {"schema_id": "s-v1", "stage": "design"}
    state = server.call(
        "initialize_project_state", {"project_id": "proj-1", "stage_schema": schema}
    )
    assert state["ok"] and state["state"]["revision"] == 0
    decision = server.call(
        "record_human_project_decision",
        {
            "project_id": "proj-1",
            "decision_id": "d1",
            "content": "use X",
            "status": "locked",
            "evidence_id": "ev-1",
        },
    )
    assert decision["ok"] and decision["state"]["decisions"][0]["authority"] == "human"
    question = server.call(
        "open_project_question",
        {
            "project_id": "proj-1",
            "question_id": "q1",
            "content": "why?",
            "owner": "human",
            "evidence_id": "ev-2",
        },
    )
    assert question["ok"] and question["state"]["revision"] == 2
    fetched = server.call("get_project_state", {"project_id": "proj-1"})
    assert fetched["state"]["state_hash"] == question["state"]["state_hash"]
    assert server.call("get_project_state", {"project_id": "nope"})["ok"] is False
    assert (
        server.call(
            "open_project_question",
            {
                "project_id": "proj-1",
                "question_id": "q2",
                "content": "?",
                "owner": "alien",
                "evidence_id": "e",
            },
        )["ok"]
        is False
    )
    assert (
        server.call(
            "record_human_project_decision",
            {
                "project_id": "proj-1",
                "decision_id": "d2",
                "content": "c",
                "status": "bogus",
                "evidence_id": "e",
            },
        )["ok"]
        is False
    )


def test_questions_and_blockers_are_closed_over_the_wire(server):
    schema = {"schema_id": "s-v1", "stage": "design"}
    assert server.call(
        "initialize_project_state", {"project_id": "proj-close", "stage_schema": schema}
    )["ok"]
    question = server.call(
        "open_project_question",
        {
            "project_id": "proj-close",
            "question_id": "q1",
            "content": "why?",
            "owner": "human",
            "evidence_id": "ev-2",
        },
    )
    assert question["ok"] and question["state"]["revision"] == 1
    wrong_owner = server.call(
        "resolve_project_question",
        {
            "project_id": "proj-close",
            "question_id": "nope",
            "resolution": "r",
            "evidence_id": "ev-x",
        },
    )
    assert wrong_owner["ok"] is False
    assert 'Open question "nope" does not exist' in wrong_owner["errors"][0]["message"]
    resolved = server.call(
        "resolve_project_question",
        {
            "project_id": "proj-close",
            "question_id": "q1",
            "resolution": "because the designer said so",
            "evidence_id": "ev-3",
            "source_spans": ["chat:42"],
        },
    )
    assert resolved["ok"] and resolved["state"]["open_questions"] == []
    assert resolved["state"]["revision"] == 2
    unknown_blocker = server.call(
        "clear_project_blocker",
        {"project_id": "proj-close", "blocker_id": "tool:c9", "reason": "r", "evidence_id": "ev-4"},
    )
    assert unknown_blocker["ok"] is False
    assert 'Blocker "tool:c9" does not exist' in unknown_blocker["errors"][0]["message"]
    blank = server.call(
        "clear_project_blocker",
        {"project_id": "proj-close", "blocker_id": "tool:c9", "reason": " ", "evidence_id": "ev-4"},
    )
    assert blank["ok"] is False
    reopened = server.call(
        "open_project_question",
        {
            "project_id": "proj-close",
            "question_id": "q1",
            "content": "why again?",
            "owner": "human",
            "evidence_id": "ev-5",
        },
    )
    assert reopened["ok"] and reopened["state"]["revision"] == 3


def test_specification_tools(server):
    (server.spec_root / "a.md").write_text("The adder shall add.\nIt shall be fast.\n", "utf-8")
    (server.spec_root / "specification-manifest.yaml").write_text(
        "documents:\n"
        "  - {id: spec-a, title: Spec A, format: md, path: a.md, category: functional}\n",
        "utf-8",
    )
    processed = server.call("process_specification_manifest", {})
    assert (
        processed["ok"]
        and processed["processed_paths"] == ["processed\\spec-a.document.yaml"]
        or processed["processed_paths"] == ["processed/spec-a.document.yaml"]
    )
    missing = server.call("process_specification_manifest", {"manifest_path": "../outside.yaml"})
    assert missing["ok"] is False and "escapes" in missing["errors"][0]["message"]
    assert [tree["category"] for tree in processed["trees"]] == ["functional"]
    keyed = server.call("process_specification_manifest", {"manifest_path": "a.md"})
    assert keyed["ok"] is False and "must be a mapping" in keyed["errors"][0]["message"]


def test_telemetry_tools(server):
    runs = server.call("list_telemetry_runs", {"limit": 50})
    assert runs["ok"] and any(r["run_id"] == "mcp-run-1" for r in runs["runs"])
    events = server.call("get_telemetry_events", {"run_id": "mcp-run-1"})
    assert events["integrity_chain_valid"] is True and events["events"]
    assert (
        server.call("get_telemetry_events", {"run_id": "mcp-run-1", "limit": 5000})["ok"] is False
    )
    audit = server.call("get_audit_log", {"run_id": "mcp-run-1"})
    assert audit["ok"] and audit["integrity_chain_valid"] is True and audit["events"]
    rendered = server.call("render_audit_transcript", {"run_id": "mcp-run-1"})
    assert rendered["ok"] and rendered["report"]["integrity_chain_valid"] is True
    assert server.call("get_telemetry_metrics", {"run_id": "mcp-run-1"})["metrics"]
    assert server.call("create_telemetry_report", {"run_id": "mcp-run-1"})["ok"]
    assert len(server.call("list_metric_definitions")["definitions"]) > 20
    assert (
        server.call("register_metric_definition", {"definition": {"metric_id": ""}})["ok"] is False
    )
    assert server.call("record_metric_observation", {"observation": {"nope": 1}})["ok"] is False
