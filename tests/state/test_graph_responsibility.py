import math
import random
import re
import time

import pytest

from nailong_agent_sdk.developer_tools.attribute_failure import (
    MAX_ATTRIBUTED_BLOCKED,
    MAX_ATTRIBUTED_ROOTS,
    MAX_RELEASED_LISTED,
    attribute_graph,
)
from nailong_agent_sdk.developer_tools.diagnose import diagnose_graph
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphEdge, GraphEdgeKind, GraphNodeResult
from tests.support.graph_worlds import execute, world
from tests.support.plans import A, C, F, arun, n

TOLERANCE = 1e-4


def attributed(seed):
    ids, dependencies, edges, outcomes = world(seed)
    graph = execute(ids, dependencies, edges, outcomes)
    return graph, attribute_graph(graph)


def failing_graph(nodes, failing, edges=()):
    graph = StateGraph(nodes, list(edges))

    async def run(node, context):
        broken = node.node_id in failing
        return GraphNodeResult(status=F if broken else C, reason="failed" if broken else None)

    arun(graph.execute({A: run}))
    return graph


def shapley_values(graph, players):
    count = len(players)
    value = {}
    for mask in range(1 << count):
        chosen = [players[index] for index in range(count) if mask >> index & 1]
        value[mask] = len(chosen) + len(graph.released_by(chosen))
    values = dict.fromkeys(players, 0.0)
    for index, player in enumerate(players):
        for mask in range(1 << count):
            if mask >> index & 1:
                continue
            size = bin(mask).count("1")
            weight = math.factorial(size) * math.factorial(count - size - 1) / math.factorial(count)
            values[player] += weight * (value[mask | 1 << index] - value[mask])
    return values


@pytest.mark.parametrize("seed", range(120))
def test_responsibility_adds_up_to_every_stop_plus_every_node_it_held_up(seed):
    graph, report = attributed(seed)
    sets = graph.root_cause_sets()
    attributable = sum(1 for caused_by in sets.values() if caused_by)
    assert report.root_count == len(graph.failure_roots()) == len(report.roots)
    assert report.blocked_count == len(sets)
    assert report.total_responsibility == pytest.approx(
        report.root_count + attributable, abs=TOLERANCE
    )
    assert sum(root.responsibility for root in report.roots) == pytest.approx(
        report.total_responsibility, abs=TOLERANCE
    )


@pytest.mark.parametrize("seed", range(120))
def test_responsibility_is_the_shapley_value_of_repairing_the_roots(seed):
    graph, report = attributed(seed)
    sets = graph.root_cause_sets()
    involved = sorted({root for caused_by in sets.values() for root in caused_by})
    exact = shapley_values(graph, involved) if involved else {}
    for root in report.roots:
        expected = exact.get(root.node_id, 1.0)
        assert root.responsibility == pytest.approx(expected, abs=TOLERANCE), root.node_id


@pytest.mark.parametrize("seed", range(120))
def test_each_root_reports_exactly_what_it_holds_up_and_what_repairing_it_alone_releases(seed):
    graph, report = attributed(seed)
    sets = graph.root_cause_sets()
    diagnosis = diagnose_graph("run", graph)
    assert diagnosis.blocked_downstream == report.blocked_count
    held = {root.node_id: root.blocked_descendants for root in diagnosis.roots}
    for root in report.roots:
        holding = {node for node, caused_by in sets.items() if root.node_id in caused_by}
        assert root.blocked_count == len(holding)
        assert held[root.node_id] == sorted(holding)
        assert root.released_alone == list(graph.released_by([root.node_id]))
        partners = set().union(*(sets[node] for node in holding)) - {root.node_id}
        assert root.also_requires == sorted(partners)


@pytest.mark.parametrize("seed", range(60))
def test_roots_are_ranked_by_responsibility_then_by_when_they_stopped(seed):
    _, report = attributed(seed)
    keys = [
        (-root.responsibility, root.graph_event_sequence or 0, root.node_id)
        for root in report.roots
    ]
    assert keys == sorted(keys)


@pytest.mark.parametrize("seed", range(60))
def test_each_blocked_node_lists_its_independent_causes_and_a_real_path(seed):
    graph, report = attributed(seed)
    sets = graph.root_cause_sets()
    assert [item.node_id for item in report.blocked] == list(sets)[:MAX_ATTRIBUTED_BLOCKED]
    for item in report.blocked:
        path = graph.blocking_path(item.node_id)
        assert item.root_causes == sorted(sets[item.node_id])
        assert item.path == list(path) and item.path_length == len(path)
        assert item.path[0] in item.root_causes and item.path[-1] == item.node_id


def diamond():
    nodes = [n("A"), n("B"), n("C", ["A", "B"]), n("D", ["C"]), n("E", ["A"])]
    return failing_graph(nodes, {"A", "B"})


