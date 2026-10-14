import json

import pytest

from nailong_agent_sdk.state.elastic import (
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
    ElasticCapacity,
    ElasticRefusalCode,
    GraphSpawnStatus,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus
from tests.support.elastic import done_with, req, statuses
from tests.support.elastic_graph import (
    BLOCKED,
    COMPLETED,
    PENDING,
    RUNNABLE,
    complete_with,
    scripted,
)
from tests.support.plans import n, ok


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
