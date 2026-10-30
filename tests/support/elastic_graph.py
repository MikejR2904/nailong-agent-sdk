from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeStatus
from tests.support.plans import AGENT, arun

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
