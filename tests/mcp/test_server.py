import asyncio
import importlib.metadata
import json
import os
import subprocess
import time

import pytest

from tests.support.agents import definition, final, task, tool, tool_call
from tests.support.mcp_server import Server, raw_post
from tests.support.specs import req, spec, tree

EXPECTED_TOOLS = {
    "approve_controller_plan",
    "approve_orchestration",
    "assemble_initial_context",
    "cancel_controller",
    "cancel_orchestration",
    "cancel_run",
    "clear_project_blocker",
    "classify_specification_version",
    "complete_controller",
    "create_controller",
    "create_specification_git_lock",
    "create_telemetry_report",
    "create_variant_worktree",
    "decline_elastic_requests",
    "dispatch_controller",
    "get_audit_log",
    "get_controller_state",
    "get_git_repository_state",
    "get_orchestration",
    "get_project_state",
    "get_run_state",
    "get_shared_state",
    "get_telemetry_events",
    "get_telemetry_metrics",
    "grant_elastic_capacity",
    "initialize_project_state",
    "list_metric_definitions",
    "list_telemetry_runs",
    "open_project_question",
    "prepare_orchestration",
    "process_specification_manifest",
    "publish_exploratory_discovery",
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
    "select_task_context",
    "soft_lock_specification",
    "start_run",
    "submit_approval",
    "submit_controller_plan",
    "submit_orchestration_for_approval",
    "validate_agent_definition",
    "validate_gate_one",
    "validate_plan",
    "verify_provenance_contract",
}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    instance = Server(tmp_path_factory.mktemp("mcp"))
    yield instance
    instance.stop()


def test_server_exposes_exactly_the_documented_tools(server):
    async def listing():
        async with server.session() as session:
            return (await session.list_tools()).tools

    tools = asyncio.run(listing())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS and len(tools) == 52
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


