import asyncio
import json
import time

import pytest

from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import (
    GraphEdge,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
    GraphStateConflictKind,
)
from nailong_agent_sdk.state.shared_state import (
    DiscoveryRouteStatus,
    DiscoveryRoutingRefs,
    ExploratoryDiscovery,
    LateralDependencyRequest,
    SharedStateWrite,
    SharedSubstrateSnapshot,
)
from tests.support.payloads import nested
from tests.support.plans import A, B, F, arun, n, ok, run_ok


def test_graph_basic_waves_failure_blocking_and_commit_order():
    graph = StateGraph([n("a"), n("b"), n("c", ["a", "b"]), n("d", ["c"])])
    assert [x.node_id for x in graph.runnable()] == ["a", "b"]
    wave = graph.start_runnable_wave(max_parallelism=1)
    assert [x.node_id for x in wave] == ["a"] and graph.status("a") is GraphNodeStatus.RUNNING
    with pytest.raises(ValueError, match="max_parallelism"):
        graph.start_runnable_wave(max_parallelism=0)
    with pytest.raises(ValueError, match='Node "a" is not runnable'):
        graph.mark_started("a")
    graph.mark_terminal("a", ok())
    assert [x.node_id for x in graph.runnable()] == ["b"]
    graph.mark_started("b")
    graph.mark_terminal("b", GraphNodeResult(status=F, reason="boom"))
    assert graph.status("c") is B and graph.status("d") is B
    assert 'Dependency "b" did not complete' in graph.result("c").reason
    assert graph.runnable() == []
    with pytest.raises(ValueError, match="results must be terminal"):
        GraphNodeResult(status=GraphNodeStatus.RUNNING)


def test_graph_construction_validation():
    with pytest.raises(ValueError, match="unique"):
        StateGraph([n("a"), n("a")])
    with pytest.raises(ValueError, match="unknown dependencies"):
        StateGraph([n("a", ["zzz"])])
    with pytest.raises(ValueError, match="dependency cycle at node"):
        StateGraph([n("a", ["b"]), n("b", ["a"])])
    with pytest.raises(ValueError, match="distinct"):
        GraphEdge(parent_node_id="a", child_node_id="a")
    with pytest.raises(ValueError, match="cannot depend on itself"):
        n("a", ["a"])
    with pytest.raises(ValueError, match="Every graph edge"):
        StateGraph([n("a")], [GraphEdge(parent_node_id="a", child_node_id="ghost")])


def test_execute_runs_waves_with_dependency_results_only():
    graph = StateGraph([n("a"), n("b"), n("c", ["a", "b"])])
    results = arun(graph.execute({A: run_ok}))
    assert results["c"].output == {"node": "c", "deps": ["a", "b"]}
    assert [e.status.value for e in graph.events if e.node_id == "c"] == [
        "runnable",
        "running",
        "completed",
    ]

    async def raising(node, context):
        raise KeyError("missing-key")

    failing = StateGraph([n("a", kind=GraphNodeKind.GATE)])
    out = arun(failing.execute({GraphNodeKind.GATE: raising}))
    assert (
        out["a"].status is F
        and "executor raised KeyError" in out["a"].reason
        and '"a"' in out["a"].reason
    )
    nothing = StateGraph([n("a", kind=GraphNodeKind.FUNCTION)])
    missing = arun(nothing.execute({A: run_ok}))
    assert 'No executor is registered for node kind "pure-function"' in missing["a"].reason


def test_execute_has_no_parallelism_cap():
    peak = {"now": 0, "max": 0}

    async def slow(node, context):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(0.2)
        peak["now"] -= 1
        return ok()

    graph = StateGraph([n(f"n{i:03d}") for i in range(150)])
    arun(graph.execute({A: slow}))
    assert peak["max"] <= 32, f"StateGraph.execute ran {peak['max']} nodes concurrently with no cap"


