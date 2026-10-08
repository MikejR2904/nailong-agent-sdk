import json

import pytest

from nailong_agent_sdk.state.elastic import (
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
    ElasticCapacity,
    ElasticNodeRole,
    ElasticNodeSpec,
    ElasticRefusalCode,
    GraphSpawnStatus,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import (
    GraphNode,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
)
from nailong_agent_sdk.state.planning import Plan
from nailong_agent_sdk.state.shared_state import DiscoveryRouteStatus, DiscoveryRoutingRefs
from tests.support.elastic import (
    SUBSTRATE,
    blocked,
    discovery,
    done_with,
    edge_set,
    failed,
    req,
    started_order,
    statuses,
)
from tests.support.plans import AGENT, arun, n, ok, task

ELASTIC = GraphNodeKind.ELASTIC
COMPLETED = GraphNodeStatus.COMPLETED
PENDING = GraphNodeStatus.PENDING
RUNNABLE = GraphNodeStatus.RUNNABLE
BLOCKED = GraphNodeStatus.BLOCKED


def complete_with(graph, node_id, result):
    graph.mark_started(node_id)
    graph.mark_terminal(node_id, result)


def scripted(graph, plan):
    seen = {}

    async def executor(node, context):
        seen[node.node_id] = {key: value for key, value in context.dependencies.items()}
        return plan(node, context)

    return seen, lambda: arun(graph.execute({AGENT: executor, ELASTIC: executor}))


def test_a_completed_result_with_requests_creates_children_and_one_join():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=2, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    assert sorted(graph.nodes) == [
        "after",
        "elastic:root:a",
        "elastic:root:b",
        "join:root",
        "root",
    ]
    child = graph.nodes["elastic:root:a"]
    assert child.kind is ELASTIC and child.elastic_depth == 1 and child.dependencies == ["root"]
    assert child.elastic.role is ElasticNodeRole.CHILD
    assert (child.elastic.parent_node_id, child.elastic.root_node_id) == ("root", "root")
    assert child.elastic.request == req("a")
    join = graph.nodes["join:root"]
    assert join.kind is ELASTIC and join.elastic_depth == 1 and join.dependencies == ["root"]
    assert join.elastic.role is ElasticNodeRole.JOIN
    assert join.elastic.joins == ["elastic:root:a", "elastic:root:b"]
    assert statuses(graph) == {
        "after": "pending",
        "elastic:root:a": "runnable",
        "elastic:root:b": "runnable",
        "join:root": "pending",
        "root": "completed",
    }
    assert edge_set(graph) == {
        ("root", "elastic:root:a", "dynamic-fan-out"),
        ("root", "elastic:root:b", "dynamic-fan-out"),
        ("elastic:root:a", "join:root", "fan-in"),
        ("elastic:root:b", "join:root", "fan-in"),
        ("join:root", "after", "static"),
    }
    records = graph.spawn_records
    assert [
        (r.sequence, r.parent_node_id, r.request.request_id, r.status, r.child_node_id)
        for r in records
    ] == [
        (1, "root", "a", GraphSpawnStatus.ACCEPTED, "elastic:root:a"),
        (2, "root", "b", GraphSpawnStatus.ACCEPTED, "elastic:root:b"),
    ]
    assert {r.join_node_id for r in records} == {"join:root"}
    assert {r.depth for r in records} == {1} and {r.event_sequence for r in records} == {3}
    assert graph.result("root").spawn_requests == [req("a"), req("b")]


def test_children_run_in_the_next_wave_and_the_join_resumes_with_their_results():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=3)

    def plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"), req("b"), output="root-out")
        return ok(f"{node.node_id}-out")

    seen, run = scripted(graph, plan)
    results = run()
    outputs = {
        node_id: {key: value.output for key, value in deps.items()}
        for node_id, deps in seen.items()
    }
    assert outputs["elastic:root:a"] == {"root": "root-out"}
    assert outputs["join:root"] == {
        "root": "root-out",
        "elastic:root:a": "elastic:root:a-out",
        "elastic:root:b": "elastic:root:b-out",
    }
    assert outputs["after"] == {"root": "root-out", "join:root": "join:root-out"}
    assert started_order(graph) == [
        "root",
        "elastic:root:a",
        "elastic:root:b",
        "join:root",
        "after",
    ]
    assert all(result.status is COMPLETED for result in results.values())


