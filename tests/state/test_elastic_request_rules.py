import json

import pytest

from nailong_agent_sdk.state.elastic import (
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
    ElasticNodeRole,
    ElasticNodeSpec,
    ElasticRefusalCode,
    GraphSpawnStatus,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNode, GraphSharedState
from nailong_agent_sdk.state.planning import Plan
from nailong_agent_sdk.state.shared_state import DiscoveryRouteStatus, DiscoveryRoutingRefs
from tests.support.elastic import SUBSTRATE, discovery, done_with, req
from tests.support.elastic_graph import ELASTIC, RUNNABLE, complete_with
from tests.support.plans import AGENT, n, ok, task


def test_invalid_requests_are_refused_one_by_one_and_valid_ones_still_proceed():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=4)
    complete_with(
        graph,
        "root",
        done_with(req("dup"), req("dup"), req("bad", dependencies=["ghost"]), req("ok")),
    )
    by_id = {}
    for record in graph.spawn_records:
        by_id.setdefault(record.request.request_id, []).append(record)
    assert [r.status for r in by_id["dup"]] == [GraphSpawnStatus.ACCEPTED, GraphSpawnStatus.REFUSED]
    assert by_id["dup"][1].code is ElasticRefusalCode.REQUEST_ID_DUPLICATE
    (bad,) = by_id["bad"]
    assert bad.status is GraphSpawnStatus.REFUSED
    assert bad.code is ElasticRefusalCode.DEPENDENCY_NOT_VISIBLE
    assert '"ghost"' in bad.reason and '"root"' in bad.reason
    assert by_id["ok"][0].status is GraphSpawnStatus.ACCEPTED
    assert graph.nodes["join:root"].elastic.joins == ["elastic:root:dup", "elastic:root:ok"]
    assert graph.result("root").diagnostics == [
        "elastic-refused:ELASTIC_REQUEST_ID_DUPLICATE:dup",
        "elastic-refused:ELASTIC_DEPENDENCY_NOT_VISIBLE:bad",
    ]


def test_a_request_can_never_overwrite_an_existing_node():
    graph = StateGraph(
        [n("root"), n("elastic:root:a"), n("after", ["root"])],
        max_elastic_depth=1,
        max_elastic_nodes=4,
    )
    complete_with(graph, "root", done_with(req("a"), req("b")))
    by_id = {r.request.request_id: r for r in graph.spawn_records}
    assert by_id["a"].status is GraphSpawnStatus.REFUSED
    assert by_id["a"].code is ElasticRefusalCode.NODE_ID_EXISTS
    assert '"elastic:root:a"' in by_id["a"].reason and '"root"' in by_id["a"].reason
    assert by_id["b"].status is GraphSpawnStatus.ACCEPTED
    assert graph.nodes["join:root"].elastic.joins == ["elastic:root:b"]
    blocked_join = StateGraph([n("root"), n("join:root")], max_elastic_depth=1, max_elastic_nodes=4)
    complete_with(blocked_join, "root", done_with(req("a")))
    (record,) = blocked_join.spawn_records
    assert record.status is GraphSpawnStatus.REFUSED
    assert record.code is ElasticRefusalCode.NODE_ID_EXISTS and '"join:root"' in record.reason
    assert sorted(blocked_join.nodes) == ["join:root", "root"]


def test_an_elastic_node_must_depend_on_its_parent_and_carry_a_consistent_spec():
    spec = ElasticNodeSpec(
        role=ElasticNodeRole.CHILD, parent_node_id="root", root_node_id="root", request=req("a")
    )
    with pytest.raises(ValueError, match="must depend on its parent node"):
        GraphNode(node_id="e", kind=ELASTIC, elastic_depth=1, elastic=spec)
    with pytest.raises(ValueError, match="elastic_depth of at least 1"):
        GraphNode(node_id="e", kind=ELASTIC, dependencies=["root"], elastic=spec)
    with pytest.raises(
        ValueError, match='only an elastic-node kind node may carry an "elastic" spec'
    ):
        GraphNode(node_id="e", kind=AGENT, elastic=spec)
    built = GraphNode(
        node_id="e", kind=ELASTIC, dependencies=["root"], elastic_depth=1, elastic=spec
    )
    assert built.elastic.request == req("a")


def test_when_every_request_is_invalid_no_join_is_created_and_dependents_are_not_held():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=4)
    complete_with(graph, "root", done_with(req("bad", dependencies=["ghost"])))
    assert sorted(graph.nodes) == ["after", "root"]
    assert graph.status("after") is RUNNABLE
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.REFUSED]
    assert graph.spawn_records[0].join_node_id is None


