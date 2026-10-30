from nailong_agent_sdk.state.elastic import ElasticNodeRole, ElasticRefusalCode, GraphSpawnStatus
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus
from tests.support.elastic import blocked, done_with, edge_set, failed, req, started_order, statuses
from tests.support.elastic_graph import (
    BLOCKED,
    COMPLETED,
    ELASTIC,
    PENDING,
    RUNNABLE,
    complete_with,
    scripted,
)
from tests.support.plans import n, ok


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
