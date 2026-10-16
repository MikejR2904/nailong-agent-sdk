import itertools
import random

import pytest

from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeResult
from tests.support.graph_worlds import execute, parents_of, roots_of, world
from tests.support.plans import A, B, C, F, arun, n


@pytest.mark.parametrize("seed", range(120))
def test_what_a_repair_releases_is_exactly_what_re_running_with_that_repair_releases(seed):
    ids, dependencies, edges, outcomes = world(seed)
    graph = execute(ids, dependencies, edges, outcomes)
    sets = graph.root_cause_sets()
    roots = roots_of(graph, ids)
    assert all(rooted and rooted <= roots for rooted in sets.values())
    assert set(sets) == {node_id for node_id in ids if graph.status(node_id) is B} - set(
        graph.blocked_nodes()
    )
    generator = random.Random(seed * 7919)
    pool = sorted(roots)
    subsets = [()] + [
        tuple(generator.sample(pool, generator.randrange(1, len(pool) + 1)))
        for _ in range(4 if pool else 0)
    ]
    for chosen in subsets:
        predicted = set(graph.released_by(chosen))
        repaired = {
            node_id: (C if node_id in chosen or node_id in sets else outcomes[node_id])
            for node_id in ids
        }
        after = execute(ids, dependencies, edges, repaired)
        released = {node_id for node_id in sets if after.status(node_id) is C}
        assert predicted == released, (seed, chosen)
        assert all(after.root_causes(node_id) for node_id in set(sets) - released)


@pytest.mark.parametrize("seed", range(60))
def test_the_blocking_path_walks_from_a_root_to_the_node_through_real_blockers(seed):
    ids, dependencies, edges, outcomes = world(seed)
    graph = execute(ids, dependencies, edges, outcomes)
    roots = roots_of(graph, ids)
    for node_id, caused_by in graph.root_cause_sets().items():
        path = graph.blocking_path(node_id)
        assert path[-1] == node_id and path[0] in roots and path[0] in caused_by
        assert len(set(path)) == len(path)
        for parent, child in itertools.pairwise(path):
            assert parent in parents_of(child, dependencies, edges)
    for root in sorted(roots):
        assert graph.blocking_path(root) == (root,)


def test_a_node_released_only_by_two_repairs_is_not_released_by_either_alone():
    graph = StateGraph([n("A"), n("B"), n("C", ["A", "B"]), n("D", ["C"])])

    async def run(node, context):
        failing = node.node_id in {"A", "B"}
        return GraphNodeResult(status=F if failing else C, reason="x" if failing else None)

    arun(graph.execute({A: run}))
    assert graph.released_by(["A"]) == () and graph.released_by(["B"]) == ()
    assert graph.released_by(["A", "B"]) == ("C", "D")
    assert graph.root_cause_sets() == {
        "C": frozenset({"A", "B"}),
        "D": frozenset({"A", "B"}),
    }


def test_a_repair_names_only_nodes_that_exist():
    graph = StateGraph([n("A")])
    with pytest.raises(ValueError, match=r"Nodes \['ghost'\] are not in the graph."):
        graph.released_by(["ghost"])
    with pytest.raises(ValueError, match='Node "ghost" is not in the graph.'):
        graph.blocking_path("ghost")