def test_a_request_may_name_its_parent_among_its_dependencies_without_duplicating_it():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
    complete_with(graph, "root", done_with(req("a", dependencies=["root"])))
    assert graph.nodes["elastic:root:a"].dependencies == ["root"]
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED]


def test_a_child_may_depend_on_what_its_parent_could_see_and_on_nothing_else():
    graph = StateGraph(
        [n("pre"), n("a", ["pre"]), n("b")], max_elastic_depth=1, max_elastic_nodes=4
    )
    complete_with(graph, "pre", ok("pre-out"))
    complete_with(graph, "b", ok("b-secret"))
    complete_with(
        graph, "a", done_with(req("visible", dependencies=["pre"]), req("leak", dependencies=["b"]))
    )
    records = {r.request.request_id: r for r in graph.spawn_records}
    assert records["visible"].status is GraphSpawnStatus.ACCEPTED
    assert graph.nodes["elastic:a:visible"].dependencies == ["a", "pre"]
    assert records["leak"].status is GraphSpawnStatus.REFUSED
    assert records["leak"].code is ElasticRefusalCode.DEPENDENCY_NOT_VISIBLE
    assert '"b"' in records["leak"].reason and '"a"' in records["leak"].reason
    graph.mark_started("elastic:a:visible")
    assert sorted(graph.execution_context("elastic:a:visible").dependencies) == ["a", "pre"]


def test_a_child_may_narrow_its_parents_routing_references_but_never_widen_them():
    refs = DiscoveryRoutingRefs(signal_ids=["clk"], requirement_ids=["R1"])
    graph = StateGraph([n("root", routing_refs=refs)], max_elastic_depth=1, max_elastic_nodes=4)
    narrower = DiscoveryRoutingRefs(signal_ids=["clk"])
    wider = DiscoveryRoutingRefs(signal_ids=["clk", "rst"], schema_ids=["S9"])
    complete_with(
        graph,
        "root",
        done_with(req("narrow", routing_refs=narrower), req("wide", routing_refs=wider)),
    )
    records = {r.request.request_id: r for r in graph.spawn_records}
    assert records["narrow"].status is GraphSpawnStatus.ACCEPTED
    assert graph.nodes["elastic:root:narrow"].routing_refs == narrower
    wide = records["wide"]
    assert wide.status is GraphSpawnStatus.REFUSED
    assert wide.code is ElasticRefusalCode.ROUTING_REFS_NOT_INHERITED
    assert "rst" in wide.reason and "S9" in wide.reason and '"root"' in wide.reason


def test_discoveries_published_before_a_child_existed_are_routed_to_it_on_spawn():
    graph = StateGraph(
        [n("prod"), n("root", routing_refs=DiscoveryRoutingRefs(signal_ids=["sig"]))],
        shared_state=GraphSharedState(substrate=SUBSTRATE),
        max_elastic_depth=1,
        max_elastic_nodes=2,
    )
    complete_with(graph, "prod", ok())
    graph.publish_discovery(discovery("d1", producer="prod", signals=("sig",)))
    complete_with(
        graph,
        "root",
        done_with(req("a", routing_refs=DiscoveryRoutingRefs(signal_ids=["sig"]))),
    )
    decisions = [
        (d.consumer_node_id, d.status, d.reason)
        for d in graph.shared_state.route_decisions
        if d.consumer_node_id == "elastic:root:a"
    ]
    assert decisions == [
        (
            "elastic:root:a",
            DiscoveryRouteStatus.ACCEPTED,
            "Exact graph routing references matched a node spawned after publication.",
        )
    ]
    graph.mark_started("elastic:root:a")
    context = graph.execution_context("elastic:root:a")
    assert sorted(context.shared_state.discoveries) == ["d1"]


def test_a_child_without_routing_references_receives_no_earlier_discoveries():
    graph = StateGraph(
        [n("prod"), n("root", routing_refs=DiscoveryRoutingRefs(signal_ids=["sig"]))],
        shared_state=GraphSharedState(substrate=SUBSTRATE),
        max_elastic_depth=1,
        max_elastic_nodes=2,
    )
    complete_with(graph, "prod", ok())
    graph.publish_discovery(discovery("d1", producer="prod", signals=("sig",)))
    complete_with(graph, "root", done_with(req("a")))
    graph.mark_started("elastic:root:a")
    assert graph.execution_context("elastic:root:a").shared_state.discoveries == {}


