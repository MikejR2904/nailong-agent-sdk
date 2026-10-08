import json

import pytest

from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
from nailong_agent_sdk.state.graph_models import GraphNodeKind
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from nailong_agent_sdk.state.planning import Plan
from tests.support.controllers import new_controller
from tests.support.elastic import done_with, req
from tests.support.plans import AGENT, K, P, arun, ok, proof, task

ELASTIC = GraphNodeKind.ELASTIC


class Crash(BaseException):
    pass


def plan_with(depth=1, nodes=3, plan_id="plan"):
    return Plan(
        plan_id=plan_id,
        tasks=[task("T1", ["a"], [("a", P)]), task("T2", ["a"], [("a", K)], deps=["T1"])],
        dependency_proofs=[proof("T1", "T2", ["a"])],
        max_elastic_depth=depth,
        max_elastic_nodes=nodes,
    )


def executing(tmp_path, plan, telemetry=None, **options):
    runtime = ControllerRuntime(tmp_path, telemetry=telemetry)
    controller = new_controller(runtime, **options)
    runtime.submit_plan(controller.controller_id, plan)
    runtime.approve_plan(controller.controller_id, True)
    runtime.dispatch(controller.controller_id)
    return runtime, controller.controller_id


async def crashing(node, context):
    raise Crash()


async def finishing(node, context):
    return ok("done")


def interrupted(tmp_path, telemetry=None):
    runtime, cid = executing(tmp_path, plan_with(), telemetry)
    with pytest.raises(Crash):
        arun(runtime.execute_graph(cid, {AGENT: crashing, ELASTIC: crashing}))
    return cid


def test_a_controller_defaults_to_the_hard_limits_as_ceilings(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    controller = new_controller(runtime)
    assert controller.elastic_depth_ceiling == MAX_ELASTIC_DEPTH_LIMIT
    assert controller.elastic_nodes_ceiling == MAX_ELASTIC_NODES_LIMIT
    with pytest.raises(ValueError, match="elastic_nodes_ceiling"):
        new_controller(runtime, elastic_nodes_ceiling=MAX_ELASTIC_NODES_LIMIT + 1)
    with pytest.raises(ValueError, match="elastic_depth_ceiling"):
        new_controller(runtime, elastic_depth_ceiling=-1)


def test_a_controller_refuses_a_plan_above_its_ceilings_and_names_both(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    controller = new_controller(runtime, elastic_depth_ceiling=1, elastic_nodes_ceiling=3)
    cid = controller.controller_id
    with pytest.raises(
        ValueError,
        match='Plan "plan" declares max_elastic_nodes 4, above the ceiling max_elastic_nodes 3 '
        f'of controller "{cid}"',
    ):
        runtime.submit_plan(cid, plan_with(nodes=4))
    with pytest.raises(
        ValueError,
        match='Plan "plan" declares max_elastic_depth 2, above the ceiling max_elastic_depth 1 '
        f'of controller "{cid}"',
    ):
        runtime.submit_plan(cid, plan_with(depth=2))
    assert runtime.get_controller(cid).phase is ControllerPhase.PLANNING
    submitted = runtime.submit_plan(cid, plan_with(nodes=3))
    assert submitted.phase is ControllerPhase.AWAITING_PLAN_APPROVAL


def test_the_ceilings_travel_from_the_controller_into_the_run_and_bound_every_grant(tmp_path):
    runtime, cid = executing(
        tmp_path, plan_with(nodes=2), elastic_depth_ceiling=2, elastic_nodes_ceiling=5
    )
    run_id = runtime.get_controller(cid).run_id
    graph = HarnessCoordinator(tmp_path).get_run_state(run_id).graph
    assert (graph["elastic_depth_ceiling"], graph["elastic_nodes_ceiling"]) == (2, 5)
    with pytest.raises(ValueError, match="max_elastic_nodes 6: it is above the ceiling 5"):
        runtime.grant_elastic_capacity(cid, max_elastic_nodes=6, reason="beyond the policy")
    granted = runtime.grant_elastic_capacity(cid, max_elastic_nodes=5, reason="to the ceiling")
    assert granted.graph["max_elastic_nodes"] == 5


def test_controller_records_written_before_ceilings_existed_load_with_the_hard_limits(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    cid = new_controller(runtime, elastic_nodes_ceiling=4).controller_id
    path = tmp_path / ".agent-controllers" / f"{cid}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("elastic_depth_ceiling")
    payload.pop("elastic_nodes_ceiling")
    path.write_text(json.dumps(payload), encoding="utf-8")
    record = ControllerRuntime(tmp_path).get_controller(cid)
    assert record.elastic_depth_ceiling == MAX_ELASTIC_DEPTH_LIMIT
    assert record.elastic_nodes_ceiling == MAX_ELASTIC_NODES_LIMIT


def test_a_crashed_controller_run_recovers_replayable_nodes_and_reports_it(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        cid = interrupted(tmp_path, telemetry)
        fresh = ControllerRuntime(tmp_path, telemetry=telemetry)
        run_id = fresh.get_controller(cid).run_id
        assert fresh._harness.get_run_state(run_id).graph["statuses"]["node:T1"] == "running"
        calls = []

        def policy(node, context):
            calls.append(node.node_id)
            return True

        recovered = fresh.recover_interrupted_graph(cid, is_replayable=policy)
        assert calls == ["node:T1"]
        assert recovered.graph["statuses"]["node:T1"] == "runnable"
        events = [
            event
            for event in telemetry.list_events(run_id, limit=500)
            if event.event_type == "graph.interrupted-recovered"
        ]
        assert [(e.status, e.payload) for e in events] == [
            ("recovered", {"replayed_node_ids": ["node:T1"], "failed_node_ids": []})
        ]
        done = arun(fresh.execute_graph(cid, {AGENT: finishing, ELASTIC: finishing}))
        assert set(done.graph["statuses"].values()) == {"completed"}
    finally:
        telemetry.close()


def test_recovery_fails_what_is_not_replayable_and_requires_an_executing_run(tmp_path):
    cid = interrupted(tmp_path)
    fresh = ControllerRuntime(tmp_path)
    recovered = fresh.recover_interrupted_graph(cid)
    assert recovered.graph["statuses"]["node:T1"] == "failed"
    assert recovered.graph["results"]["node:T1"]["diagnostics"] == ["interrupted-non-idempotent"]
    undispatched = ControllerRuntime(tmp_path / "undispatched")
    waiting = new_controller(undispatched).controller_id
    with pytest.raises(ValueError, match="requires a dispatched graph run"):
        undispatched.recover_interrupted_graph(waiting)
    runtime, finished = executing(tmp_path / "finished", plan_with())
    arun(runtime.execute_graph(finished, {AGENT: finishing, ELASTIC: finishing}))
    runtime.complete(finished)
    with pytest.raises(
        ValueError,
        match=f'requires an executing controller; "{finished}" is in phase "completed"',
    ):
        runtime.recover_interrupted_graph(finished)


def test_the_node_cap_counts_the_join_through_the_coordinator(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(plan_with(nodes=2))
    held = coordinator.record_node_result(run.run_id, "node:T1", done_with(req("a"), req("b")))
    assert [item["status"] for item in held.graph["spawn_records"]] == ["deferred"] * 2
    assert held.graph["statuses"]["join:node:T1"] == "blocked"
    granted = coordinator.grant_elastic_capacity(run.run_id, max_elastic_nodes=3, reason="join")
    assert [item["status"] for item in granted.graph["spawn_records"]] == ["accepted"] * 2