def test_a_node_two_failures_hold_up_is_shared_between_them_and_one_holds_up_alone():
    report = attribute_graph(diamond())
    first, second = report.roots
    assert (first.node_id, second.node_id) == ("A", "B")
    assert (first.responsibility, second.responsibility) == (3.0, 2.0)
    assert report.total_responsibility == 5.0 and report.root_count == 2
    assert (first.blocked_count, second.blocked_count) == (3, 2)
    assert first.released_alone == ["E"] and second.released_alone == []
    assert first.also_requires == ["B"] and second.also_requires == ["A"]
    paths = {item.node_id: item.path for item in report.blocked}
    assert paths == {"C": ["A", "C"], "D": ["A", "C", "D"], "E": ["A", "E"]}
    assert shapley_values(diamond(), ["A", "B"]) == {"A": 3.0, "B": 2.0}


def test_one_node_can_be_explained_on_its_own():
    report = attribute_graph(diamond(), node_id="D")
    (item,) = report.blocked
    assert item.node_id == "D" and item.root_causes == ["A", "B"]
    assert report.blocked_omitted == 2 and report.blocked_count == 3


def test_a_node_that_is_not_blocked_by_a_dependency_cannot_be_explained():
    graph = diamond()
    unknown = (
        re.escape('Node "ghost" is not in the graph; nodes: ') + r"\['A', 'B', 'C', 'D', 'E'\]"
    )
    with pytest.raises(ValueError, match=unknown):
        attribute_graph(graph, node_id="ghost")
    with pytest.raises(
        ValueError, match=r'Node "A" is not blocked by a dependency \(status failed\)'
    ):
        attribute_graph(graph, node_id="A")


def test_the_listings_are_bounded_and_say_how_much_was_left_out():
    children = [n(f"child{index:03d}", ["root"]) for index in range(300)]
    star = failing_graph([n("root"), *children], {"root"})
    report = attribute_graph(star)
    (root,) = report.roots
    assert root.responsibility == 301.0 and root.blocked_count == 300
    assert len(root.released_alone) == MAX_RELEASED_LISTED
    assert root.released_alone_omitted == 300 - MAX_RELEASED_LISTED
    assert len(report.blocked) == MAX_ATTRIBUTED_BLOCKED
    assert report.blocked_omitted == 300 - MAX_ATTRIBUTED_BLOCKED
    independent = failing_graph(
        [n(f"solo{index:03d}") for index in range(60)], {f"solo{index:03d}" for index in range(60)}
    )
    many = attribute_graph(independent)
    assert len(many.roots) == MAX_ATTRIBUTED_ROOTS and many.roots_omitted == 10
    assert many.root_count == 60 and many.total_responsibility == 60.0


def test_a_blocked_node_with_no_recorded_cause_is_listed_rather_than_guessed():
    graph = failing_graph([n("A"), n("B", ["A"])], {"A"})
    snapshot = graph.snapshot()
    snapshot["statuses"]["A"] = "completed"
    restored = StateGraph.from_snapshot(snapshot)
    report = attribute_graph(restored)
    assert report.roots == [] and report.blocked_without_root == ["B"]
    assert report.blocked[0].root_causes == [] and report.total_responsibility == 0.0


def test_a_failure_that_only_feeds_a_node_through_a_fan_in_edge_is_not_blamed_for_blocking_it():
    nodes = [n("A"), n("B"), n("X", ["B"])]
    feed = GraphEdge(parent_node_id="A", child_node_id="X", kind=GraphEdgeKind.FAN_IN)
    graph = failing_graph(nodes, {"A", "B"}, [feed])
    report = attribute_graph(graph)
    by_node = {root.node_id: root for root in report.roots}
    assert by_node["A"].blocked_count == 0 and by_node["A"].responsibility == 1.0
    assert by_node["B"].blocked_count == 1 and by_node["B"].released_alone == ["X"]
    assert graph.blocked_descendants("A") == ("X",)
    diagnosis = diagnose_graph("run", graph)
    held = {root.node_id: root.blocked_descendants for root in diagnosis.roots}
    assert held == {"A": [], "B": ["X"]} and diagnosis.blocked_downstream == 1


def layered_graph(layers, width, fan, failing, seed):
    generator = random.Random(seed)
    rows = [[f"L{layer:02d}N{index:03d}" for index in range(width)] for layer in range(layers)]
    nodes = [
        n(node_id, [] if layer == 0 else generator.sample(rows[layer - 1], fan))
        for layer, row in enumerate(rows)
        for node_id in row
    ]
    return failing_graph(nodes, set(generator.sample(rows[0], failing)))


def test_attributing_and_diagnosing_a_large_graph_stays_far_below_a_second():
    graph = layered_graph(layers=20, width=100, fan=2, failing=30, seed=7)
    started = time.perf_counter()
    report = attribute_graph(graph)
    diagnosis = diagnose_graph("run", graph)
    elapsed = time.perf_counter() - started
    assert len(graph.nodes) == 2_000 and report.blocked_count > 1_500
    assert diagnosis.blocked_downstream == report.blocked_count
    assert report.total_responsibility == pytest.approx(
        report.root_count + report.blocked_count, abs=TOLERANCE * 100
    )
    assert elapsed < 1.0, f"attributing and diagnosing {len(graph.nodes)} nodes took {elapsed:.2f}s"
