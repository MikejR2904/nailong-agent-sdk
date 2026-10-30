import random

from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import (
    GraphEdge,
    GraphEdgeKind,
    GraphNodeResult,
    GraphNodeStatus,
)
from tests.support.plans import A, B, C, F, arun, n

CANCELLED = GraphNodeStatus.CANCELLED


def world(seed):
    generator = random.Random(seed)
    count = generator.randrange(4, 16)
    ids = [f"N{index:02d}" for index in range(count)]
    dependencies, edges = {}, []
    for index, node_id in enumerate(ids):
        earlier = ids[:index]
        hard = [parent for parent in earlier if generator.random() < 0.22]
        dependencies[node_id] = hard
        for parent in earlier:
            if parent not in hard and generator.random() < 0.12:
                edges.append(
                    GraphEdge(
                        parent_node_id=parent, child_node_id=node_id, kind=GraphEdgeKind.FAN_IN
                    )
                )
    outcomes = {
        node_id: generator.choices([C, F, B, CANCELLED], weights=[6, 2, 1, 1])[0] for node_id in ids
    }
    return ids, dependencies, edges, outcomes


def execute(ids, dependencies, edges, outcomes):
    graph = StateGraph([n(node_id, dependencies[node_id]) for node_id in ids], list(edges))

    async def run(node, context):
        status = outcomes[node.node_id]
        if status is C:
            return GraphNodeResult(status=C)
        return GraphNodeResult(status=status, reason=f"{node.node_id} ended {status.value}")

    arun(graph.execute({A: run}))
    return graph


def roots_of(graph, ids):
    own = {node_id for node_id in ids if graph.status(node_id) in (F, CANCELLED)}
    return own | set(graph.blocked_nodes())


def parents_of(node_id, dependencies, edges):
    soft = [edge.parent_node_id for edge in edges if edge.child_node_id == node_id]
    return set(dependencies[node_id]) | set(soft)
