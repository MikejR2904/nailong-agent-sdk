import json

import pytest

from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.coordination_records import _hash_run
from nailong_agent_sdk.state.graph_models import GraphNodeKind
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from nailong_agent_sdk.state.planning import Plan
from nailong_agent_sdk.state.run_state_store import RunStateStore
from tests.support.controllers import new_controller
from tests.support.elastic import done_with, req
from tests.support.plans import AGENT, K, P, arun, ok, proof, task

ELASTIC = GraphNodeKind.ELASTIC


def elastic_plan(depth=1, nodes=2, plan_id="plan"):
    return Plan(
        plan_id=plan_id,
        tasks=[task("T1", ["a"], [("a", P)]), task("T2", ["a"], [("a", K)], deps=["T1"])],
        dependency_proofs=[proof("T1", "T2", ["a"])],
        max_elastic_depth=depth,
        max_elastic_nodes=nodes,
    )


def explorer(requests_by_node):
    async def executor(node, context):
        requests = requests_by_node.get(node.node_id, [])
        return done_with(*requests, output=f"{node.node_id}-out")

    return {AGENT: executor, ELASTIC: executor}


def node_ids(record):
    return sorted(item["node_id"] for item in record.graph["nodes"])


def all_completed(record):
    return all(status == "completed" for status in record.graph["statuses"].values())