def test_a_failed_child_still_lets_the_join_run_with_the_typed_failure():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=3)

    def plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"), req("b"))
        if node.node_id == "elastic:root:a":
            return failed("probe crashed")
        return ok(node.node_id)

    seen, run = scripted(graph, plan)
    run()
    join_view = {key: value.status.value for key, value in seen["join:root"].items()}
    assert join_view == {
        "root": "completed",
        "elastic:root:a": "failed",
        "elastic:root:b": "completed",
    }
    assert seen["join:root"]["elastic:root:a"].reason == "probe crashed"
    assert graph.status("join:root") is COMPLETED and graph.status("after") is COMPLETED


def test_a_cancelled_child_blocks_the_join_and_its_dependents_for_good():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    complete_with(graph, "elastic:root:a", GraphNodeResult(status=GraphNodeStatus.CANCELLED))
    assert graph.status("join:root") is BLOCKED and graph.status("after") is BLOCKED
    assert 'Dependency "elastic:root:a" is cancelled' in graph.result("join:root").reason
    assert graph.status("elastic:root:b") is RUNNABLE
    assert graph.blocked_nodes() == []
    complete_with(graph, "elastic:root:b", ok())
    assert graph.status("join:root") is BLOCKED


def test_a_blocked_child_holds_the_join_and_its_dependents_until_it_is_reopened():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=2)
    attempts = {"a": 0}

    def plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"))
        if node.node_id == "elastic:root:a":
            attempts["a"] += 1
            return blocked("needs approval") if attempts["a"] == 1 else ok("approved-out")
        return ok(node.node_id)

    seen, run = scripted(graph, plan)
    run()
    assert statuses(graph) == {
        "after": "blocked",
        "elastic:root:a": "blocked",
        "join:root": "blocked",
        "root": "completed",
    }
    assert graph.blocked_nodes() == ["elastic:root:a"]
    assert graph.reopen_blocked() == ["elastic:root:a"]
    assert graph.status("join:root") is PENDING and graph.status("after") is PENDING
    run()
    assert statuses(graph) == {node_id: "completed" for node_id in graph.nodes}
    assert seen["join:root"]["elastic:root:a"].output == "approved-out"


def test_requests_are_only_allowed_on_a_completed_result():
    with pytest.raises(ValueError, match="spawn_requests"):
        GraphNodeResult(status=GraphNodeStatus.FAILED, spawn_requests=[req()])
    with pytest.raises(ValueError, match="spawn_requests"):
        GraphNodeResult(status=GraphNodeStatus.BLOCKED, spawn_requests=[req()])
    assert GraphNodeResult(status=COMPLETED, spawn_requests=[req()]).spawn_requests == [req()]


def test_the_depth_cap_defers_the_batch_behind_a_blocked_join():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=0, max_elastic_nodes=2)
    complete_with(graph, "root", done_with(req("a")))
    assert sorted(graph.nodes) == ["after", "join:root", "root"]
    assert statuses(graph) == {"after": "blocked", "join:root": "blocked", "root": "completed"}
    (record,) = graph.spawn_records
    assert record.status is GraphSpawnStatus.DEFERRED
    assert record.code is ElasticRefusalCode.DEPTH_CAP_REACHED
    assert record.child_node_id is None and record.join_node_id == "join:root"
    assert "max_elastic_depth 0" in record.reason and '"root"' in record.reason
    held = graph.result("join:root")
    assert held.status is BLOCKED
    assert "grant_elastic_capacity" in held.reason and "decline_elastic_requests" in held.reason
    assert graph.result("root").diagnostics == ["elastic-deferred:ELASTIC_DEPTH_CAP_REACHED:a"]
    assert graph.blocked_nodes() == ["join:root"]


