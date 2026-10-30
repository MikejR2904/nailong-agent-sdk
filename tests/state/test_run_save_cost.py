import asyncio

from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import (
    GraphEdge,
    GraphEvent,
    GraphNode,
    GraphNodeResult,
    GraphNodeStatus,
)
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.run_state_store import RunStateStore
from tests.support.plans import AGENT, arun, chain_plan, n, ok, run_ok, simple_plan


def counting_saves(monkeypatch):
    saves = []
    real = RunStateStore.save

    def save(self, record):
        saves.append(sorted(record.graph["statuses"].items()))
        return real(self, record)

    monkeypatch.setattr(RunStateStore, "save", save)
    return saves


def counting_dumps(monkeypatch):
    dumped = []
    for model in (GraphNode, GraphEdge, GraphEvent, GraphNodeResult):
        real = model.model_dump

        def counting(self, *args, _real=real, **kwargs):
            dumped.append(type(self).__name__)
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(model, "model_dump", counting)
    return dumped


def test_a_chain_is_saved_once_per_node_and_once_more_at_the_end(tmp_path, monkeypatch):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(chain_plan(25, plan_id="chain")).run_id
    saves = counting_saves(monkeypatch)
    final = arun(coordinator.execute_run(run_id, {AGENT: run_ok}))
    assert len(saves) == 26
    assert set(final.graph["statuses"].values()) == {"completed"}
    assert HarnessCoordinator(tmp_path).get_run_state(run_id).run_hash == final.run_hash


def test_independent_nodes_share_the_saves_of_their_wave(tmp_path, monkeypatch):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    saves = counting_saves(monkeypatch)
    arun(coordinator.execute_run(run_id, {AGENT: run_ok}))
    assert len(saves) == 3


def test_a_crash_keeps_the_finished_waves_and_leaves_the_running_one_to_recovery(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(chain_plan(4, plan_id="chain")).run_id

    async def crash_at_the_third_node(node, context):
        if node.node_id == "node:T0002":
            raise asyncio.CancelledError()
        return ok()

    async def scenario():
        try:
            await coordinator.execute_run(run_id, {AGENT: crash_at_the_third_node})
        except asyncio.CancelledError:
            return "cancelled"

    arun(scenario())
    statuses = HarnessCoordinator(tmp_path).get_run_state(run_id).graph["statuses"]
    assert statuses == {
        "node:T0000": "completed",
        "node:T0001": "completed",
        "node:T0002": "running",
        "node:T0003": "pending",
    }


def test_the_results_of_a_wave_reach_the_disk_before_the_next_wave_runs(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    seen = {}

    async def watch(node, context):
        if node.node_id == "node:T2":
            seen.update(HarnessCoordinator(tmp_path).get_run_state(run_id).graph["statuses"])
        return ok()

    arun(coordinator.execute_run(run_id, {AGENT: watch}))
    assert seen == {"node:T1": "completed", "node:T2": "running", "node:T3": "completed"}


def test_a_snapshot_dumps_only_what_changed_since_the_previous_one(
    plain_graph_snapshots, monkeypatch
):
    graph = StateGraph([n("a"), n("b", ["a"]), n("c", ["b"])])
    graph.snapshot()
    dumped = counting_dumps(monkeypatch)
    graph.snapshot()
    assert dumped == []
    events_before = len(graph.events)
    graph.start_runnable_wave(max_parallelism=4)
    graph.snapshot()
    assert dumped == ["GraphEvent"] * (len(graph.events) - events_before)
    dumped.clear()
    events_before = len(graph.events)
    graph.mark_terminal("a", ok({"x": 1}))
    graph.snapshot()
    assert sorted(dumped) == ["GraphEvent"] * (len(graph.events) - events_before) + [
        "GraphNodeResult"
    ]


def test_the_containers_of_a_snapshot_are_not_shared_with_the_next_one():
    graph = StateGraph([n("a"), n("b", ["a"])])
    first = graph.snapshot()
    first["nodes"].clear()
    first["statuses"]["a"] = "tampered"
    first["results"]["a"] = {}
    second = graph.snapshot()
    assert [node["node_id"] for node in second["nodes"]] == ["a", "b"]
    assert second["statuses"]["a"] == "runnable"
    assert second["results"] == {}


def test_a_result_that_is_replaced_is_dumped_again():
    graph = StateGraph([n("a"), n("b", ["a"])])
    graph.start_runnable_wave(max_parallelism=4)
    graph.mark_terminal("a", GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason="waiting"))
    assert graph.snapshot()["results"]["a"]["reason"] == "waiting"
    graph.reopen_blocked(["a"])
    assert "a" not in graph.snapshot()["results"]
    graph.start_runnable_wave(max_parallelism=4)
    graph.mark_terminal("a", ok({"second": True}))
    assert graph.snapshot()["results"]["a"]["output"] == {"second": True}


def test_a_chain_snapshot_round_trips_after_every_wave():
    size = 40
    graph = StateGraph([n("n0")] + [n(f"n{i}", [f"n{i - 1}"]) for i in range(1, size)])
    for _ in range(size):
        for node in graph.start_runnable_wave(max_parallelism=4):
            graph.mark_terminal(node.node_id, ok(node.node_id))
        restored = StateGraph.from_snapshot(graph.snapshot())
        assert restored.snapshot() == graph.snapshot()