def test_the_plan_caps_and_the_spawn_records_are_persisted_with_the_run(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(elastic_plan(depth=2, nodes=3))
    assert (run.graph["max_elastic_depth"], run.graph["max_elastic_nodes"]) == (2, 3)
    assert run.graph["spawn_records"] == [] and run.graph["capacity_grants"] == []
    record = arun(coordinator.execute_run(run.run_id, explorer({"node:T1": [req("a")]})))
    assert all_completed(record)
    assert "elastic:node:T1:a" in node_ids(record) and "join:node:T1" in node_ids(record)
    loaded = HarnessCoordinator(tmp_path).get_run_state(run.run_id)
    assert loaded.graph["spawn_records"] == record.graph["spawn_records"]
    assert [item["status"] for item in loaded.graph["spawn_records"]] == ["accepted"]
    assert loaded.run_hash == record.run_hash


def test_a_spawn_committed_before_a_restart_runs_after_it(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(elastic_plan())
    coordinator.record_node_result(run.run_id, "node:T1", done_with(req("a"), output="t1"))
    restarted = HarnessCoordinator(tmp_path)
    state = restarted.get_run_state(run.run_id)
    assert state.graph["statuses"]["elastic:node:T1:a"] == "runnable"
    assert state.graph["statuses"]["join:node:T1"] == "pending"
    assert state.graph["statuses"]["node:T2"] == "pending"
    record = arun(restarted.execute_run(run.run_id, explorer({})))
    assert all_completed(record)
    started = [event["node_id"] for event in record.graph["events"] if event["status"] == "running"]
    assert started.index("join:node:T1") < started.index("node:T2")


def test_a_deferred_batch_waits_for_a_grant_through_the_coordinator(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(elastic_plan(depth=0))
    held = coordinator.record_node_result(run.run_id, "node:T1", done_with(req("a")))
    assert held.graph["statuses"]["join:node:T1"] == "blocked"
    assert held.graph["statuses"]["node:T2"] == "blocked"
    assert [item["status"] for item in held.graph["spawn_records"]] == ["deferred"]
    assert coordinator.resume_run(run.run_id).graph["statuses"]["join:node:T1"] == "blocked"
    idle = arun(coordinator.execute_run(run.run_id, explorer({})))
    assert idle.graph["statuses"]["join:node:T1"] == "blocked"
    granted = coordinator.grant_elastic_capacity(
        run.run_id, max_elastic_depth=1, reason="designer approved the exploration"
    )
    assert granted.graph["max_elastic_depth"] == 1
    assert [item["reason"] for item in granted.graph["capacity_grants"]] == [
        "designer approved the exploration"
    ]
    assert granted.graph["statuses"]["elastic:node:T1:a"] == "runnable"
    assert granted.graph["spawn_records"][0]["status"] == "accepted"
    restarted = HarnessCoordinator(tmp_path)
    record = arun(restarted.execute_run(run.run_id, explorer({})))
    assert all_completed(record)
    assert (
        restarted.get_run_state(run.run_id).graph["capacity_grants"]
        == granted.graph["capacity_grants"]
    )


def test_a_run_held_for_a_capacity_decision_survives_a_restart_before_the_decision(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(elastic_plan(depth=0))
    held = coordinator.record_node_result(run.run_id, "node:T1", done_with(req("a")))
    restarted = HarnessCoordinator(tmp_path)
    reloaded = restarted.get_run_state(run.run_id)
    assert reloaded.graph["statuses"] == held.graph["statuses"]
    assert reloaded.graph["statuses"]["join:node:T1"] == "blocked"
    granted = restarted.grant_elastic_capacity(
        run.run_id, max_elastic_depth=1, reason="designer approved"
    )
    assert granted.graph["statuses"]["elastic:node:T1:a"] == "runnable"
    declining = HarnessCoordinator(tmp_path / "other")
    other = declining.start_run(elastic_plan(depth=0))
    declining.record_node_result(other.run_id, "node:T1", done_with(req("a")))
    after_restart = HarnessCoordinator(tmp_path / "other")
    declined = after_restart.decline_elastic_requests(other.run_id, "node:T1", "out of budget")
    assert declined.graph["statuses"]["join:node:T1"] == "completed"
    assert HarnessCoordinator(tmp_path / "other").get_run_state(other.run_id).run_hash == (
        declined.run_hash
    )


def test_declining_through_the_coordinator_releases_the_held_dependents(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(elastic_plan(depth=0))
    coordinator.record_node_result(run.run_id, "node:T1", done_with(req("a")))
    declined = coordinator.decline_elastic_requests(
        run.run_id, "node:T1", "exploration is out of budget"
    )
    assert declined.graph["statuses"]["join:node:T1"] == "completed"
    assert declined.graph["spawn_records"][0]["status"] == "discarded"
    record = arun(coordinator.execute_run(run.run_id, explorer({})))
    assert all_completed(record)
    assert "elastic:node:T1:a" not in node_ids(record)


def test_cancelling_a_run_with_pending_exploration_leaves_nothing_runnable(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(elastic_plan(nodes=3))
    coordinator.record_node_result(run.run_id, "node:T1", done_with(req("a"), req("b")))
    cancelled = coordinator.cancel_run(run.run_id)
    statuses = cancelled.graph["statuses"]
    assert statuses["elastic:node:T1:a"] == "cancelled"
    assert statuses["elastic:node:T1:b"] == "cancelled"
    assert statuses["join:node:T1"] == "blocked" and statuses["node:T2"] == "blocked"
    assert "runnable" not in statuses.values() and cancelled.cancelled is True
    assert arun(coordinator.execute_run(run.run_id, explorer({}))).graph["statuses"] == statuses


def test_coordinator_decisions_name_what_is_wrong(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    with pytest.raises(ValueError, match='Run "run-9" is unknown'):
        coordinator.grant_elastic_capacity("run-9", max_elastic_nodes=3, reason="x")
    with pytest.raises(ValueError, match='Run "run-9" is unknown'):
        coordinator.decline_elastic_requests("run-9", "node:T1", "x")
    run = coordinator.start_run(elastic_plan())
    with pytest.raises(ValueError, match='"node:T1" has no deferred elastic requests'):
        coordinator.decline_elastic_requests(run.run_id, "node:T1", "nothing to decline")
    with pytest.raises(ValueError, match="cannot lower max_elastic_nodes from 2 to 1"):
        coordinator.grant_elastic_capacity(run.run_id, max_elastic_nodes=1, reason="x")
    assert coordinator.get_run_state(run.run_id).graph["capacity_grants"] == []


def dispatched(tmp_path, plan, telemetry=None):
    runtime = ControllerRuntime(tmp_path, telemetry=telemetry)
    controller = new_controller(runtime)
    runtime.submit_plan(controller.controller_id, plan)
    runtime.approve_plan(controller.controller_id, True)
    runtime.dispatch(controller.controller_id)
    return runtime, controller.controller_id


def spawn_events(telemetry, runtime, controller_id):
    run_id = runtime.get_controller(controller_id).run_id
    events = telemetry.list_events(run_id, limit=500)
    return [event for event in events if event.event_type == "graph.elastic-spawn"]


def test_a_controller_run_with_elastic_exploration_completes_and_reports_it(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(depth=2, nodes=3), telemetry)
        run = arun(runtime.execute_graph(cid, explorer({"node:T1": [req("a"), req("b")]})))
        assert all_completed(run)
        items = {w.work_item_id: w.status.value for w in runtime.project_state(cid).work_items}
        assert items["elastic:node:T1:a"] == "completed" and items["join:node:T1"] == "completed"
        assert runtime.complete(cid).phase is ControllerPhase.COMPLETED
        events = spawn_events(telemetry, runtime, cid)
        assert [(e.status, e.payload["request_id"]) for e in events] == [
            ("accepted", "a"),
            ("accepted", "b"),
        ]
        assert events[0].payload == {
            "sequence": 1,
            "parent_node_id": "node:T1",
            "request_id": "a",
            "depth": 1,
            "child_node_id": "elastic:node:T1:a",
            "join_node_id": "join:node:T1",
            "code": None,
        }
    finally:
        telemetry.close()


def test_a_run_stored_before_elastic_nodes_existed_still_executes_and_reports(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(), telemetry)
        run_id = runtime.get_controller(cid).run_id
        store = RunStateStore(tmp_path)
        stored = store.load(run_id)
        graph = json.loads(json.dumps(stored.graph))
        graph.pop("spawn_records")
        graph.pop("capacity_grants")
        for item in graph["nodes"]:
            item.pop("elastic")
        store.save(
            stored.model_copy(
                update={
                    "graph": graph,
                    "run_hash": _hash_run(
                        stored.run_id,
                        stored.plan_id,
                        graph,
                        stored.plan_validation,
                        stored.cancelled,
                    ),
                }
            )
        )
        fresh = ControllerRuntime(tmp_path, telemetry=telemetry)
        run = arun(fresh.execute_graph(cid, explorer({"node:T1": [req("a")]})))
        assert all_completed(run)
        assert [e.status for e in spawn_events(telemetry, fresh, cid)] == ["accepted"]
        assert fresh.complete(cid).phase is ControllerPhase.COMPLETED
    finally:
        telemetry.close()


def test_each_spawn_record_is_reported_once_even_when_execution_is_repeated(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(), telemetry)
        arun(runtime.execute_graph(cid, explorer({"node:T1": [req("a")]})))
        arun(runtime.execute_graph(cid, explorer({})))
        assert len(spawn_events(telemetry, runtime, cid)) == 1
    finally:
        telemetry.close()


def test_a_deferred_batch_blocks_completion_until_the_controller_grants_capacity(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(depth=0), telemetry)
        arun(runtime.execute_graph(cid, explorer({"node:T1": [req("a")]})))
        assert runtime.get_controller(cid).phase is ControllerPhase.EXECUTING
        with pytest.raises(ValueError, match=r"join:node:T1 \(blocked\)") as unfinished:
            runtime.complete(cid)
        assert "node:T2 (blocked)" in str(unfinished.value)
        (deferred,) = spawn_events(telemetry, runtime, cid)
        assert deferred.status == "deferred"
        assert deferred.payload["code"] == "ELASTIC_DEPTH_CAP_REACHED"
        assert deferred.payload["child_node_id"] is None
        assert deferred.payload["join_node_id"] == "join:node:T1"
        granted = runtime.grant_elastic_capacity(
            cid, max_elastic_depth=1, reason="designer approved the exploration"
        )
        assert granted.graph["statuses"]["elastic:node:T1:a"] == "runnable"
        run_id = runtime.get_controller(cid).run_id
        events = telemetry.list_events(run_id, limit=500)
        grant_event = next(e for e in events if e.event_type == "graph.elastic-capacity-granted")
        assert grant_event.status == "granted"
        assert grant_event.payload == {
            "sequence": 1,
            "previous_max_depth": 0,
            "max_depth": 1,
            "previous_max_nodes": 2,
            "max_nodes": 2,
            "reason": "designer approved the exploration",
        }
        assert [e.status for e in spawn_events(telemetry, runtime, cid)] == [
            "deferred",
            "accepted",
        ]
        run = arun(runtime.execute_graph(cid, explorer({})))
        assert all_completed(run)
        assert runtime.complete(cid).phase is ControllerPhase.COMPLETED
    finally:
        telemetry.close()


def test_declining_deferred_requests_lets_the_controller_complete(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(depth=0), telemetry)
        arun(runtime.execute_graph(cid, explorer({"node:T1": [req("a")]})))
        declined = runtime.decline_elastic_requests(cid, "node:T1", "out of budget")
        assert declined.graph["statuses"]["join:node:T1"] == "completed"
        run_id = runtime.get_controller(cid).run_id
        events = telemetry.list_events(run_id, limit=500)
        event = next(e for e in events if e.event_type == "graph.elastic-requests-declined")
        assert event.status == "declined"
        assert event.payload == {
            "parent_node_id": "node:T1",
            "request_ids": ["a"],
            "reason": "out of budget",
        }
        run = arun(runtime.execute_graph(cid, explorer({})))
        assert all_completed(run)
        assert runtime.complete(cid).phase is ControllerPhase.COMPLETED
    finally:
        telemetry.close()


def test_controller_decisions_need_a_dispatched_run(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    controller = new_controller(runtime)
    with pytest.raises(ValueError, match="require a dispatched graph run"):
        runtime.grant_elastic_capacity(controller.controller_id, max_elastic_nodes=3, reason="x")
    with pytest.raises(ValueError, match="require a dispatched graph run"):
        runtime.decline_elastic_requests(controller.controller_id, "node:T1", "x")


def test_a_result_with_requests_recorded_through_the_controller_spawns_and_reports(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(), telemetry)
        runtime.record_node_result(cid, "node:T1", done_with(req("a"), output="t1"))
        events = spawn_events(telemetry, runtime, cid)
        assert [(e.status, e.payload["child_node_id"]) for e in events] == [
            ("accepted", "elastic:node:T1:a")
        ]
    finally:
        telemetry.close()


def test_successful_nodes_without_requests_report_nothing_elastic(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = dispatched(tmp_path, elastic_plan(), telemetry)

        async def plain(node, context):
            return ok(node.node_id)

        arun(runtime.execute_graph(cid, {AGENT: plain}))
        assert spawn_events(telemetry, runtime, cid) == []
    finally:
        telemetry.close()