def test_hostile_scripted_output_does_not_break_the_server(server):
    deep: object = "leaf"
    for _ in range(120):
        deep = {"k": deep}
    schema = {
        "type": "object",
        "properties": {"status": {"const": "complete"}},
        "required": ["status"],
    }
    d = definition(output_schema=schema).model_dump(mode="json")
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "run_agent_task",
                "arguments": {
                    "definition": d,
                    "task": task("mcp-deep").model_dump(mode="json"),
                    "runtime_options": {
                        "scripted_turns": [
                            {"type": "final", "output": {"status": "complete", "deep": deep}}
                        ]
                    },
                },
            },
        }
    ).encode()
    status, raw = raw_post(
        server,
        body,
        {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        limit=6000,
    )
    assert status == 200 and b"Circular reference" not in raw, raw[:400]
    assert server.call("list_metric_definitions")["ok"] is True


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


def _plan_payload():
    from tests.support.plans import simple_plan

    return simple_plan().model_dump(mode="json")


def test_run_and_controller_tools_end_to_end(server):
    plan = _plan_payload()
    report = server.call("validate_plan", {"plan": plan})
    assert report["ok"] and report["report"]["valid"] is True
    bad = {**plan, "tasks": [{**plan["tasks"][0], "dependencies": ["ghost"]}]}
    assert server.call("validate_plan", {"plan": bad})["report"]["valid"] is False
    started = server.call("start_run", {"plan": plan})
    assert started["ok"]
    run_id = started["run"]["run_id"]
    assert (
        server.call("get_run_state", {"run_id": run_id})["run"]["run_hash"]
        == started["run"]["run_hash"]
    )
    assert server.call("resume_run", {"run_id": run_id})["ok"]
    cancelled = server.call("cancel_run", {"run_id": run_id})
    assert cancelled["ok"] and cancelled["run"]["cancelled"] is True
    rejected = server.call("start_run", {"plan": bad})
    assert rejected["ok"] is False
    snapshot = {"snapshot_id": "snap-mcp", "version": "1", "content_hash": "h"}
    profile = {"stage": "design", "source_snapshot_id": "snap-mcp"}
    rules = {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 5}
    controller = server.call(
        "create_controller",
        {
            "snapshot": snapshot,
            "profile": profile,
            "routing_rules": rules,
            "gap_metadata": {},
            "max_repair_attempts": 1,
        },
    )
    assert controller["ok"]
    cid = controller["controller"]["controller_id"]
    assert (
        server.call("submit_controller_plan", {"controller_id": cid, "plan": plan})["controller"][
            "phase"
        ]
        == "awaiting-plan-approval"
    )
    assert (
        server.call(
            "approve_controller_plan", {"controller_id": cid, "approved": True, "reason": "ok"}
        )["controller"]["phase"]
        == "dispatch-ready"
    )
    dispatched = server.call("dispatch_controller", {"controller_id": cid})
    assert dispatched["ok"] and dispatched["controller"]["phase"] == "executing"
    node_result = {"status": "completed", "output": {"v": 1}}
    recorded = server.call(
        "record_controller_node_result",
        {"controller_id": cid, "node_id": "node:T1", "result": node_result},
    )
    assert recorded["ok"] and recorded["run"]["graph"]["statuses"]["node:T1"] == "completed"
    assert (
        server.call("get_controller_state", {"controller_id": cid})["controller"]["run_id"]
        == dispatched["run"]["run_id"]
    )
    assert server.call("get_shared_state", {"controller_id": cid})["ok"]
    assert (
        server.call(
            "verify_provenance_contract",
            {"controller_id": cid, "records": [], "required_schema_version": "v1"},
        )["decision"]["accepted"]
        is True
    )
    failure = server.call(
        "record_controller_stage_failure", {"controller_id": cid, "reason": "simulated"}
    )
    assert failure["controller"]["phase"] == "repair-required"
    assert server.call("complete_controller", {"controller_id": cid})["ok"] is False
    assert (
        server.call("cancel_controller", {"controller_id": cid, "reason": "done"})["controller"][
            "phase"
        ]
        == "cancelled"
    )
    assert server.call("get_controller_state", {"controller_id": "controller-404"})["ok"] is False


def _dispatched_controller(server, snapshot_id, *, depth, nodes=2):
    plan = {**_plan_payload(), "max_elastic_depth": depth, "max_elastic_nodes": nodes}
    controller = server.call(
        "create_controller",
        {
            "snapshot": {"snapshot_id": snapshot_id, "version": "1", "content_hash": "h"},
            "profile": {"stage": "design", "source_snapshot_id": snapshot_id},
            "routing_rules": {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 5},
            "gap_metadata": {},
            "max_repair_attempts": 1,
        },
    )
    cid = controller["controller"]["controller_id"]
    server.call("submit_controller_plan", {"controller_id": cid, "plan": plan})
    server.call("approve_controller_plan", {"controller_id": cid, "approved": True})
    assert server.call("dispatch_controller", {"controller_id": cid})["ok"]
    return cid


ELASTIC_RESULT = {
    "status": "completed",
    "output": {"v": 1},
    "spawn_requests": [
        {
            "request_id": "a",
            "scope": "the clock tree",
            "instructions": "Trace the clock tree.",
            "reason": "The section is ambiguous.",
        }
    ],
}


def test_a_deferred_elastic_batch_is_resolved_by_a_capacity_grant_over_the_wire(server):
    cid = _dispatched_controller(server, "snap-elastic-grant", depth=0)
    recorded = server.call(
        "record_controller_node_result",
        {"controller_id": cid, "node_id": "node:T1", "result": ELASTIC_RESULT},
    )
    graph = recorded["run"]["graph"]
    assert recorded["ok"] and graph["statuses"]["join:node:T1"] == "blocked"
    assert [item["status"] for item in graph["spawn_records"]] == ["deferred"]
    assert graph["spawn_records"][0]["code"] == "ELASTIC_DEPTH_CAP_REACHED"
    missing = server.call("grant_elastic_capacity", {"controller_id": cid, "reason": "x"})
    assert missing["ok"] is False and "at least one of" in missing["errors"][0]["message"]
    lowered = server.call(
        "grant_elastic_capacity",
        {"controller_id": cid, "max_elastic_nodes": 1, "reason": "x"},
    )
    assert (
        lowered["ok"] is False
        and "cannot lower max_elastic_nodes" in lowered["errors"][0]["message"]
    )
    granted = server.call(
        "grant_elastic_capacity",
        {"controller_id": cid, "max_elastic_depth": 1, "reason": "designer approved"},
    )
    assert granted["ok"]
    graph = granted["run"]["graph"]
    assert graph["statuses"]["elastic:node:T1:a"] == "runnable"
    assert graph["spawn_records"][0]["status"] == "accepted"
    assert [item["reason"] for item in graph["capacity_grants"]] == ["designer approved"]
    assert server.call("get_run_state", {"run_id": granted["run"]["run_id"]})["ok"]


def test_a_deferred_elastic_batch_is_resolved_by_declining_it_over_the_wire(server):
    cid = _dispatched_controller(server, "snap-elastic-decline", depth=0)
    server.call(
        "record_controller_node_result",
        {"controller_id": cid, "node_id": "node:T1", "result": ELASTIC_RESULT},
    )
    declined = server.call(
        "decline_elastic_requests",
        {"controller_id": cid, "parent_node_id": "node:T1", "reason": "out of budget"},
    )
    assert declined["ok"]
    graph = declined["run"]["graph"]
    assert graph["statuses"]["join:node:T1"] == "completed"
    assert graph["spawn_records"][0]["status"] == "discarded"
    again = server.call(
        "decline_elastic_requests",
        {"controller_id": cid, "parent_node_id": "node:T1", "reason": "again"},
    )
    assert (
        again["ok"] is False and "has no deferred elastic requests" in again["errors"][0]["message"]
    )
    unknown = server.call(
        "grant_elastic_capacity",
        {"controller_id": "controller-404", "max_elastic_nodes": 3, "reason": "x"},
    )
    assert unknown["ok"] is False


def test_controller_ceilings_bound_the_plan_and_every_grant_over_the_wire(server):
    snapshot_id = "snap-elastic-ceiling"
    created = server.call(
        "create_controller",
        {
            "snapshot": {"snapshot_id": snapshot_id, "version": "1", "content_hash": "h"},
            "profile": {"stage": "design", "source_snapshot_id": snapshot_id},
            "routing_rules": {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 5},
            "gap_metadata": {},
            "max_repair_attempts": 1,
            "elastic_depth_ceiling": 1,
            "elastic_nodes_ceiling": 3,
        },
    )
    assert created["ok"] and created["controller"]["elastic_nodes_ceiling"] == 3
    cid = created["controller"]["controller_id"]
    too_big = {**_plan_payload(), "max_elastic_depth": 0, "max_elastic_nodes": 4}
    refused = server.call("submit_controller_plan", {"controller_id": cid, "plan": too_big})
    assert refused["ok"] is False
    assert "above the ceiling max_elastic_nodes 3" in refused["errors"][0]["message"]
    plan = {**_plan_payload(), "max_elastic_depth": 0, "max_elastic_nodes": 2}
    assert server.call("submit_controller_plan", {"controller_id": cid, "plan": plan})["ok"]
    server.call("approve_controller_plan", {"controller_id": cid, "approved": True})
    assert server.call("dispatch_controller", {"controller_id": cid})["ok"]
    server.call(
        "record_controller_node_result",
        {"controller_id": cid, "node_id": "node:T1", "result": ELASTIC_RESULT},
    )
    deeper = server.call(
        "grant_elastic_capacity",
        {"controller_id": cid, "max_elastic_depth": 2, "reason": "beyond the policy"},
    )
    assert deeper["ok"] is False
    assert "max_elastic_depth 2: it is above the ceiling 1" in deeper["errors"][0]["message"]
    wider = server.call(
        "grant_elastic_capacity",
        {"controller_id": cid, "max_elastic_nodes": 4, "reason": "beyond the policy"},
    )
    assert wider["ok"] is False
    assert "max_elastic_nodes 4: it is above the ceiling 3" in wider["errors"][0]["message"]
    granted = server.call(
        "grant_elastic_capacity",
        {"controller_id": cid, "max_elastic_depth": 1, "reason": "inside the policy"},
    )
    assert granted["ok"] and granted["run"]["graph"]["elastic_depth_ceiling"] == 1


def test_an_invalid_spawn_request_is_rejected_with_the_field_that_is_wrong(server):
    cid = _dispatched_controller(server, "snap-elastic-invalid", depth=1)
    bad = {**ELASTIC_RESULT, "spawn_requests": [{"request_id": "has space", "scope": "s"}]}
    rejected = server.call(
        "record_controller_node_result",
        {"controller_id": cid, "node_id": "node:T1", "result": bad},
    )
    assert rejected["ok"] is False
    locations = {tuple(error["location"]) for error in rejected["errors"]}
    assert ("spawn_requests", 0, "request_id") in locations
    assert ("spawn_requests", 0, "instructions") in locations


def test_specification_and_gate_tools(server):
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
    trees = processed["trees"]
    selection = server.call(
        "select_task_context",
        {"trees": trees, "stage": "rtl-development", "task_text": "adder", "scope_pointers": []},
    )
    assert selection["ok"], selection
    specification = spec(reqs=[req("R1", checks=())], trees=[tree()]).model_dump(mode="json")
    gate = server.call(
        "validate_gate_one",
        {"specification": specification, "required_categories": ["functional", "ppa"]},
    )
    assert gate["ok"] and gate["summary"]["critical"] == 1 and gate["summary"]["important"] == 1
    metadata = {"version": "1.0.0", "change_kind": "major", "unified_specification_hash": "h"}
    refused = server.call(
        "soft_lock_specification",
        {
            "specification": specification,
            "dependency_graph": gate["dependency_graph"],
            "gap_report": gate["gap_report"],
            "metadata": metadata,
            "user_approved": False,
        },
    )
    assert refused["decision"]["accepted"] is False
    mismatched = server.call(
        "soft_lock_specification",
        {
            "specification": specification,
            "dependency_graph": gate["dependency_graph"],
            "gap_report": gate["gap_report"],
            "metadata": {**metadata, "version": "9.9.9"},
            "user_approved": True,
            "proceed_with_gaps": True,
        },
    )
    assert mismatched["ok"] is False
    assert mismatched["errors"][0]["message"] == (
        'Version metadata version "9.9.9" does not match the specification version "1.0.0".'
    )
    assert not (server.spec_root / "unified-specification.yaml").exists()
    accepted = server.call(
        "soft_lock_specification",
        {
            "specification": specification,
            "dependency_graph": gate["dependency_graph"],
            "gap_report": gate["gap_report"],
            "metadata": metadata,
            "user_approved": True,
            "proceed_with_gaps": True,
        },
    )
    assert (
        accepted["decision"]["accepted"] is True
        and (server.spec_root / "unified-specification.yaml").is_file()
    )


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


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


def test_git_tools_with_a_real_repository(server):
    repo = server.run_root / "specrepo"
    repo.mkdir()
    env = {"GIT_CEILING_DIRECTORIES": str(server.run_root)}
    os.environ.update(env)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    state = server.call("get_git_repository_state", {"repository_path": "specrepo"})
    assert state["ok"] and state["repository"]["clean"] is True
    for escape in ("../outside", "..", "C:\\Windows", "/etc"):
        refused = server.call("get_git_repository_state", {"repository_path": escape})
        assert refused["ok"] is False
    specification = spec(version="1.0.0", reqs=[req("R1")], trees=[tree()])
    from nailong_agent_sdk.specifications.gate import SpecificationGate
    from nailong_agent_sdk.specifications.git_versioning import _sha256

    graph, report = SpecificationGate().validate(specification, required_categories=set())
    metadata = {
        "version": "1.0.0",
        "change_kind": "major",
        "unified_specification_hash": _sha256(specification.model_dump(mode="json")),
        "soft_locked": True,
    }
    self_made_approval = {
        "approved": True,
        "approver_id": "anyone-at-all",
        "reason": "self approved",
        "action": "create-specification-lock",
        "approval_id": "made-up",
        "at_utc": "2026-01-01T00:00:00+00:00",
    }
    lock = server.call(
        "create_specification_git_lock",
        {
            "repository_path": "specrepo",
            "specification": specification.model_dump(mode="json"),
            "dependency_graph": graph.model_dump(mode="json"),
            "gap_report": report.model_dump(mode="json"),
            "metadata": metadata,
            "approval": self_made_approval,
        },
    )
    assert lock["ok"], lock
    unapproved = server.call(
        "create_specification_git_lock",
        {
            "repository_path": "specrepo",
            "specification": specification.model_dump(mode="json"),
            "dependency_graph": graph.model_dump(mode="json"),
            "gap_report": report.model_dump(mode="json"),
            "metadata": metadata,
            "approval": {**self_made_approval, "approved": False},
        },
    )
    assert (
        unapproved["ok"] is False
        and "approved create-specification-lock" in unapproved["errors"][0]["message"]
    )
    classification = server.call(
        "classify_specification_version",
        {
            "repository_path": "specrepo",
            "version": "1.0.1",
            "specification": specification.model_dump(mode="json"),
            "dependency_graph": graph.model_dump(mode="json"),
        },
    )
    assert classification["ok"] and classification["classification"]["recommended_bump"] == "patch"
    variant = server.call(
        "create_variant_worktree",
        {
            "repository_path": "specrepo",
            "name": "var-a",
            "branch": "variant/a",
            "base_ref": "v1.0.0",
            "specification_tag": "v1.0.0",
            "purpose": "test",
            "approval": {**self_made_approval, "action": "create-variant-worktree"},
        },
    )
    assert variant["ok"], variant


def test_orchestration_tools(server):
    policy = {
        "policy_id": "mcp-pol",
        "routing_rules": {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 3},
        "skills": [],
        "models": [
            {
                "model_key": "m1",
                "binding": {"provider": "fake", "model": "fake-1"},
                "allowed_tiers": ["cheap", "standard", "strong"],
            }
        ],
        "profiles": [
            {
                "profile_id": "p1",
                "stage": "design",
                "role": "worker",
                "allowed_model_keys": ["m1"],
                "max_instances": 8,
                "capability_grant": {"role": "worker", "capabilities": [], "allowed_paths": []},
            }
        ],
    }
    request = {
        "request_id": "req-mcp",
        "stage": "design",
        "snapshot": {"snapshot_id": "snap-orch", "version": "1", "content_hash": "h"},
        "gap_metadata": {"blast_radius": 9},
        "plan": _plan_payload(),
    }
    prepared = server.call("prepare_orchestration", {"policy": policy, "request": request})
    assert prepared["ok"], prepared
    oid = prepared["orchestration"]["orchestration_id"]
    assert (
        server.call("submit_orchestration_for_approval", {"orchestration_id": oid})[
            "orchestration"
        ]["status"]
        == "awaiting-plan-approval"
    )
    assert (
        server.call("approve_orchestration", {"orchestration_id": oid, "approved": True})[
            "orchestration"
        ]["status"]
        == "approved"
    )
    assert server.call("get_orchestration", {"orchestration_id": oid})["orchestration"][
        "controller_id"
    ]
    assert (
        server.call("cancel_orchestration", {"orchestration_id": oid, "reason": "done"})[
            "orchestration"
        ]["status"]
        == "cancelled"
    )
    assert (
        server.call("get_orchestration", {"orchestration_id": "orchestration-404"})["ok"] is False
    )


def test_path_traversal_through_run_and_controller_ids_does_not_leak_files(server):
    outside = server.run_root.parent / "outside_secret.json"
    outside.write_text('{"api_key": "TOP-SECRET-MCP-LEAK", "unexpected": true}', "utf-8")
    leaks = {}
    for tool_name, argument in (
        ("get_run_state", "run_id"),
        ("resume_run", "run_id"),
        ("get_controller_state", "controller_id"),
        ("get_orchestration", "orchestration_id"),
    ):
        result = server.call(tool_name, {argument: "../../outside_secret"})
        blob = json.dumps(result)
        leaks[tool_name] = "TOP-SECRET-MCP-LEAK" in blob
    assert not any(leaks.values()), leaks


def _decision_call(decision_id):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "record_human_project_decision",
                "arguments": {
                    "project_id": "authority",
                    "decision_id": decision_id,
                    "content": "ship it",
                    "status": "locked",
                    "evidence_id": "anything",
                },
            },
        }
    ).encode()