def test_a_held_join_is_never_reopened_by_the_generic_reopen():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=0, max_elastic_nodes=2)
    complete_with(graph, "root", done_with(req("a")))
    assert graph.reopen_blocked() == []
    assert graph.status("join:root") is BLOCKED
    with pytest.raises(ValueError, match="grant_elastic_capacity") as error:
        graph.reopen_blocked(["join:root"])
    assert '"join:root"' in str(error.value) and "decline_elastic_requests" in str(error.value)
    assert graph.status("join:root") is BLOCKED


def test_the_node_cap_defers_the_whole_batch_and_never_a_part_of_it():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=1)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    assert sorted(graph.nodes) == ["after", "join:root", "root"]
    assert [(r.request.request_id, r.status, r.code) for r in graph.spawn_records] == [
        ("a", GraphSpawnStatus.DEFERRED, ElasticRefusalCode.NODE_CAP_REACHED),
        ("b", GraphSpawnStatus.DEFERRED, ElasticRefusalCode.NODE_CAP_REACHED),
    ]
    assert "max_elastic_nodes 1" in graph.spawn_records[0].reason
    assert "2 requested" in graph.spawn_records[0].reason


def test_a_batch_that_fits_the_cap_exactly_is_accepted():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2
    assert graph.status("join:root") is PENDING
    single = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
    complete_with(single, "root", done_with(req("a")))
    assert [r.status for r in single.spawn_records] == [GraphSpawnStatus.ACCEPTED]


def test_a_grant_applies_the_deferred_batch_and_reopens_the_join_and_dependents():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=0, max_elastic_nodes=2)

    def plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"), output="root-out")
        return ok(f"{node.node_id}-out")

    seen, run = scripted(graph, plan)
    run()
    assert graph.status("join:root") is BLOCKED and graph.status("after") is BLOCKED
    grant = graph.grant_elastic_capacity(
        max_elastic_depth=1, max_elastic_nodes=2, reason="approved by the designer"
    )
    assert (grant.sequence, grant.previous_max_depth, grant.max_depth) == (1, 0, 1)
    assert (grant.previous_max_nodes, grant.max_nodes) == (2, 2)
    assert grant.reason == "approved by the designer"
    assert graph.capacity_grants == (grant,)
    (record,) = graph.spawn_records
    assert record.status is GraphSpawnStatus.ACCEPTED and record.child_node_id == "elastic:root:a"
    assert record.grant_sequence == 1 and record.code is None
    assert graph.nodes["join:root"].elastic.joins == ["elastic:root:a"]
    assert statuses(graph)["elastic:root:a"] == "runnable"
    assert graph.status("join:root") is PENDING and graph.status("after") is PENDING
    run()
    assert statuses(graph) == {node_id: "completed" for node_id in graph.nodes}
    assert {key: value.output for key, value in seen["join:root"].items()} == {
        "root": "root-out",
        "elastic:root:a": "elastic:root:a-out",
    }


def test_a_grant_that_is_still_too_small_leaves_the_batch_deferred():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=0, max_elastic_nodes=1)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    graph.grant_elastic_capacity(max_elastic_depth=1, reason="depth only")
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.DEFERRED] * 2
    assert graph.spawn_records[0].code is ElasticRefusalCode.NODE_CAP_REACHED
    assert graph.status("join:root") is BLOCKED
    graph.grant_elastic_capacity(max_elastic_nodes=2, reason="one short of the join")
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.DEFERRED] * 2
    graph.grant_elastic_capacity(max_elastic_nodes=3, reason="room for both and the join")
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2
    assert [g.sequence for g in graph.capacity_grants] == [1, 2, 3]


