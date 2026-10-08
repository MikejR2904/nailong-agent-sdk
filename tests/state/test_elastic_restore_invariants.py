import json

import pytest

from nailong_agent_sdk.state.graph import StateGraph
from tests.support.elastic import done_with, req
from tests.support.plans import n


def spawned(*requests, cap=5, **options):
    graph = StateGraph([n("root")], max_elastic_depth=2, max_elastic_nodes=cap, **options)
    graph.mark_started("root")
    graph.mark_terminal("root", done_with(*requests))
    return json.loads(json.dumps(graph.snapshot()))


def node_of(snapshot, node_id):
    return next(node for node in snapshot["nodes"] if node["node_id"] == node_id)


def test_an_untouched_snapshot_restores():
    restored = StateGraph.from_snapshot(spawned(req("a"), req("b")))
    assert sorted(restored.nodes) == ["elastic:root:a", "elastic:root:b", "join:root", "root"]


def test_a_child_that_names_the_wrong_root_is_refused_with_both_roots():
    snapshot = spawned(req("a"))
    node_of(snapshot, "elastic:root:a")["elastic"]["root_node_id"] = "elsewhere"
    with pytest.raises(
        ValueError,
        match=r'Elastic node "elastic:root:a" names root "elsewhere" but its ancestry leads to '
        r'"root"\.',
    ):
        StateGraph.from_snapshot(snapshot)


def test_a_join_that_joins_a_planned_node_is_refused():
    snapshot = spawned(req("a"))
    node_of(snapshot, "join:root")["elastic"]["joins"] = ["root"]
    with pytest.raises(
        ValueError,
        match=r'Join node "join:root" joins "root", which is not an elastic child of "root"\.',
    ):
        StateGraph.from_snapshot(snapshot)


def test_a_join_that_joins_the_child_of_another_parent_is_refused():
    snapshot = spawned(req("a"))
    first = StateGraph.from_snapshot(snapshot)
    first.mark_started("elastic:root:a")
    first.mark_terminal("elastic:root:a", done_with(req("deeper")))
    nested = json.loads(json.dumps(first.snapshot()))
    node_of(nested, "join:root")["elastic"]["joins"] = ["elastic:elastic:root:a:deeper"]
    with pytest.raises(
        ValueError,
        match=r'Join node "join:root" joins "elastic:elastic:root:a:deeper", which is not an '
        r'elastic child of "root"\.',
    ):
        StateGraph.from_snapshot(nested)


def test_an_accepted_record_for_a_node_the_graph_does_not_have_is_refused():
    snapshot = spawned(req("a"))
    extra = json.loads(json.dumps(snapshot["spawn_records"][0]))
    extra["sequence"] = 2
    extra["child_node_id"] = "elastic:root:ghost"
    snapshot["spawn_records"].append(extra)
    with pytest.raises(
        ValueError,
        match=r'Spawn record 2 accepts "elastic:root:ghost", which is not a node of this graph\.',
    ):
        StateGraph.from_snapshot(snapshot)


def test_a_deferred_record_that_points_at_a_node_that_holds_nothing_is_refused():
    snapshot = spawned(req("a"), req("b"), cap=2)
    assert [record["status"] for record in snapshot["spawn_records"]] == ["deferred", "deferred"]
    snapshot["spawn_records"][0]["join_node_id"] = "root"
    with pytest.raises(
        ValueError,
        match=r'Spawn record 1 is deferred behind "root", which is not a held join node\.',
    ):
        StateGraph.from_snapshot(snapshot)
