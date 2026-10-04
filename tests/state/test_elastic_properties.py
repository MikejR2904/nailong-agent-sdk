import json
import random

import pytest

from nailong_agent_sdk.state.elastic import (
    ElasticNodeRole,
    ElasticSpawnRequest,
    GraphSpawnStatus,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus
from tests.support.plans import n

S = GraphNodeStatus
SOFT_PROCEEDS = {S.COMPLETED, S.FAILED}


def random_request(rng, index, visible):
    dependencies = [item for item in sorted(visible) if rng.random() < 0.3]
    if rng.random() < 0.15:
        dependencies.append("ghost")
    return ElasticSpawnRequest(
        request_id=f"r{index}",
        scope="scope",
        instructions="instructions",
        reason="reason",
        dependencies=list(dict.fromkeys(dependencies)),
    )


def lineage_contains(nodes, node, ancestor):
    seen = set()
    while node.elastic is not None and node.node_id not in seen:
        seen.add(node.node_id)
        if node.elastic.parent_node_id == ancestor:
            return True
        node = nodes[node.elastic.parent_node_id]
    return False


def transitive_predecessors(graph, node_id):
    reached, stack = set(), [node_id]
    while stack:
        hard, soft = graph._split_dependencies(stack.pop())
        for item in hard | soft:
            if item not in reached:
                reached.add(item)
                stack.append(item)
    return reached


def assert_invariants(graph, label):
    snapshot = graph.snapshot()
    restored = StateGraph.from_snapshot(json.loads(json.dumps(snapshot)))
    assert restored.snapshot() == snapshot, label
    nodes = graph.nodes
    for node_id in nodes:
        hard, soft = graph._split_dependencies(node_id)
        status = graph.status(node_id)
        if status in {S.RUNNABLE, S.RUNNING, S.COMPLETED}:
            assert all(graph.status(item) is S.COMPLETED for item in hard), (label, node_id)
            assert all(graph.status(item) in SOFT_PROCEEDS for item in soft), (label, node_id)
    accepted = {
        record.child_node_id
        for record in graph.spawn_records
        if record.status is GraphSpawnStatus.ACCEPTED
    }
    children = {
        node_id
        for node_id, node in nodes.items()
        if node.elastic is not None and node.elastic.role is ElasticNodeRole.CHILD
    }
    assert children == accepted, label
    assert len(children) <= snapshot["max_elastic_nodes"], label
    for node in nodes.values():
        placeholder = (
            node.elastic is not None
            and node.elastic.role is ElasticNodeRole.JOIN
            and not node.elastic.joins
        )
        assert node.elastic_depth <= snapshot["max_elastic_depth"] or placeholder, label
    for record in graph.spawn_records:
        if record.status is not GraphSpawnStatus.ACCEPTED:
            continue
        join = nodes[record.join_node_id]
        exempt = {record.join_node_id, *join.elastic.joins}
        for node_id, node in nodes.items():
            if node_id in exempt or lineage_contains(nodes, node, record.parent_node_id):
                continue
            hard, soft = graph._split_dependencies(node_id)
            if record.parent_node_id in hard | soft:
                assert record.join_node_id in transitive_predecessors(graph, node_id), (
                    label,
                    node_id,
                )


def random_run(seed):
    rng = random.Random(seed)
    count = rng.randint(2, 6)
    nodes = [n(f"n{i}", [f"n{j}" for j in range(i) if rng.random() < 0.35]) for i in range(count)]
    graph = StateGraph(
        nodes, max_elastic_depth=rng.randint(0, 3), max_elastic_nodes=rng.randint(0, 6)
    )
    counter = 0
    for step in range(60):
        wave = graph.start_runnable_wave()
        if not wave:
            held = sorted(graph._held_join_ids())
            if held and rng.random() < 0.85:
                if rng.random() < 0.5:
                    caps = graph.snapshot()
                    try:
                        graph.grant_elastic_capacity(
                            max_elastic_depth=min(8, caps["max_elastic_depth"] + rng.randint(0, 2)),
                            max_elastic_nodes=min(
                                256, caps["max_elastic_nodes"] + rng.randint(0, 3)
                            ),
                            reason="randomised grant",
                        )
                    except ValueError:
                        pass
                else:
                    graph.decline_elastic_requests(
                        graph.nodes[held[0]].elastic.parent_node_id, "randomised decline"
                    )
                assert_invariants(graph, f"{seed}:{step}:decision")
                continue
            break
        for node in wave:
            context = graph.execution_context(node.node_id)
            roll = rng.random()
            if roll < 0.5:
                result = GraphNodeResult(status=S.COMPLETED, output=node.node_id)
            elif roll < 0.8:
                requests = []
                for _ in range(rng.randint(1, 3)):
                    counter += 1
                    requests.append(random_request(rng, counter, set(context.dependencies)))
                result = GraphNodeResult(
                    status=S.COMPLETED, output=node.node_id, spawn_requests=requests
                )
            elif roll < 0.9:
                result = GraphNodeResult(status=S.FAILED, reason="failed")
            elif roll < 0.97:
                result = GraphNodeResult(status=S.BLOCKED, reason="blocked")
            else:
                result = GraphNodeResult(status=S.CANCELLED, reason="cancelled")
            graph.mark_terminal(node.node_id, result)
        assert_invariants(graph, f"{seed}:{step}")
    return graph


@pytest.mark.parametrize("block", range(4))
def test_random_elastic_runs_keep_the_graph_invariants(block):
    spawned = deferred = joins = 0
    for seed in range(block * 250, (block + 1) * 250):
        graph = random_run(seed)
        spawned += sum(r.status is GraphSpawnStatus.ACCEPTED for r in graph.spawn_records)
        deferred += sum(r.status is GraphSpawnStatus.DEFERRED for r in graph.spawn_records)
        joins += sum(
            node.elastic is not None and node.elastic.role is ElasticNodeRole.JOIN
            for node in graph.nodes.values()
        )
    assert spawned > 100 and deferred > 10 and joins > 100