def test_elastic_node_invariants_hold_at_construction_and_on_restore():
    with pytest.raises(ValueError, match="elastic"):
        n("e", kind=ELASTIC, elastic_depth=1)
    with pytest.raises(ValueError, match="elastic_depth"):
        n("x", elastic_depth=1)
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
    complete_with(graph, "root", done_with(req("a")))
    snapshot = json.loads(json.dumps(graph.snapshot()))

    over_cap = json.loads(json.dumps(snapshot))
    over_cap["max_elastic_nodes"] = 0
    with pytest.raises(
        ValueError, match=r"2 elastic nodes \(1 child and 1 join\) exceed max_elastic_nodes 0"
    ):
        StateGraph.from_snapshot(over_cap)

    over_depth = json.loads(json.dumps(snapshot))
    over_depth["max_elastic_depth"] = 0
    with pytest.raises(ValueError, match="elastic depth 1 exceeds max_elastic_depth 0"):
        StateGraph.from_snapshot(over_depth)

    no_record = json.loads(json.dumps(snapshot))
    no_record["spawn_records"] = []
    with pytest.raises(ValueError, match='"elastic:root:a" has no accepted spawn record'):
        StateGraph.from_snapshot(no_record)

    bad_join = json.loads(json.dumps(snapshot))
    for node in bad_join["nodes"]:
        if node["node_id"] == "join:root":
            node["elastic"]["joins"] = ["ghost"]
    with pytest.raises(ValueError, match='joins unknown node "ghost"'):
        StateGraph.from_snapshot(bad_join)

    bad_depth = json.loads(json.dumps(snapshot))
    for node in bad_depth["nodes"]:
        if node["node_id"] == "elastic:root:a":
            node["elastic_depth"] = 2
    with pytest.raises(ValueError, match="must be one deeper than its parent"):
        StateGraph.from_snapshot(bad_depth)


def test_caps_are_validated_by_the_graph_and_the_plan():
    with pytest.raises(ValueError, match="max_elastic_nodes must be between 0 and"):
        StateGraph([n("a")], max_elastic_nodes=-1)
    with pytest.raises(ValueError, match="max_elastic_depth must be between 0 and"):
        StateGraph([n("a")], max_elastic_depth=MAX_ELASTIC_DEPTH_LIMIT + 1)
    with pytest.raises(ValueError, match="max_elastic_nodes"):
        Plan(plan_id="p", tasks=[task("T1")], max_elastic_nodes=MAX_ELASTIC_NODES_LIMIT + 1)
    with pytest.raises(ValueError, match="max_elastic_depth"):
        Plan(plan_id="p", tasks=[task("T1")], max_elastic_depth=MAX_ELASTIC_DEPTH_LIMIT + 1)
    plan = Plan(
        plan_id="p",
        tasks=[task("T1")],
        max_elastic_depth=MAX_ELASTIC_DEPTH_LIMIT,
        max_elastic_nodes=MAX_ELASTIC_NODES_LIMIT,
    )
    assert plan.max_elastic_nodes == MAX_ELASTIC_NODES_LIMIT


def test_the_snapshot_round_trips_nodes_records_grants_and_remaining_capacity():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    payload = json.loads(json.dumps(graph.snapshot()))
    restored = StateGraph.from_snapshot(payload)
    assert restored.snapshot() == graph.snapshot()
    assert restored.spawn_records == graph.spawn_records
    complete_with(restored, "elastic:root:a", ok())
    complete_with(restored, "elastic:root:b", ok())
    complete_with(restored, "join:root", done_with(req("late")))
    deferred = [r for r in restored.spawn_records if r.status is GraphSpawnStatus.DEFERRED]
    assert [(r.parent_node_id, r.code) for r in deferred] == [
        ("join:root", ElasticRefusalCode.DEPTH_CAP_REACHED)
    ]


def test_snapshots_written_before_elastic_nodes_existed_still_restore():
    graph = StateGraph([n("a"), n("b", ["a"])])
    payload = json.loads(json.dumps(graph.snapshot()))
    payload.pop("spawn_records")
    payload.pop("capacity_grants")
    for node in payload["nodes"]:
        node.pop("elastic")
    restored = StateGraph.from_snapshot(payload)
    assert restored.spawn_records == () and restored.capacity_grants == ()
    assert sorted(restored.nodes) == ["a", "b"]