def test_context_isolation_and_filtered_shared_state():
    snapshot = SharedSubstrateSnapshot(snapshot_id="s", version="1", content_hash="h")
    graph = StateGraph(
        [
            n("producer", routing_refs=DiscoveryRoutingRefs(signal_ids=["sig"])),
            n("consumer", routing_refs=DiscoveryRoutingRefs(signal_ids=["sig"])),
            n("other"),
        ],
        shared_state=GraphSharedState(substrate=snapshot),
    )
    graph.mark_started("producer")
    graph.mark_terminal("producer", ok({"v": 1}))
    discovery = ExploratoryDiscovery(
        episode_id="e1",
        producer_node_id="producer",
        owner_id="o",
        snapshot_id="s",
        snapshot_version="1",
        source_spans=["x"],
        description="d",
        payload={"k": "v"},
        provenance_hash="p",
        affected_refs=DiscoveryRoutingRefs(signal_ids=["sig"]),
    )
    graph.publish_discovery(discovery)
    decisions = graph.shared_state.route_decisions
    assert [(d.consumer_node_id, d.status) for d in decisions] == [
        ("consumer", DiscoveryRouteStatus.ACCEPTED)
    ] or any(
        d.consumer_node_id == "consumer" and d.status is DiscoveryRouteStatus.ACCEPTED
        for d in decisions
    )
    assert graph.status("consumer") in {GraphNodeStatus.PENDING, GraphNodeStatus.RUNNABLE}
    for name in ("consumer", "other"):
        if graph.status(name) is GraphNodeStatus.RUNNABLE:
            graph.mark_started(name)
    context = graph.execution_context("consumer")
    assert set(context.shared_state.discoveries) == {"e1"}
    context.shared_state.discoveries["e1"].payload["k"] = "mutated"
    assert graph.shared_state.discoveries["e1"].payload == {"k": "v"}
    other = graph.execution_context("other")
    assert other.shared_state.discoveries == {} and other.dependencies == {}
    with pytest.raises(ValueError, match="is not running"):
        graph.execution_context("producer")


def test_discovery_publication_rules_and_conflicts():
    snapshot = SharedSubstrateSnapshot(snapshot_id="s", version="1", content_hash="h")
    graph = StateGraph([n("p"), n("c")], shared_state=GraphSharedState(substrate=snapshot))

    def disc(**overrides):
        values = dict(
            episode_id="e1",
            producer_node_id="p",
            owner_id="o",
            snapshot_id="s",
            snapshot_version="1",
            source_spans=["x"],
            description="d",
            payload={},
            provenance_hash="h",
            affected_refs=DiscoveryRoutingRefs(task_ids=["c"]),
        )
        values.update(overrides)
        return ExploratoryDiscovery(**values)

    with pytest.raises(ValueError, match="must complete before publication"):
        graph.publish_discovery(disc())
    graph.mark_started("p")
    graph.mark_terminal("p", ok())
    with pytest.raises(ValueError, match="unexpected graph source snapshot"):
        graph.publish_discovery(disc(snapshot_id="other"))
    with pytest.raises(ValueError, match="unexpected graph source version"):
        graph.publish_discovery(disc(snapshot_version="2"))
    with pytest.raises(ValueError, match="Only closed"):
        graph.publish_discovery(disc(closed=False))
    with pytest.raises(ValueError, match="known graph node"):
        graph.publish_discovery(disc(producer_node_id="ghost"))
    conflict = graph.try_publish_discovery(disc(snapshot_version="2"))
    assert (
        conflict.kind is GraphStateConflictKind.DISCOVERY_VERSION_MISMATCH
        and conflict.sequence == 1
    )
    graph.publish_discovery(disc())
    with pytest.raises(ValueError, match="already exists"):
        graph.publish_discovery(disc())
    write = SharedStateWrite(producer_node_id="p", key="k", value={"a": 1}, provenance_hash="h")
    graph.write_shared_value(write)
    collision = graph.try_write_shared_value(write)
    assert (
        collision.kind is GraphStateConflictKind.IMMUTABLE_WRITE_COLLISION
        and collision.sequence == 2
    )
    with pytest.raises(ValueError, match="immutable once written"):
        graph.write_shared_value(write)
    graph.mark_started("c") if graph.status("c") is GraphNodeStatus.RUNNABLE else None
    late = graph.try_publish_discovery(disc(episode_id="e2"))
    assert late is None


def test_lateral_dependency_orders_the_consumer_and_rejects_late_requests():
    snapshot = SharedSubstrateSnapshot(snapshot_id="s", version="1", content_hash="h")
    graph = StateGraph([n("p"), n("c"), n("d")], shared_state=GraphSharedState(substrate=snapshot))
    graph.mark_started("p")
    graph.mark_terminal("p", ok())
    discovery = ExploratoryDiscovery(
        episode_id="e1",
        producer_node_id="p",
        owner_id="o",
        snapshot_id="s",
        snapshot_version="1",
        source_spans=["x"],
        description="d",
        payload={},
        provenance_hash="h",
    )
    graph.publish_discovery(discovery)
    request = LateralDependencyRequest(
        consumer_node_id="c", consumer_action_id="act", discovery_episode_id="e1", reason="r"
    )
    graph.request_lateral_dependency(request)
    with pytest.raises(ValueError, match="already exists"):
        graph.request_lateral_dependency(request)
    with pytest.raises(ValueError, match="distinct graph nodes"):
        graph.request_lateral_dependency(request.model_copy(update={"consumer_node_id": "p"}))
    with pytest.raises(ValueError, match="known graph node"):
        graph.request_lateral_dependency(
            request.model_copy(update={"consumer_node_id": "ghost", "consumer_action_id": "x"})
        )
    with pytest.raises(ValueError, match="published discovery|graph-published discovery"):
        graph.request_lateral_dependency(
            request.model_copy(update={"discovery_episode_id": "nope", "consumer_action_id": "y"})
        )
    graph.mark_started("c")
    with pytest.raises(ValueError, match="after consumer execution starts"):
        graph.add_lateral_dependency("p", "c", "e1")