def test_unauthenticated_clients_cannot_assert_human_authority(server):
    server.call(
        "initialize_project_state",
        {"project_id": "authority", "stage_schema": {"schema_id": "s", "stage": "design"}},
    )
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    attempts = {
        "no token": {},
        "wrong token": {"Authorization": "Bearer " + "x" * len(server.token)},
        "other scheme": {"Authorization": f"Basic {server.token}"},
        "token without scheme": {"Authorization": server.token},
        "empty bearer": {"Authorization": "Bearer "},
    }
    refused = {
        label: raw_post(server, _decision_call(f"forged-{index}"), {**base, **extra}, False)
        for index, (label, extra) in enumerate(attempts.items())
    }
    assert {label: status for label, (status, _raw) in refused.items()} == {
        label: 401 for label in attempts
    }
    assert all(
        b"Unauthorized" in raw and b"bearer token" in raw for _status, raw in refused.values()
    )
    state = server.call("get_project_state", {"project_id": "authority"})["state"]
    assert state["decisions"] == [] and state["revision"] == 0
    status, raw = raw_post(server, _decision_call("real"), base, limit=1_000_000)
    assert status == 200 and json.loads(raw)["result"]["structuredContent"]["ok"] is True
    state = server.call("get_project_state", {"project_id": "authority"})["state"]
    assert [d["decision_id"] for d in state["decisions"]] == ["real"]


