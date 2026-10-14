from tests.support.mcp_cases import dispatched_controller, plan_payload


def test_run_and_controller_tools_end_to_end(server):
    plan = plan_payload()
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
    cid = dispatched_controller(server, "snap-elastic-grant", depth=0)
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
    cid = dispatched_controller(server, "snap-elastic-decline", depth=0)
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
    too_big = {**plan_payload(), "max_elastic_depth": 0, "max_elastic_nodes": 4}
    refused = server.call("submit_controller_plan", {"controller_id": cid, "plan": too_big})
    assert refused["ok"] is False
    assert "above the ceiling max_elastic_nodes 3" in refused["errors"][0]["message"]
    plan = {**plan_payload(), "max_elastic_depth": 0, "max_elastic_nodes": 2}
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
    cid = dispatched_controller(server, "snap-elastic-invalid", depth=1)
    bad = {**ELASTIC_RESULT, "spawn_requests": [{"request_id": "has space", "scope": "s"}]}
    rejected = server.call(
        "record_controller_node_result",
        {"controller_id": cid, "node_id": "node:T1", "result": bad},
    )
    assert rejected["ok"] is False
    locations = {tuple(error["location"]) for error in rejected["errors"]}
    assert ("spawn_requests", 0, "request_id") in locations
    assert ("spawn_requests", 0, "instructions") in locations
