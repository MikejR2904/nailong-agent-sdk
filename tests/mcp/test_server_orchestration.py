from tests.support.mcp_cases import dispatched_controller, plan_payload


def _orchestration_payloads(snapshot_id):
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
        "snapshot": {"snapshot_id": snapshot_id, "version": "1", "content_hash": "h"},
        "gap_metadata": {"blast_radius": 9},
        "plan": plan_payload(),
    }
    return policy, request


def test_orchestration_tools(server):
    policy, request = _orchestration_payloads("snap-orch")
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


def test_reconcile_controller_cancels_a_controller_whose_run_was_cancelled_behind_its_back(server):
    cid = dispatched_controller(server, "snap-reconcile-controller", depth=0)
    run_id = server.call("get_controller_state", {"controller_id": cid})["controller"]["run_id"]
    assert server.call("cancel_run", {"run_id": run_id})["ok"]
    state = server.call("get_controller_state", {"controller_id": cid})["controller"]
    assert state["phase"] == "executing"
    reconciled = server.call("reconcile_controller", {"controller_id": cid})
    assert reconciled["ok"], reconciled
    stores = [action["store"] for action in reconciled["report"]["actions"]]
    assert "controller" in stores and "project-state" in stores
    state = server.call("get_controller_state", {"controller_id": cid})["controller"]
    assert state["phase"] == "cancelled"
    again = server.call("reconcile_controller", {"controller_id": cid})
    assert again["ok"] and again["report"]["actions"] == []
    unknown = server.call("reconcile_controller", {"controller_id": "controller-404"})
    assert unknown["ok"] is False and "controller-404" in unknown["errors"][0]["message"]


def test_reconcile_orchestration_follows_a_controller_cancelled_behind_its_back(server):
    policy, request = _orchestration_payloads("snap-reconcile-orchestration")
    prepared = server.call("prepare_orchestration", {"policy": policy, "request": request})
    oid = prepared["orchestration"]["orchestration_id"]
    server.call("submit_orchestration_for_approval", {"orchestration_id": oid})
    server.call("approve_orchestration", {"orchestration_id": oid, "approved": True})
    controller_id = server.call("get_orchestration", {"orchestration_id": oid})["orchestration"][
        "controller_id"
    ]
    assert server.call("cancel_controller", {"controller_id": controller_id, "reason": "x"})["ok"]
    stored = server.call("get_orchestration", {"orchestration_id": oid})["orchestration"]
    assert stored["status"] == "approved"
    reconciled = server.call("reconcile_orchestration", {"orchestration_id": oid})
    assert reconciled["ok"], reconciled
    assert [action["store"] for action in reconciled["report"]["actions"]] == ["orchestration"]
    stored = server.call("get_orchestration", {"orchestration_id": oid})["orchestration"]
    assert stored["status"] == "cancelled"
    missing = server.call("reconcile_orchestration", {"orchestration_id": "orchestration-404"})
    assert missing["ok"] is False