def test_dns_rebinding_and_cross_origin_requests_are_refused(server):
    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
    ).encode()
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    good = raw_post(server, init, {**base, "Host": f"127.0.0.1:{server.port}"})
    rebound = raw_post(server, init, {**base, "Host": "attacker.example.com"})
    foreign_origin = raw_post(
        server,
        init,
        {**base, "Host": f"127.0.0.1:{server.port}", "Origin": "https://attacker.example.com"},
    )
    assert good[0] == 200
    assert rebound[0] in {400, 403, 421}, (
        f"Host: attacker.example.com was served with HTTP {rebound[0]}"
    )
    assert foreign_origin[0] in {400, 403}, (
        f"Origin: https://attacker.example.com was served with HTTP {foreign_origin[0]}"
    )


def test_the_running_server_announces_the_package_name_and_version(server):
    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": f"127.0.0.1:{server.port}",
    }
    status, raw = raw_post(server, init, headers, limit=100_000)
    assert status == 200
    info = json.loads(raw)["result"]["serverInfo"]
    assert (info["name"], info["version"]) == (
        "nailong-agent-sdk",
        importlib.metadata.version("nailong-agent-sdk"),
    )


def test_malformed_and_oversized_requests_leave_the_server_healthy(server):
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    raw_post(server, b"{not json", base)
    raw_post(server, b"{}", {"Content-Type": "text/plain", "Accept": "application/json"})
    raw_post(server, b"[" * 100_000 + b"]" * 100_000, base)
    big = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "validate_plan", "arguments": {"plan": {"x": "y" * 20_000_000}}},
        }
    ).encode()
    raw_post(server, big, base)
    assert server.call("list_metric_definitions")["ok"] is True
    assert server.process.poll() is None