def test_a_grant_can_only_raise_the_caps_and_stays_inside_the_hard_limits():
    graph = StateGraph([n("root")], max_elastic_depth=2, max_elastic_nodes=4)
    with pytest.raises(ValueError, match="cannot lower max_elastic_depth from 2 to 1"):
        graph.grant_elastic_capacity(max_elastic_depth=1, reason="x")
    with pytest.raises(ValueError, match="cannot lower max_elastic_nodes from 4 to 3"):
        graph.grant_elastic_capacity(max_elastic_nodes=3, reason="x")
    with pytest.raises(ValueError, match=f"above the hard limit {MAX_ELASTIC_DEPTH_LIMIT}"):
        graph.grant_elastic_capacity(max_elastic_depth=MAX_ELASTIC_DEPTH_LIMIT + 1, reason="x")
    with pytest.raises(ValueError, match=f"above the hard limit {MAX_ELASTIC_NODES_LIMIT}"):
        graph.grant_elastic_capacity(max_elastic_nodes=MAX_ELASTIC_NODES_LIMIT + 1, reason="x")
    with pytest.raises(ValueError, match="at least one of"):
        graph.grant_elastic_capacity(reason="x")
    with pytest.raises(ValueError, match="reason"):
        graph.grant_elastic_capacity(max_elastic_nodes=5, reason="  ")
    assert graph.capacity_grants == ()
    graph.grant_elastic_capacity(max_elastic_nodes=5, reason="more room")
    assert graph.snapshot()["max_elastic_nodes"] == 5 and graph.snapshot()["max_elastic_depth"] == 2


def test_declining_resolves_the_join_without_running_it_and_releases_dependents():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=0, max_elastic_nodes=2)

    def plan(node, context):
        return done_with(req("a")) if node.node_id == "root" else ok(node.node_id)

    seen, run = scripted(graph, plan)
    run()
    records = graph.decline_elastic_requests("root", "not worth the budget")
    assert [(r.status, r.code) for r in records] == [
        (GraphSpawnStatus.DISCARDED, ElasticRefusalCode.BATCH_DECLINED)
    ]
    assert "not worth the budget" in records[0].reason
    assert graph.status("join:root") is COMPLETED
    assert graph.result("join:root").diagnostics == ["elastic-requests-declined"]
    assert "not worth the budget" in graph.result("join:root").reason
    assert graph.status("after") is RUNNABLE
    run()
    assert graph.status("after") is COMPLETED
    assert "join:root" not in seen
    assert sorted(seen["after"]) == ["join:root", "root"]
    with pytest.raises(ValueError, match='"root" has no deferred elastic requests'):
        graph.decline_elastic_requests("root", "again")
    with pytest.raises(ValueError, match='"ghost" is not a node of this graph'):
        graph.decline_elastic_requests("ghost", "why")
    with pytest.raises(ValueError, match="reason"):
        graph.decline_elastic_requests("root", " ")


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


def test_children_can_request_grandchildren_until_the_depth_cap_and_chain_the_joins():
    graph = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=2, max_elastic_nodes=4)

    def plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"), output="root-out")
        if node.node_id == "elastic:root:a":
            return done_with(req("g"), output="a-out")
        return ok(f"{node.node_id}-out")

    seen, run = scripted(graph, plan)
    run()
    grandchild = "elastic:elastic:root:a:g"
    assert graph.nodes[grandchild].elastic_depth == 2
    assert graph.nodes[grandchild].elastic.root_node_id == "root"
    assert graph.nodes[grandchild].elastic.parent_node_id == "elastic:root:a"
    assert "join:elastic:root:a" in graph.nodes
    assert sorted(seen["join:root"]) == ["elastic:root:a", "join:elastic:root:a", "root"]
    assert sorted(seen["after"]) == ["join:root", "root"]
    assert started_order(graph).index("join:elastic:root:a") < started_order(graph).index(
        "join:root"
    )
    assert statuses(graph) == {node_id: "completed" for node_id in graph.nodes}
    deep = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=4)

    def deep_plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"))
        if node.node_id == "elastic:root:a":
            return done_with(req("g"))
        return ok(node.node_id)

    _, deep_run = scripted(deep, deep_plan)
    deep_run()
    deferred = [r for r in deep.spawn_records if r.status is GraphSpawnStatus.DEFERRED]
    assert [(r.parent_node_id, r.code) for r in deferred] == [
        ("elastic:root:a", ElasticRefusalCode.DEPTH_CAP_REACHED)
    ]
    assert deep.status("join:elastic:root:a") is BLOCKED and deep.status("join:root") is BLOCKED


