import pytest
from pydantic import ValidationError

from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import (
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
)


def node(node_id):
    return GraphNode(node_id=node_id, kind=GraphNodeKind.AGENT)


def edge(parent, child, **changes):
    return GraphEdge(
        parent_node_id=parent, child_node_id=child, kind=GraphEdgeKind.CONDITIONAL, **changes
    )


def chain():
    link = edge("a", "b")
    return StateGraph([node("a"), node("b")], [link]), link


def ids(nodes):
    return [item.node_id for item in nodes]


def test_an_edge_cannot_be_changed_in_place_behind_the_schedulers_back():
    graph, link = chain()
    assert ids(graph.runnable()) == ["a"]
    with pytest.raises(ValidationError, match="frozen"):
        link.enabled = False
    assert ids(graph.runnable()) == ["a"]


def test_disabling_an_edge_through_the_graph_frees_the_child_at_once():
    graph, _ = chain()
    assert ids(graph.runnable()) == ["a"]
    graph.set_edge_enabled("a", "b", False)
    assert ids(graph.runnable()) == ["a", "b"]
    assert graph.root_causes("b") == ()


def test_enabling_an_edge_again_holds_a_child_that_has_not_started():
    graph, _ = chain()
    graph.set_edge_enabled("a", "b", False)
    assert ids(graph.runnable()) == ["a", "b"]
    graph.set_edge_enabled("a", "b", True)
    assert ids(graph.runnable()) == ["a"]
    assert graph.status("b") is GraphNodeStatus.PENDING


def test_an_edge_into_a_node_that_started_or_finished_cannot_be_changed():
    graph, _ = chain()
    (first,) = graph.start_runnable_wave()
    graph.mark_terminal(first.node_id, GraphNodeResult(status=GraphNodeStatus.COMPLETED))
    graph.start_runnable_wave()
    with pytest.raises(ValueError, match='node "b" is running'):
        graph.set_edge_enabled("a", "b", False)


def test_an_unknown_edge_is_named_and_an_edge_that_would_close_a_cycle_is_refused():
    graph, _ = chain()
    with pytest.raises(ValueError, match='no edge from "b" to "a"'):
        graph.set_edge_enabled("b", "a", False)
    cyclic = StateGraph([node("a"), node("b")], [edge("a", "b"), edge("b", "a", enabled=False)])
    with pytest.raises(ValueError, match="cycle"):
        cyclic.set_edge_enabled("b", "a", True)


def test_a_disabled_edge_survives_a_snapshot_round_trip_and_stays_out_of_the_schedule():
    graph, _ = chain()
    graph.set_edge_enabled("a", "b", False)
    restored = StateGraph.from_snapshot(graph.snapshot())
    assert ids(restored.runnable()) == ["a", "b"]


def test_the_incoming_edge_index_is_rebuilt_when_edges_are_added_and_when_one_is_toggled():
    graph, _ = chain()
    graph.runnable()
    first = graph._incoming_edges()
    assert graph._incoming_edges() is first
    graph.set_edge_enabled("a", "b", False)
    assert graph._incoming_edges() is not first
    assert graph._incoming_edges() == {}