def test_snapshot_roundtrip_recovery_and_corruption():
    graph = StateGraph([n("a"), n("b", ["a"]), n("c", ["b"])])
    graph.mark_started("a")
    graph.mark_terminal("a", ok({"x": 1}))
    graph.mark_started("b")
    payload = json.loads(json.dumps(graph.snapshot()))
    restored = StateGraph.from_snapshot(payload)
    assert restored.snapshot() == graph.snapshot()
    assert restored.status("b") is GraphNodeStatus.RUNNING
    assert restored.recover_interrupted() == ["b"]
    assert restored.status("b") is F and restored.status("c") is B
    assert restored.result("b").diagnostics == ["interrupted-non-idempotent"]
    replay = StateGraph.from_snapshot(payload)
    assert (
        replay.recover_interrupted({"b"}) == ["b"]
        and replay.status("b") is GraphNodeStatus.RUNNABLE
    )
    bad = json.loads(json.dumps(payload))
    bad["statuses"].pop("c")
    with pytest.raises(ValueError, match="statuses do not match"):
        StateGraph.from_snapshot(bad)
    inconsistent = json.loads(json.dumps(payload))
    inconsistent["results"].pop("a")
    with pytest.raises(ValueError, match=r"\['a'\] have a terminal status but no recorded result"):
        StateGraph.from_snapshot(inconsistent)


def test_snapshot_survives_the_deepest_allowed_node_output():
    graph = StateGraph([n("a")])
    graph.mark_started("a")
    graph.mark_terminal("a", ok(nested(64)))
    restored = StateGraph.from_snapshot(json.loads(json.dumps(graph.snapshot())))
    assert restored.result("a").output == nested(64)


def test_overdeep_node_output_fails_the_node_and_names_the_depth_limit():
    async def deep(node, context):
        return ok(nested(120))

    graph = StateGraph([n("a"), n("b", ["a"])])
    results = arun(graph.execute({A: deep}))
    assert results["a"].status is F
    assert "graph node result output nests more than 64 levels deep" in results["a"].reason
    assert graph.status("b") is B
    graph.snapshot()


def test_graph_scheduling_cost_scales_acceptably():
    report = {}
    for size in (250, 500, 1000):
        nodes = [n("n0")] + [n(f"n{i}", [f"n{i - 1}"]) for i in range(1, size)]
        graph = StateGraph(nodes)
        started = time.monotonic()
        arun(graph.execute({A: run_ok}), timeout=900)
        report[size] = round(time.monotonic() - started, 2)
    assert report[1000] < 30, report
    wide = [n(f"leaf{i}") for i in range(800)] + [n("sink", [f"leaf{i}" for i in range(800)])]
    started = time.monotonic()
    graph = StateGraph(wide)
    arun(graph.execute({A: run_ok}), timeout=900)


def test_late_discovery_is_recorded_when_the_consumer_already_started():
    snapshot = SharedSubstrateSnapshot(snapshot_id="s", version="1", content_hash="h")
    graph = StateGraph(
        [n("p"), n("c", routing_refs=DiscoveryRoutingRefs(task_ids=["c-task"]))],
        shared_state=GraphSharedState(substrate=snapshot),
    )
    graph.mark_started("p")
    graph.mark_terminal("p", ok())
    graph.mark_started("c")
    graph.publish_discovery(
        ExploratoryDiscovery(
            episode_id="late",
            producer_node_id="p",
            owner_id="o",
            snapshot_id="s",
            snapshot_version="1",
            source_spans=["x"],
            description="d",
            payload={},
            provenance_hash="h",
            affected_refs=DiscoveryRoutingRefs(task_ids=["c-task"]),
        )
    )
    state = graph.shared_state
    assert [d.status for d in state.route_decisions] == [DiscoveryRouteStatus.LATE_DISCOVERY]
    assert [c.kind for c in state.conflicts] == [GraphStateConflictKind.LATE_DISCOVERY]
    empty = StateGraph([n("p2")], shared_state=GraphSharedState(substrate=snapshot))
    empty.mark_started("p2")
    empty.mark_terminal("p2", ok())
    empty.publish_discovery(
        ExploratoryDiscovery(
            episode_id="none",
            producer_node_id="p2",
            owner_id="o",
            snapshot_id="s",
            snapshot_version="1",
            source_spans=["x"],
            description="d",
            payload={},
            provenance_hash="h",
        )
    )
    assert [d.status for d in empty.shared_state.route_decisions] == [DiscoveryRouteStatus.REJECTED]