def test_a_blocked_join_still_waits_for_the_continuation_of_a_sibling_that_explored_later():
    graph = StateGraph([n("root")], max_elastic_depth=2, max_elastic_nodes=5)
    attempts = {"a": 0}

    def plan(node, context):
        if node.node_id == "root":
            return done_with(req("a"), req("b"))
        if node.node_id == "elastic:root:a":
            attempts["a"] += 1
            return blocked() if attempts["a"] == 1 else ok("a-out")
        if node.node_id == "elastic:root:b":
            return done_with(req("g"), output="b-out")
        return ok(node.node_id)

    seen, run = scripted(graph, plan)
    run()
    assert graph.status("join:root") is BLOCKED
    assert ("join:elastic:root:b", "join:root", "fan-in") in edge_set(graph)
    graph.reopen_blocked()
    run()
    assert statuses(graph) == {node_id: "completed" for node_id in graph.nodes}
    order = started_order(graph)
    assert order.index("join:elastic:root:b") < order.index("join:root")
    assert "join:elastic:root:b" in seen["join:root"]


def test_a_continuation_join_takes_over_the_soft_wait_its_parent_held():
    graph = StateGraph([n("root")], max_elastic_depth=2, max_elastic_nodes=4)
    complete_with(graph, "root", done_with(req("a")))
    complete_with(graph, "elastic:root:a", done_with(req("g")))
    edges = edge_set(graph)
    assert ("join:elastic:root:a", "join:root", "fan-in") in edges
    assert ("elastic:root:a", "join:root", "fan-in") in edges


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


def test_batches_commit_in_node_order_and_consume_the_cap_deterministically():
    def build():
        graph = StateGraph(
            [n("p1"), n("p2"), n("after", ["p1", "p2"])], max_elastic_depth=1, max_elastic_nodes=3
        )

        def plan(node, context):
            if node.node_id in {"p1", "p2"}:
                return done_with(req("x"), req("y"))
            return ok(node.node_id)

        _, run = scripted(graph, plan)
        run()
        return graph

    graph = build()
    by_parent = {}
    for record in graph.spawn_records:
        by_parent.setdefault(record.parent_node_id, set()).add(record.status)
    assert by_parent == {"p1": {GraphSpawnStatus.ACCEPTED}, "p2": {GraphSpawnStatus.DEFERRED}}
    assert [r.sequence for r in graph.spawn_records] == [1, 2, 3, 4]
    assert json.dumps(build().snapshot(), sort_keys=True) == json.dumps(
        graph.snapshot(), sort_keys=True
    )
    assert graph.status("join:p2") is BLOCKED and graph.status("after") is BLOCKED


def test_the_execution_context_reports_the_remaining_elastic_capacity():
    graph = StateGraph([n("root")], max_elastic_depth=2, max_elastic_nodes=3)
    graph.mark_started("root")
    capacity = graph.execution_context("root").elastic_capacity
    assert capacity == ElasticCapacity(node_depth=0, max_depth=2, nodes_used=0, max_nodes=3)
    assert capacity.remaining_nodes == 3 and capacity.depth_available is True
    graph.mark_terminal("root", done_with(req("a")))
    graph.mark_started("elastic:root:a")
    inner = graph.execution_context("elastic:root:a").elastic_capacity
    assert inner == ElasticCapacity(node_depth=1, max_depth=2, nodes_used=2, max_nodes=3)
    assert inner.remaining_nodes == 1 and inner.depth_available is True
    leaf = ElasticCapacity(node_depth=2, max_depth=2, nodes_used=1, max_nodes=3)
    assert leaf.depth_available is False


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