def test_one_slow_tool_does_not_stall_every_other_client(server):
    (server.spec_root / "big.csv").write_text(
        "h\n" + "\n".join(f"row,{i},{'x' * 40}" for i in range(400_000)), "utf-8"
    )
    (server.spec_root / "big-manifest.yaml").write_text(
        "documents:\n  - {id: big, title: Big, format: csv, path: big.csv, category: functional}\n",
        "utf-8",
    )

    async def scenario():
        async def slow():
            async with server.session() as session:
                started = time.monotonic()
                await session.call_tool(
                    "process_specification_manifest", {"manifest_path": "big-manifest.yaml"}
                )
                return time.monotonic() - started

        async def probe():
            await asyncio.sleep(1.0)
            started = time.monotonic()
            async with server.session() as session:
                await session.call_tool("list_metric_definitions", {})
                return time.monotonic() - started

        return await asyncio.gather(slow(), probe())

    slow_seconds, probe_seconds = asyncio.run(scenario())
    assert probe_seconds < 3.0, (
        f"a trivial call took {probe_seconds:.1f}s while a {slow_seconds:.1f}s tool ran"
    )


def test_parallel_calls_are_consistent(server):
    async def many():
        async def one(index):
            async with server.session() as session:
                result = await session.call_tool("validate_plan", {"plan": _plan_payload()})
                return result.structured_content["report"]["valid"]

        return await asyncio.gather(*(one(i) for i in range(20)))

    assert all(asyncio.run(many()))


