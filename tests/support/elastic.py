from nailong_agent_sdk.state.elastic import ElasticSpawnRequest
from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.shared_state import (
    DiscoveryRoutingRefs,
    ExploratoryDiscovery,
    SharedSubstrateSnapshot,
)

SUBSTRATE = SharedSubstrateSnapshot(snapshot_id="s", version="1", content_hash="h")


def req(request_id="a", **overrides):
    values = {
        "request_id": request_id,
        "scope": "the clock tree",
        "instructions": "Trace the clock tree and report every divider.",
        "reason": "The specification section is ambiguous about the dividers.",
    }
    values.update(overrides)
    return ElasticSpawnRequest(**values)


def done_with(*requests, output=None):
    return GraphNodeResult(
        status=GraphNodeStatus.COMPLETED, output=output, spawn_requests=list(requests)
    )


def failed(reason="probe crashed"):
    return GraphNodeResult(status=GraphNodeStatus.FAILED, reason=reason)


def blocked(reason="needs approval"):
    return GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason=reason)


def discovery(episode_id="d1", producer="prod", signals=("sig",)):
    return ExploratoryDiscovery(
        episode_id=episode_id,
        producer_node_id=producer,
        owner_id="o",
        snapshot_id=SUBSTRATE.snapshot_id,
        snapshot_version=SUBSTRATE.version,
        source_spans=["x"],
        description="finding",
        payload={"k": "v"},
        provenance_hash="p",
        affected_refs=DiscoveryRoutingRefs(signal_ids=list(signals)),
    )


def edge_set(graph):
    return {
        (edge["parent_node_id"], edge["child_node_id"], edge["kind"])
        for edge in graph.snapshot()["edges"]
    }


def statuses(graph):
    return {node_id: graph.status(node_id).value for node_id in sorted(graph.nodes)}


def started_order(graph):
    return [event.node_id for event in graph.events if event.status is GraphNodeStatus.RUNNING]
