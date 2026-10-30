import asyncio
import importlib.metadata
import json
import time

from tests.support.mcp_cases import plan_payload
from tests.support.mcp_server import raw_post


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
    }, refused
    assert all(
        b"Unauthorized" in raw and b"bearer token" in raw for _status, raw in refused.values()
    )
    state = server.call("get_project_state", {"project_id": "authority"})["state"]
    assert state["decisions"] == [] and state["revision"] == 0
    status, raw = raw_post(server, _decision_call("real"), base, limit=1_000_000)
    assert status == 200 and json.loads(raw)["result"]["structuredContent"]["ok"] is True
    state = server.call("get_project_state", {"project_id": "authority"})["state"]
    assert [d["decision_id"] for d in state["decisions"]] == ["real"]


def test_a_refusal_reaches_a_client_whose_request_body_arrives_late(server):
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    delays = (0.0, 0.005, 0.05, 0.25)
    outcomes = [
        raw_post(server, _decision_call(f"late-{index}"), base, False, body_delay=delay)
        for index, delay in enumerate(delays)
    ]
    assert [status for status, _raw in outcomes] == [401] * len(delays), outcomes


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
                result = await session.call_tool("validate_plan", {"plan": plan_payload()})
                return result.structured_content["report"]["valid"]

        return await asyncio.gather(*(one(i) for i in range(20)))

    assert all(asyncio.run(many()))


def test_claim_1_one_writer_over_the_run_root_run_state_and_project_state_survive_polling(server):
    plan = plan_payload()
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