def test_claim_1_one_writer_over_the_run_root_run_state_and_project_state_survive_polling(server):
    plan = _plan_payload()
    snapshot = {"snapshot_id": "snap-claim1", "version": "1", "content_hash": "h"}
    profile = {"stage": "design", "source_snapshot_id": "snap-claim1"}
    rules = {"multi_agent_min_categories": 3, "multi_agent_min_blast_radius": 5}
    created = server.call(
        "create_controller",
        {
            "snapshot": snapshot,
            "profile": profile,
            "routing_rules": rules,
            "gap_metadata": {},
            "max_repair_attempts": 1,
        },
    )["controller"]
    cid, project_id = created["controller_id"], created["project_state_id"]
    server.call("submit_controller_plan", {"controller_id": cid, "plan": plan})
    server.call("approve_controller_plan", {"controller_id": cid, "approved": True, "reason": "ok"})
    run_id = server.call("dispatch_controller", {"controller_id": cid})["run"]["run_id"]
    decision = server.call(
        "record_human_project_decision",
        {
            "project_id": project_id,
            "decision_id": "d-locked",
            "content": "keep",
            "status": "locked",
            "evidence_id": "ev",
        },
    )
    assert decision["ok"], decision
    recorded = server.call(
        "record_controller_node_result",
        {
            "controller_id": cid,
            "node_id": "node:T1",
            "result": {"status": "completed", "output": {"v": 1}},
        },
    )
    assert recorded["ok"], recorded
    polls = [
        server.call("get_run_state", {"run_id": run_id})["run"]["graph"]["statuses"]["node:T1"]
        for _ in range(4)
    ]
    state = server.call("get_project_state", {"project_id": project_id})["state"]
    decisions = [d["decision_id"] for d in state["decisions"]]
    work_items = sorted(w["work_item_id"] for w in state["work_items"])
    assert polls == ["completed"] * 4
    assert "d-locked" in decisions and "node:T1" in work_items, (decisions, work_items)
