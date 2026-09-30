# Copyright (c) 2026 David Michael Indraputra

"""Typed state-graph node, edge, event, and shared-state contracts.

Workers receive typed predecessor results and a read-only graph-shared
substrate; they never receive another worker's transcript. This implements the
systems-design requirement that typed state is passed through graph edges
rather than conversation (updated framework design, p. 60).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..foundations.contracts import StrictModel
from .shared_state import (
    DiscoveryRouteDecision,
    DiscoveryRoutingIndex,
    DiscoveryRoutingRefs,
    ExploratoryDiscovery,
    LateralDependencyRequest,
    SharedStateWrite,
    SharedSubstrateSnapshot,
)


class GraphNodeKind(StrEnum):
    AGENT = "agent-invocation"
    GATE = "deterministic-gate"
    FUNCTION = "pure-function"
    ELASTIC = "elastic-node"


class GraphEdgeKind(StrEnum):
    STATIC = "static"
    CONDITIONAL = "conditional"
    DYNAMIC_FAN_OUT = "dynamic-fan-out"
    FAN_IN = "fan-in"


class GraphNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNABLE = "runnable"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class GraphStateConflictKind(StrEnum):
    IMMUTABLE_WRITE_COLLISION = "immutable-write-collision"
    DISCOVERY_SNAPSHOT_MISMATCH = "discovery-snapshot-mismatch"
    DISCOVERY_VERSION_MISMATCH = "discovery-version-mismatch"
    LATE_DISCOVERY = "late-discovery"


class GraphStateConflict(StrictModel):
    sequence: int = Field(ge=1)
    kind: GraphStateConflictKind
    subject_id: str = Field(min_length=1)
    producer_node_id: str | None = None
    consumer_node_id: str | None = None
    reason: str = Field(min_length=1)


TERMINAL_STATUSES = {
    GraphNodeStatus.COMPLETED,
    GraphNodeStatus.FAILED,
    GraphNodeStatus.BLOCKED,
    GraphNodeStatus.CANCELLED,
}


class GraphNode(StrictModel):
    node_id: str = Field(min_length=1)
    kind: GraphNodeKind
    task_id: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    elastic_depth: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    routing_refs: DiscoveryRoutingRefs = Field(default_factory=DiscoveryRoutingRefs)

    @model_validator(mode="after")
    def node_is_well_formed(self) -> GraphNode:
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("node dependencies must be unique")
        if self.node_id in self.dependencies:
            raise ValueError("node cannot depend on itself")
        return self


class GraphEdge(StrictModel):
    parent_node_id: str = Field(min_length=1)
    child_node_id: str = Field(min_length=1)
    kind: GraphEdgeKind = GraphEdgeKind.STATIC
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def edge_has_distinct_endpoints(self) -> GraphEdge:
        if self.parent_node_id == self.child_node_id:
            raise ValueError("graph edge endpoints must be distinct")
        return self


class GraphNodeResult(StrictModel):
    status: GraphNodeStatus
    output: Any | None = None
    reason: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    provenance_hash: str | None = None

    @model_validator(mode="after")
    def terminal_result_only(self) -> GraphNodeResult:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError("node results must be terminal")
        return self


class GraphEvent(StrictModel):
    sequence: int = Field(ge=1)
    node_id: str
    status: GraphNodeStatus
    reason: str | None = None


class GraphSharedState(StrictModel):
    """Immutable-by-key lateral payloads persisted inside ``StateGraph.snapshot``.

    The source snapshot is read-only. Discoveries and values are written only by
    controller-mediated graph operations, not by agent text. A worker receives a
    deep copy in ``GraphNodeExecutionContext`` and cannot mutate the scheduler's
    authoritative state through the executor interface.
    """

    schema_version: str = "graph-shared-state-v1"
    substrate: SharedSubstrateSnapshot
    discoveries: dict[str, ExploratoryDiscovery] = Field(default_factory=dict)
    values: dict[str, SharedStateWrite] = Field(default_factory=dict)
    lateral_dependencies: dict[str, list[LateralDependencyRequest]] = Field(default_factory=dict)
    routing_index: DiscoveryRoutingIndex = Field(default_factory=DiscoveryRoutingIndex)
    route_decisions: list[DiscoveryRouteDecision] = Field(default_factory=list)
    conflicts: list[GraphStateConflict] = Field(default_factory=list)

    @classmethod
    def unbound(cls) -> GraphSharedState:
        """Return a safe placeholder for standalone graph/unit-test construction."""

        return cls(
            substrate=SharedSubstrateSnapshot(
                snapshot_id="graph-unbound",
                version="0",
                content_hash="graph-unbound",
            )
        )


class GraphNodeExecutionContext(StrictModel):
    """Read-only inputs for a scheduled graph node.

    ``dependencies`` is the only channel for upstream node results. The shared
    substrate contains source-backed discoveries and immutable values published
    to the graph. This prevents unplanned reads of sibling result payloads.
    """

    dependencies: dict[str, GraphNodeResult] = Field(default_factory=dict)
    shared_state: GraphSharedState


NodeExecutor = Callable[[GraphNode, GraphNodeExecutionContext], Awaitable[GraphNodeResult]]
