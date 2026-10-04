# Copyright (c) 2026 David Michael Indraputra

"""Deterministic typed state graph for cross-agent coordination.

The graph is the sole durable state carrier for a run. Independent nodes
execute concurrently in a wave, while terminal commits occur in sorted
node-ID order. Graph state, node results, discovery payloads, and conditional
lateral edges are serialized together; no parallel durable discovery store
participates in execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..foundations.dependency_graph import deterministic_cycles
from .elastic import (
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
    ElasticCapacity,
    ElasticNodeRole,
    ElasticNodeSpec,
    ElasticProblem,
    ElasticRefusalCode,
    ElasticSpawnRequest,
    GraphCapacityGrant,
    GraphSpawnRecord,
    GraphSpawnStatus,
    capacity_problem,
    check_spawn_request,
    elastic_child_id,
    elastic_join_id,
    validate_elastic_caps,
)
from .graph_models import (
    TERMINAL_STATUSES,
    GraphEdge,
    GraphEdgeKind,
    GraphEvent,
    GraphNode,
    GraphNodeExecutionContext,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
    GraphStateConflict,
    GraphStateConflictKind,
    NodeExecutor,
)
from .shared_state import (
    DiscoveryRouteDecision,
    DiscoveryRouteStatus,
    DiscoveryRoutingIndex,
    ExploratoryDiscovery,
    LateralDependencyRequest,
    SharedStateWrite,
)

DEFAULT_MAX_PARALLELISM = 32
DEPENDENCY_BLOCK_DIAGNOSTIC = "blocked-by-dependency"
HELD_JOIN_DIAGNOSTIC = "elastic-capacity-decision-required"
DECLINED_JOIN_DIAGNOSTIC = "elastic-requests-declined"
_SOFT_PROCEED_STATUSES = frozenset({GraphNodeStatus.COMPLETED, GraphNodeStatus.FAILED})
_SOFT_BLOCKING_STATUSES = frozenset({GraphNodeStatus.BLOCKED, GraphNodeStatus.CANCELLED})
_HARD_BLOCKING_STATUSES = frozenset(
    {GraphNodeStatus.FAILED, GraphNodeStatus.BLOCKED, GraphNodeStatus.CANCELLED}
)


def resolve_parallelism(max_parallelism: int | None) -> int:
    if max_parallelism is None:
        return DEFAULT_MAX_PARALLELISM
    if max_parallelism < 1:
        raise ValueError("max_parallelism must be at least one.")
    return max_parallelism


class StateGraph:
    """A bounded deterministic scheduler and authoritative typed run state.

    Independent nodes execute concurrently in a wave, while terminal commits
    occur in sorted node-ID order. Graph state, node results, discovery payloads,
    and conditional lateral edges are serialized together; no parallel durable
    discovery store participates in execution.
    """

    def __init__(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge] | None = None,
        *,
        shared_state: GraphSharedState | None = None,
        max_elastic_depth: int = 1,
        max_elastic_nodes: int = 2,
        spawn_records: Iterable[GraphSpawnRecord] | None = None,
        capacity_grants: Iterable[GraphCapacityGrant] | None = None,
    ) -> None:
        validate_elastic_caps(max_elastic_depth, max_elastic_nodes)
        self._nodes = {node.node_id: node for node in nodes}
        if len(self._nodes) != len(nodes):
            raise ValueError("graph node IDs must be unique")
        self._edges = list(edges or [])
        self._shared_state = shared_state or GraphSharedState.unbound()
        self._max_elastic_depth = max_elastic_depth
        self._max_elastic_nodes = max_elastic_nodes
        self._elastic_nodes = 0
        self._spawn_records = list(spawn_records or [])
        self._capacity_grants = list(capacity_grants or [])
        self._status = {node_id: GraphNodeStatus.PENDING for node_id in self._nodes}
        self._results: dict[str, GraphNodeResult] = {}
        self._events: list[GraphEvent] = []
        self._validate_graph()
        self._validate_elastic()
        self._rebuild_routing_index()
        self._refresh_runnable()

    @property
    def nodes(self) -> Mapping[str, GraphNode]:
        return dict(self._nodes)

    @property
    def events(self) -> tuple[GraphEvent, ...]:
        return tuple(self._events)

    @property
    def shared_state(self) -> GraphSharedState:
        """Return an isolated read-only copy of graph-owned lateral state."""

        return self._shared_state.model_copy(deep=True)

    @property
    def spawn_records(self) -> tuple[GraphSpawnRecord, ...]:
        return tuple(self._spawn_records)

    @property
    def capacity_grants(self) -> tuple[GraphCapacityGrant, ...]:
        return tuple(self._capacity_grants)

    def status(self, node_id: str) -> GraphNodeStatus:
        return self._status[node_id]

    def result(self, node_id: str) -> GraphNodeResult | None:
        return self._results.get(node_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "nodes": [
                self._nodes[node_id].model_dump(mode="json") for node_id in sorted(self._nodes)
            ],
            "edges": [edge.model_dump(mode="json") for edge in self._edges],
            "shared_state": self._shared_state.model_dump(mode="json"),
            "statuses": {node_id: self._status[node_id].value for node_id in sorted(self._status)},
            "results": {
                node_id: self._results[node_id].model_dump(mode="json")
                for node_id in sorted(self._results)
            },
            "events": [event.model_dump(mode="json") for event in self._events],
            "max_elastic_depth": self._max_elastic_depth,
            "max_elastic_nodes": self._max_elastic_nodes,
            "spawn_records": [record.model_dump(mode="json") for record in self._spawn_records],
            "capacity_grants": [grant.model_dump(mode="json") for grant in self._capacity_grants],
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> StateGraph:
        """Rehydrate graph scheduling and lateral shared state from one record."""

        nodes = [GraphNode.model_validate(item) for item in payload["nodes"]]
        edges = [GraphEdge.model_validate(item) for item in payload.get("edges", [])]
        shared_state_payload = payload.get("shared_state")
        graph = cls(
            nodes,
            edges,
            shared_state=(
                GraphSharedState.model_validate(shared_state_payload)
                if shared_state_payload is not None
                else GraphSharedState.unbound()
            ),
            max_elastic_depth=int(payload.get("max_elastic_depth", 1)),
            max_elastic_nodes=int(payload.get("max_elastic_nodes", 2)),
            spawn_records=[
                GraphSpawnRecord.model_validate(item) for item in payload.get("spawn_records", [])
            ],
            capacity_grants=[
                GraphCapacityGrant.model_validate(item)
                for item in payload.get("capacity_grants", [])
            ],
        )
        statuses = payload.get("statuses", {})
        if set(statuses) != set(graph._nodes):
            raise ValueError("Graph snapshot statuses do not match graph node IDs.")
        graph._status = {node_id: GraphNodeStatus(status) for node_id, status in statuses.items()}
        graph._results = {
            node_id: GraphNodeResult.model_validate(result)
            for node_id, result in payload.get("results", {}).items()
        }
        missing = sorted(
            node_id
            for node_id, status in graph._status.items()
            if status in TERMINAL_STATUSES and node_id not in graph._results
        )
        if missing:
            raise ValueError(
                f"Graph snapshot nodes {missing} have a terminal status but no recorded result."
            )
        graph._events = [GraphEvent.model_validate(event) for event in payload.get("events", [])]
        graph._rebuild_routing_index()
        return graph

    def runnable(self) -> list[GraphNode]:
        self._block_unreachable_nodes()
        self._refresh_runnable()
        return [
            self._nodes[node_id]
            for node_id in sorted(self._nodes)
            if self._status[node_id] is GraphNodeStatus.RUNNABLE
        ]

    def start_runnable_wave(self, *, max_parallelism: int | None = None) -> list[GraphNode]:
        """Transition the current deterministic wave to ``RUNNING`` atomically.

        The coordinator persists a snapshot immediately after this method.  A
        host restart can therefore distinguish an interrupted invocation from a
        node that was never started.  When a caller supplies a limit, only the
        canonical prefix is started; remaining runnable nodes form later waves.
        """

        wave = self.runnable()
        if max_parallelism is not None:
            wave = wave[: resolve_parallelism(max_parallelism)]
        for node in wave:
            self.mark_started(node.node_id)
        return wave

    def blocked_nodes(self) -> list[str]:
        return [
            node_id
            for node_id in sorted(self._nodes)
            if self._status[node_id] is GraphNodeStatus.BLOCKED
            and not self._is_dependency_blocked(node_id)
        ]

    def reopen_blocked(self, node_ids: Iterable[str] | None = None) -> list[str]:
        held = self._held_join_ids()
        candidates = [node_id for node_id in self.blocked_nodes() if node_id not in held]
        requested = candidates if node_ids is None else sorted(set(node_ids))
        for node_id in requested:
            if node_id in held:
                raise ValueError(
                    f'Node "{node_id}" holds deferred elastic requests; call '
                    "grant_elastic_capacity to run them or decline_elastic_requests to drop them "
                    "instead of re-opening it."
                )
        invalid = [node_id for node_id in requested if node_id not in candidates]
        if invalid:
            raise ValueError(
                f"Nodes {invalid} are not blocked by their own result and cannot be re-opened; "
                f"nodes that can be re-opened: {candidates}."
            )
        for node_id in requested:
            self._results.pop(node_id, None)
            self._set_status(
                node_id, GraphNodeStatus.PENDING, "Re-opened after the blocking condition changed."
            )
        for node_id in self._dependency_blocked_descendants(requested):
            self._results.pop(node_id, None)
            self._set_status(
                node_id, GraphNodeStatus.PENDING, "Re-opened with the dependency it waited on."
            )
        self._block_unreachable_nodes()
        self._refresh_runnable()
        return requested

    def execution_context(
        self,
        node_id: str,
        *,
        shared_state: GraphSharedState | None = None,
    ) -> GraphNodeExecutionContext:
        """Return only declared predecessor results and a frozen state view."""

        if self._status[node_id] is not GraphNodeStatus.RUNNING:
            raise ValueError(f'Node "{node_id}" is not running.')
        dependencies: dict[str, GraphNodeResult] = {}
        for dependency in sorted(self._dependencies_for(node_id)):
            result = self._results.get(dependency)
            if result is None:
                raise ValueError(
                    f'Node "{node_id}" depends on "{dependency}", which has no recorded result.'
                )
            dependencies[dependency] = result.model_copy(deep=True)
        spec = self._nodes[node_id].elastic
        joined_requests = (
            {
                child_id: request
                for child_id in spec.joins
                if (request := self._nodes[child_id].elastic.request) is not None
            }
            if spec is not None and spec.role is ElasticNodeRole.JOIN
            else {}
        )
        return GraphNodeExecutionContext(
            dependencies=dependencies,
            shared_state=self._filtered_shared_state_for(node_id, shared_state),
            elastic_capacity=ElasticCapacity(
                node_depth=self._nodes[node_id].elastic_depth,
                max_depth=self._max_elastic_depth,
                nodes_used=self._elastic_nodes,
                max_nodes=self._max_elastic_nodes,
            ),
            elastic_requests=joined_requests,
        )

    def recover_interrupted(self, replayable_node_ids: set[str] = frozenset()) -> list[str]:
        """Resolve persisted ``RUNNING`` nodes after host recovery.

        Only caller-declared idempotent nodes return to ``PENDING``. All other
        interrupted calls become typed failures, preventing silent replay of a
        state-changing action.
        """

        recovered: list[str] = []
        for node_id in sorted(self._nodes):
            if self._status[node_id] is not GraphNodeStatus.RUNNING:
                continue
            if node_id in replayable_node_ids:
                self._set_status(
                    node_id, GraphNodeStatus.PENDING, "Recovered for idempotent replay."
                )
            else:
                reason = "Node was interrupted and is not declared idempotent."
                self._results[node_id] = GraphNodeResult(
                    status=GraphNodeStatus.FAILED,
                    reason=reason,
                    diagnostics=["interrupted-non-idempotent"],
                )
                self._set_status(node_id, GraphNodeStatus.FAILED, reason)
            recovered.append(node_id)
        self._block_unreachable_nodes()
        self._refresh_runnable()
        return recovered

    def mark_started(self, node_id: str) -> None:
        if self._status[node_id] is not GraphNodeStatus.RUNNABLE:
            raise ValueError(f'Node "{node_id}" is not runnable.')
        self._set_status(node_id, GraphNodeStatus.RUNNING)

    def mark_terminal(self, node_id: str, result: GraphNodeResult) -> None:
        if self._status[node_id] is not GraphNodeStatus.RUNNING:
            raise ValueError(f'Node "{node_id}" is not running.')
        self._results[node_id] = result
        self._set_status(node_id, result.status, result.reason)
        if result.spawn_requests:
            self._apply_spawn_requests(node_id, result)
        self._block_unreachable_nodes()
        self._refresh_runnable()

    async def execute(
        self,
        executors: Mapping[GraphNodeKind, NodeExecutor],
        *,
        max_parallelism: int | None = None,
    ) -> dict[str, GraphNodeResult]:
        """Execute graph waves with a frozen graph-shared-state view per wave."""

        limit = resolve_parallelism(max_parallelism)
        while wave := self.start_runnable_wave(max_parallelism=limit):
            wave_state = self.shared_state

            async def execute_one(node: GraphNode) -> GraphNodeResult:
                executor = executors.get(node.kind)
                if executor is None:
                    return GraphNodeResult(
                        status=GraphNodeStatus.FAILED,
                        reason=f'No executor is registered for node kind "{node.kind.value}".',
                    )
                try:
                    return await executor(
                        node,
                        self.execution_context(node.node_id, shared_state=wave_state),
                    )
                except Exception as error:
                    return GraphNodeResult(
                        status=GraphNodeStatus.FAILED,
                        reason=f'Node "{node.node_id}" ({node.kind.value}) executor raised '
                        f"{type(error).__name__}: {error}",
                    )

            wave_results = await asyncio.gather(*(execute_one(node) for node in wave))
            for node, result in zip(wave, wave_results, strict=True):
                self.mark_terminal(node.node_id, result)

        return dict(self._results)

    def publish_discovery(self, discovery: ExploratoryDiscovery) -> None:
        """Publish one closed source-backed discovery into graph-owned state."""

        substrate = self._shared_state.substrate
        if discovery.snapshot_id != substrate.snapshot_id:
            raise ValueError("Discovery references an unexpected graph source snapshot.")
        if discovery.snapshot_version != substrate.version:
            raise ValueError("Discovery references an unexpected graph source version.")
        if not discovery.closed:
            raise ValueError("Only closed exploratory discoveries may enter graph shared state.")
        if discovery.producer_node_id not in self._nodes:
            raise ValueError("Discovery producer must be a known graph node.")
        if self._status[discovery.producer_node_id] is not GraphNodeStatus.COMPLETED:
            raise ValueError("Discovery producer must complete before publication.")
        if discovery.episode_id in self._shared_state.discoveries:
            raise ValueError(f'Discovery episode "{discovery.episode_id}" already exists.')
        discoveries = dict(self._shared_state.discoveries)
        discoveries[discovery.episode_id] = discovery
        self._shared_state = self._shared_state.model_copy(update={"discoveries": discoveries})
        self._route_discovery(discovery)

    def try_publish_discovery(self, discovery: ExploratoryDiscovery) -> GraphStateConflict | None:
        """Publish or return a persisted typed source-version conflict."""

        substrate = self._shared_state.substrate
        if discovery.snapshot_id != substrate.snapshot_id:
            return self._record_conflict(
                GraphStateConflictKind.DISCOVERY_SNAPSHOT_MISMATCH,
                discovery.episode_id,
                producer_node_id=discovery.producer_node_id,
                reason="Discovery snapshot ID does not match the graph substrate snapshot.",
            )
        if discovery.snapshot_version != substrate.version:
            return self._record_conflict(
                GraphStateConflictKind.DISCOVERY_VERSION_MISMATCH,
                discovery.episode_id,
                producer_node_id=discovery.producer_node_id,
                reason="Discovery snapshot version does not match the graph substrate version.",
            )
        self.publish_discovery(discovery)
        return None

    def write_shared_value(self, state_write: SharedStateWrite) -> None:
        """Publish an immutable typed value to the graph state."""

        if state_write.producer_node_id not in self._nodes:
            raise ValueError("Shared-state write producer must be a known graph node.")
        if self._status[state_write.producer_node_id] is not GraphNodeStatus.COMPLETED:
            raise ValueError("Shared-state write producer must complete before publication.")
        if state_write.key in self._shared_state.values:
            raise ValueError(f'Shared-state key "{state_write.key}" is immutable once written.')
        values = dict(self._shared_state.values)
        values[state_write.key] = state_write
        self._shared_state = self._shared_state.model_copy(update={"values": values})

    def try_write_shared_value(self, state_write: SharedStateWrite) -> GraphStateConflict | None:
        """Write an immutable graph value or persist a typed collision record."""

        if state_write.key in self._shared_state.values:
            return self._record_conflict(
                GraphStateConflictKind.IMMUTABLE_WRITE_COLLISION,
                state_write.key,
                producer_node_id=state_write.producer_node_id,
                reason="Graph shared-state keys are immutable once written.",
            )
        self.write_shared_value(state_write)
        return None

    def request_lateral_dependency(self, request: LateralDependencyRequest) -> None:
        """Record a discovery use and conditionally order the consumer node."""

        discovery = self._shared_state.discoveries.get(request.discovery_episode_id)
        if discovery is None:
            raise ValueError("Lateral dependency must cite a graph-published discovery.")
        if discovery.producer_node_id == request.consumer_node_id:
            raise ValueError("Lateral dependency must cross distinct graph nodes.")
        if request.consumer_node_id not in self._nodes:
            raise ValueError("Lateral dependency consumer must be a known graph node.")
        consumers = {
            episode_id: list(requests)
            for episode_id, requests in self._shared_state.lateral_dependencies.items()
        }
        requests = consumers.setdefault(request.discovery_episode_id, [])
        if any(
            item.consumer_node_id == request.consumer_node_id
            and item.consumer_action_id == request.consumer_action_id
            for item in requests
        ):
            raise ValueError("Lateral dependency request already exists.")
        self.add_lateral_dependency(
            discovery.producer_node_id,
            request.consumer_node_id,
            request.discovery_episode_id,
        )
        requests.append(request)
        self._shared_state = self._shared_state.model_copy(
            update={"lateral_dependencies": consumers}
        )

    def grant_elastic_capacity(
        self,
        *,
        max_elastic_depth: int | None = None,
        max_elastic_nodes: int | None = None,
        reason: str,
    ) -> GraphCapacityGrant:
        if not reason.strip():
            raise ValueError("A capacity grant requires a non-blank reason.")
        if max_elastic_depth is None and max_elastic_nodes is None:
            raise ValueError(
                "A capacity grant must raise at least one of max_elastic_depth or "
                "max_elastic_nodes."
            )
        depth = self._max_elastic_depth if max_elastic_depth is None else max_elastic_depth
        nodes = self._max_elastic_nodes if max_elastic_nodes is None else max_elastic_nodes
        if depth < self._max_elastic_depth:
            raise ValueError(
                "A capacity grant cannot lower max_elastic_depth from "
                f"{self._max_elastic_depth} to {depth}."
            )
        if nodes < self._max_elastic_nodes:
            raise ValueError(
                "A capacity grant cannot lower max_elastic_nodes from "
                f"{self._max_elastic_nodes} to {nodes}."
            )
        if depth > MAX_ELASTIC_DEPTH_LIMIT:
            raise ValueError(
                f"A capacity grant cannot set max_elastic_depth {depth}: it is above the hard "
                f"limit {MAX_ELASTIC_DEPTH_LIMIT}."
            )
        if nodes > MAX_ELASTIC_NODES_LIMIT:
            raise ValueError(
                f"A capacity grant cannot set max_elastic_nodes {nodes}: it is above the hard "
                f"limit {MAX_ELASTIC_NODES_LIMIT}."
            )
        grant = GraphCapacityGrant(
            sequence=len(self._capacity_grants) + 1,
            previous_max_depth=self._max_elastic_depth,
            max_depth=depth,
            previous_max_nodes=self._max_elastic_nodes,
            max_nodes=nodes,
            reason=reason,
        )
        self._max_elastic_depth = depth
        self._max_elastic_nodes = nodes
        self._capacity_grants.append(grant)
        self._apply_deferred_batches(grant.sequence)
        self._block_unreachable_nodes()
        self._refresh_runnable()
        return grant

    def decline_elastic_requests(self, parent_node_id: str, reason: str) -> list[GraphSpawnRecord]:
        if not reason.strip():
            raise ValueError("Declining elastic requests requires a non-blank reason.")
        if parent_node_id not in self._nodes:
            raise ValueError(f'Node "{parent_node_id}" is not a node of this graph.')
        indexes = [
            index
            for index, record in enumerate(self._spawn_records)
            if record.parent_node_id == parent_node_id
            and record.status is GraphSpawnStatus.DEFERRED
        ]
        if not indexes:
            raise ValueError(
                f'Node "{parent_node_id}" has no deferred elastic requests to decline.'
            )
        declined: list[GraphSpawnRecord] = []
        for index in indexes:
            record = self._spawn_records[index].model_copy(
                update={
                    "status": GraphSpawnStatus.DISCARDED,
                    "code": ElasticRefusalCode.BATCH_DECLINED,
                    "reason": f"Declined by the controller: {reason}",
                }
            )
            self._spawn_records[index] = record
            declined.append(record)
        join_id = elastic_join_id(parent_node_id)
        self._results[join_id] = GraphNodeResult(
            status=GraphNodeStatus.COMPLETED,
            reason=f"Elastic requests declined: {reason}",
            diagnostics=[DECLINED_JOIN_DIAGNOSTIC],
        )
        self._set_status(join_id, GraphNodeStatus.COMPLETED, f"Elastic requests declined: {reason}")
        for node_id in self._dependency_blocked_descendants([join_id]):
            self._results.pop(node_id, None)
            self._set_status(
                node_id,
                GraphNodeStatus.PENDING,
                "Re-opened after the elastic requests were declined.",
            )
        self._block_unreachable_nodes()
        self._refresh_runnable()
        return declined

    def _held_join_ids(self) -> set[str]:
        return {
            record.join_node_id
            for record in self._spawn_records
            if record.status is GraphSpawnStatus.DEFERRED and record.join_node_id is not None
        }

    def _apply_spawn_requests(self, parent_id: str, result: GraphNodeResult) -> None:
        parent = self._nodes[parent_id]
        requests = result.spawn_requests
        join_id = elastic_join_id(parent_id)
        visible = self._dependencies_for(parent_id)
        taken: set[str] = set()
        problems: list[ElasticProblem | None] = []
        for request in requests:
            problem = check_spawn_request(
                request,
                parent_node_id=parent_id,
                parent_routing_refs=parent.routing_refs,
                visible_dependencies=visible,
                taken_request_ids=taken,
            )
            if problem is None:
                existing = next(
                    (
                        node_id
                        for node_id in (elastic_child_id(parent_id, request.request_id), join_id)
                        if node_id in self._nodes
                    ),
                    None,
                )
                if existing is not None:
                    problem = ElasticProblem(
                        ElasticRefusalCode.NODE_ID_EXISTS,
                        f'Node id "{existing}" already exists in the graph, so request '
                        f'"{request.request_id}" of node "{parent_id}" cannot create it.',
                    )
            if problem is None:
                taken.add(request.request_id)
            problems.append(problem)
        valid = [
            request for request, problem in zip(requests, problems, strict=True) if not problem
        ]
        batch_problem = (
            capacity_problem(
                node_id=parent_id,
                node_depth=parent.elastic_depth,
                max_depth=self._max_elastic_depth,
                nodes_used=self._elastic_nodes,
                max_nodes=self._max_elastic_nodes,
                requested=len(valid),
            )
            if valid
            else None
        )
        depth = parent.elastic_depth + 1
        event_sequence = len(self._events)
        diagnostics: list[str] = []
        accepted: list[tuple[ElasticSpawnRequest, int]] = []
        for request, problem in zip(requests, problems, strict=True):
            sequence = len(self._spawn_records) + 1
            fields: dict[str, Any] = {
                "sequence": sequence,
                "event_sequence": event_sequence,
                "parent_node_id": parent_id,
                "request": request,
                "depth": depth,
            }
            if problem is not None:
                fields.update(
                    status=GraphSpawnStatus.REFUSED, code=problem.code, reason=problem.message
                )
                diagnostics.append(f"elastic-refused:{problem.code.value}:{request.request_id}")
            elif batch_problem is not None:
                fields.update(
                    status=GraphSpawnStatus.DEFERRED,
                    code=batch_problem.code,
                    reason=batch_problem.message,
                    join_node_id=join_id,
                )
                diagnostics.append(
                    f"elastic-deferred:{batch_problem.code.value}:{request.request_id}"
                )
            else:
                fields.update(
                    status=GraphSpawnStatus.ACCEPTED,
                    child_node_id=elastic_child_id(parent_id, request.request_id),
                    join_node_id=join_id,
                )
                accepted.append((request, sequence))
            self._spawn_records.append(GraphSpawnRecord(**fields))
        if accepted:
            child_ids = self._add_children(parent, accepted)
            self._add_join(parent, join_id, child_ids)
            self._catch_up_discoveries(set(child_ids))
        elif batch_problem is not None:
            self._hold_join(parent, join_id, batch_problem)
        if diagnostics:
            self._results[parent_id] = result.model_copy(
                update={"diagnostics": [*result.diagnostics, *diagnostics]}
            )

    def _elastic_root(self, parent: GraphNode) -> str:
        return parent.elastic.root_node_id if parent.elastic is not None else parent.node_id

    def _add_children(
        self, parent: GraphNode, accepted: Sequence[tuple[ElasticSpawnRequest, int]]
    ) -> list[str]:
        root_id = self._elastic_root(parent)
        child_ids: list[str] = []
        for request, sequence in accepted:
            child_id = elastic_child_id(parent.node_id, request.request_id)
            self._nodes[child_id] = GraphNode(
                node_id=child_id,
                kind=GraphNodeKind.ELASTIC,
                task_id=child_id,
                dependencies=[
                    parent.node_id,
                    *[item for item in request.dependencies if item != parent.node_id],
                ],
                elastic_depth=parent.elastic_depth + 1,
                routing_refs=request.routing_refs,
                elastic=ElasticNodeSpec(
                    role=ElasticNodeRole.CHILD,
                    parent_node_id=parent.node_id,
                    root_node_id=root_id,
                    request=request,
                ),
            )
            self._status[child_id] = GraphNodeStatus.PENDING
            self._edges.append(
                GraphEdge(
                    parent_node_id=parent.node_id,
                    child_node_id=child_id,
                    kind=GraphEdgeKind.DYNAMIC_FAN_OUT,
                    metadata={"request_id": request.request_id, "spawn_sequence": sequence},
                )
            )
            child_ids.append(child_id)
        self._elastic_nodes += len(child_ids)
        self._rebuild_routing_index()
        return child_ids

    def _add_join(self, parent: GraphNode, join_id: str, child_ids: Sequence[str]) -> None:
        self._nodes[join_id] = GraphNode(
            node_id=join_id,
            kind=GraphNodeKind.ELASTIC,
            task_id=join_id,
            dependencies=[parent.node_id],
            elastic_depth=parent.elastic_depth + 1,
            elastic=ElasticNodeSpec(
                role=ElasticNodeRole.JOIN,
                parent_node_id=parent.node_id,
                root_node_id=self._elastic_root(parent),
                joins=list(child_ids),
            ),
        )
        self._status[join_id] = GraphNodeStatus.PENDING
        self._attach_children_to_join(join_id, parent.node_id, child_ids)
        self._hold_dependents_behind(parent.node_id, join_id)

    def _attach_children_to_join(
        self, join_id: str, parent_id: str, child_ids: Sequence[str]
    ) -> None:
        for child_id in child_ids:
            self._edges.append(
                GraphEdge(
                    parent_node_id=child_id,
                    child_node_id=join_id,
                    kind=GraphEdgeKind.FAN_IN,
                    metadata={"parent_node_id": parent_id},
                )
            )

    def _hold_join(self, parent: GraphNode, join_id: str, problem: ElasticProblem) -> None:
        self._nodes[join_id] = GraphNode(
            node_id=join_id,
            kind=GraphNodeKind.ELASTIC,
            task_id=join_id,
            dependencies=[parent.node_id],
            elastic_depth=parent.elastic_depth + 1,
            elastic=ElasticNodeSpec(
                role=ElasticNodeRole.JOIN,
                parent_node_id=parent.node_id,
                root_node_id=self._elastic_root(parent),
            ),
        )
        self._status[join_id] = GraphNodeStatus.PENDING
        reason = (
            f'Elastic requests of node "{parent.node_id}" are on hold: {problem.message} '
            "Decide with grant_elastic_capacity to run them or decline_elastic_requests to drop "
            "them."
        )
        self._results[join_id] = GraphNodeResult(
            status=GraphNodeStatus.BLOCKED, reason=reason, diagnostics=[HELD_JOIN_DIAGNOSTIC]
        )
        self._set_status(join_id, GraphNodeStatus.BLOCKED, reason)
        self._hold_dependents_behind(parent.node_id, join_id)

    def _hold_dependents_behind(self, parent_id: str, join_id: str) -> None:
        spec = self._nodes[join_id].elastic
        spawned = {join_id, *(spec.joins if spec is not None else [])}
        for node_id in sorted(self._nodes):
            status = self._status[node_id]
            not_started = status in {
                GraphNodeStatus.PENDING,
                GraphNodeStatus.RUNNABLE,
            } or (status is GraphNodeStatus.BLOCKED and self._is_dependency_blocked(node_id))
            if node_id in spawned or not not_started:
                continue
            hard, soft = self._split_dependencies(node_id)
            if parent_id not in hard | soft:
                continue
            self._edges.append(
                GraphEdge(
                    parent_node_id=join_id,
                    child_node_id=node_id,
                    kind=GraphEdgeKind.STATIC if parent_id in hard else GraphEdgeKind.FAN_IN,
                    metadata={"continuation_of": parent_id},
                )
            )
            if self._status[node_id] is GraphNodeStatus.RUNNABLE:
                self._set_status(
                    node_id, GraphNodeStatus.PENDING, "Awaiting an elastic continuation."
                )

    def _apply_deferred_batches(self, grant_sequence: int) -> None:
        parents: list[str] = []
        for record in self._spawn_records:
            if record.status is GraphSpawnStatus.DEFERRED and record.parent_node_id not in parents:
                parents.append(record.parent_node_id)
        for parent_id in parents:
            indexes = [
                index
                for index, record in enumerate(self._spawn_records)
                if record.parent_node_id == parent_id and record.status is GraphSpawnStatus.DEFERRED
            ]
            parent = self._nodes[parent_id]
            problem = capacity_problem(
                node_id=parent_id,
                node_depth=parent.elastic_depth,
                max_depth=self._max_elastic_depth,
                nodes_used=self._elastic_nodes,
                max_nodes=self._max_elastic_nodes,
                requested=len(indexes),
            )
            if problem is not None:
                for index in indexes:
                    self._spawn_records[index] = self._spawn_records[index].model_copy(
                        update={"code": problem.code, "reason": problem.message}
                    )
                continue
            join_id = elastic_join_id(parent_id)
            child_ids = self._add_children(
                parent,
                [
                    (self._spawn_records[i].request, self._spawn_records[i].sequence)
                    for i in indexes
                ],
            )
            held = self._nodes[join_id]
            self._nodes[join_id] = held.model_copy(
                update={"elastic": held.elastic.model_copy(update={"joins": child_ids})}
            )
            self._attach_children_to_join(join_id, parent_id, child_ids)
            for index, child_id in zip(indexes, child_ids, strict=True):
                self._spawn_records[index] = self._spawn_records[index].model_copy(
                    update={
                        "status": GraphSpawnStatus.ACCEPTED,
                        "child_node_id": child_id,
                        "code": None,
                        "reason": None,
                        "grant_sequence": grant_sequence,
                    }
                )
            self._results.pop(join_id, None)
            self._set_status(join_id, GraphNodeStatus.PENDING, "Elastic capacity was granted.")
            for node_id in self._dependency_blocked_descendants([join_id]):
                self._results.pop(node_id, None)
                self._set_status(
                    node_id, GraphNodeStatus.PENDING, "Re-opened with the held elastic join."
                )
            self._catch_up_discoveries(set(child_ids))

    def _catch_up_discoveries(self, new_node_ids: set[str]) -> None:
        reason = "Exact graph routing references matched a node spawned after publication."
        for discovery in list(self._shared_state.discoveries.values()):
            candidates = self._candidate_consumers(discovery)
            if not candidates:
                continue
            for consumer_node_id in sorted(candidates & new_node_ids):
                self.request_lateral_dependency(
                    LateralDependencyRequest(
                        consumer_node_id=consumer_node_id,
                        consumer_action_id=f"routed:{discovery.episode_id}",
                        discovery_episode_id=discovery.episode_id,
                        reason=reason,
                    )
                )
                self._append_route_decision(
                    discovery,
                    consumer_node_id=consumer_node_id,
                    status=DiscoveryRouteStatus.ACCEPTED,
                    reason=reason,
                )

    def add_lateral_dependency(
        self,
        producer_node_id: str,
        consumer_node_id: str,
        discovery_episode_id: str,
    ) -> None:
        """Add one discovery-triggered conditional edge without worker messaging."""

        producer = self._nodes.get(producer_node_id)
        consumer = self._nodes.get(consumer_node_id)
        if producer is None or consumer is None:
            raise ValueError("Lateral dependency endpoints must be known graph nodes.")
        discovery = self._shared_state.discoveries.get(discovery_episode_id)
        if discovery is None or discovery.producer_node_id != producer_node_id:
            raise ValueError("Lateral edge must cite a discovery in graph shared state.")
        if producer_node_id == consumer_node_id:
            raise ValueError("Lateral dependencies require distinct graph nodes.")
        if self._status[producer_node_id] is not GraphNodeStatus.COMPLETED:
            raise ValueError("Lateral discovery producer must have completed.")
        if self._status[consumer_node_id] in TERMINAL_STATUSES | {GraphNodeStatus.RUNNING}:
            raise ValueError("Lateral dependency cannot be added after consumer execution starts.")
        if any(
            edge.parent_node_id == producer_node_id
            and edge.child_node_id == consumer_node_id
            and edge.metadata.get("discovery_episode_id") == discovery_episode_id
            for edge in self._edges
        ):
            raise ValueError("Lateral dependency already exists.")
        self._edges.append(
            GraphEdge(
                parent_node_id=producer_node_id,
                child_node_id=consumer_node_id,
                kind=GraphEdgeKind.CONDITIONAL,
                metadata={"discovery_episode_id": discovery_episode_id, "lateral": True},
            )
        )
        if self._status[consumer_node_id] is GraphNodeStatus.RUNNABLE:
            self._set_status(
                consumer_node_id, GraphNodeStatus.PENDING, "Awaiting lateral graph discovery."
            )
        self._refresh_runnable()

    def _rebuild_routing_index(self) -> None:
        """Rebuild canonical exact-reference postings from graph node metadata."""

        postings: dict[str, dict[str, list[str]]] = {
            "requirement_postings": {},
            "signal_postings": {},
            "task_postings": {},
            "schema_postings": {},
        }
        for node_id in sorted(self._nodes):
            refs = self._nodes[node_id].routing_refs
            for field_name, values in (
                ("requirement_postings", refs.requirement_ids),
                ("signal_postings", refs.signal_ids),
                ("task_postings", refs.task_ids),
                ("schema_postings", refs.schema_ids),
            ):
                for value in sorted(values):
                    postings[field_name].setdefault(value, []).append(node_id)
        index = DiscoveryRoutingIndex(
            **{
                field_name: {
                    reference: sorted(node_ids) for reference, node_ids in sorted(value.items())
                }
                for field_name, value in postings.items()
            }
        )
        self._shared_state = self._shared_state.model_copy(update={"routing_index": index})

    def _route_discovery(self, discovery: ExploratoryDiscovery) -> None:
        """Deliver exact-reference matches or record deterministic outcomes.

        Empty categories do not constrain routing. When several discovery
        categories are supplied, their non-empty postings are intersected. The
        complete decision ledger stays in graph state for replay and analysis.
        """

        candidates = self._candidate_consumers(discovery)
        if candidates is None:
            self._append_route_decision(
                discovery,
                consumer_node_id=discovery.producer_node_id,
                status=DiscoveryRouteStatus.REJECTED,
                reason="Discovery declares no exact affected references.",
            )
            return
        if not candidates:
            self._append_route_decision(
                discovery,
                consumer_node_id=discovery.producer_node_id,
                status=DiscoveryRouteStatus.REJECTED,
                reason="No graph node satisfies every declared exact routing reference.",
            )
            return
        for consumer_node_id in sorted(candidates):
            if consumer_node_id == discovery.producer_node_id:
                self._append_route_decision(
                    discovery,
                    consumer_node_id=consumer_node_id,
                    status=DiscoveryRouteStatus.REJECTED,
                    reason="A producer cannot route a discovery to itself.",
                )
                continue
            status = self._status[consumer_node_id]
            if status in TERMINAL_STATUSES | {GraphNodeStatus.RUNNING}:
                reason = f"Consumer is already {status.value}."
                self._append_route_decision(
                    discovery,
                    consumer_node_id=consumer_node_id,
                    status=DiscoveryRouteStatus.LATE_DISCOVERY,
                    reason=reason,
                )
                self._record_conflict(
                    GraphStateConflictKind.LATE_DISCOVERY,
                    discovery.episode_id,
                    producer_node_id=discovery.producer_node_id,
                    consumer_node_id=consumer_node_id,
                    reason=reason,
                )
                continue
            self.request_lateral_dependency(
                LateralDependencyRequest(
                    consumer_node_id=consumer_node_id,
                    consumer_action_id=f"routed:{discovery.episode_id}",
                    discovery_episode_id=discovery.episode_id,
                    reason="Exact graph routing references matched.",
                )
            )
            self._append_route_decision(
                discovery,
                consumer_node_id=consumer_node_id,
                status=DiscoveryRouteStatus.ACCEPTED,
                reason="Exact graph routing references matched before consumer start.",
            )

    def _candidate_consumers(self, discovery: ExploratoryDiscovery) -> set[str] | None:
        index = self._shared_state.routing_index
        postings = (
            (discovery.affected_refs.requirement_ids, index.requirement_postings),
            (discovery.affected_refs.signal_ids, index.signal_postings),
            (discovery.affected_refs.task_ids, index.task_postings),
            (discovery.affected_refs.schema_ids, index.schema_postings),
        )
        relevant_postings = [
            sorted({node_id for reference in refs for node_id in mapping.get(reference, [])})
            for refs, mapping in postings
            if refs
        ]
        if not relevant_postings:
            return None
        candidates = set(relevant_postings[0])
        for posting in sorted(relevant_postings[1:], key=len):
            candidates.intersection_update(posting)
        return candidates

    def _append_route_decision(
        self,
        discovery: ExploratoryDiscovery,
        *,
        consumer_node_id: str,
        status: DiscoveryRouteStatus,
        reason: str,
    ) -> None:
        decisions = [*self._shared_state.route_decisions]
        decisions.append(
            DiscoveryRouteDecision(
                discovery_episode_id=discovery.episode_id,
                consumer_node_id=consumer_node_id,
                status=status,
                reason=reason,
                routing_snapshot_id=self._shared_state.substrate.snapshot_id,
                routing_policy_version=self._shared_state.schema_version,
            )
        )
        self._shared_state = self._shared_state.model_copy(update={"route_decisions": decisions})

    def _record_conflict(
        self,
        kind: GraphStateConflictKind,
        subject_id: str,
        *,
        producer_node_id: str | None = None,
        consumer_node_id: str | None = None,
        reason: str,
    ) -> GraphStateConflict:
        conflict = GraphStateConflict(
            sequence=len(self._shared_state.conflicts) + 1,
            kind=kind,
            subject_id=subject_id,
            producer_node_id=producer_node_id,
            consumer_node_id=consumer_node_id,
            reason=reason,
        )
        self._shared_state = self._shared_state.model_copy(
            update={"conflicts": [*self._shared_state.conflicts, conflict]}
        )
        return conflict

    def _filtered_shared_state_for(
        self,
        node_id: str,
        snapshot: GraphSharedState | None,
    ) -> GraphSharedState:
        state = snapshot or self.shared_state
        permitted = {
            episode_id: requests
            for episode_id, requests in state.lateral_dependencies.items()
            if any(request.consumer_node_id == node_id for request in requests)
        }
        discoveries = {
            episode_id: state.discoveries[episode_id]
            for episode_id in sorted(permitted)
            if episode_id in state.discoveries
        }
        filtered_dependencies = {
            episode_id: [request for request in requests if request.consumer_node_id == node_id]
            for episode_id, requests in permitted.items()
        }
        return state.model_copy(
            update={
                "discoveries": discoveries,
                "lateral_dependencies": filtered_dependencies,
            },
            deep=True,
        )

    def _validate_graph(self) -> None:
        for node_id in self._nodes:
            unknown = set(self._nodes[node_id].dependencies) - set(self._nodes)
            if unknown:
                raise ValueError(f'Node "{node_id}" has unknown dependencies: {sorted(unknown)}')
        for edge in self._edges:
            if edge.parent_node_id not in self._nodes or edge.child_node_id not in self._nodes:
                raise ValueError("Every graph edge must reference known nodes.")
        dependencies = {node_id: self._dependencies_for(node_id) for node_id in self._nodes}
        self._assert_acyclic(dependencies)

    def _validate_elastic(self) -> None:
        children = 0
        for node_id in sorted(self._nodes):
            node = self._nodes[node_id]
            spec = node.elastic
            if spec is None:
                continue
            parent = self._nodes.get(spec.parent_node_id)
            if parent is None:
                raise ValueError(
                    f'Elastic node "{node_id}" names unknown parent "{spec.parent_node_id}".'
                )
            if node.elastic_depth != parent.elastic_depth + 1:
                raise ValueError(
                    f'Elastic node "{node_id}" at depth {node.elastic_depth} must be one deeper '
                    f'than its parent "{spec.parent_node_id}" at depth {parent.elastic_depth}.'
                )
            expected_root = self._elastic_root(parent)
            if spec.root_node_id != expected_root:
                raise ValueError(
                    f'Elastic node "{node_id}" names root "{spec.root_node_id}" but its ancestry '
                    f'leads to "{expected_root}".'
                )
            if spec.role is ElasticNodeRole.CHILD:
                children += 1
                continue
            for joined in spec.joins:
                target = self._nodes.get(joined)
                if target is None:
                    raise ValueError(f'Join node "{node_id}" joins unknown node "{joined}".')
                if (
                    target.elastic is None
                    or target.elastic.role is not ElasticNodeRole.CHILD
                    or target.elastic.parent_node_id != spec.parent_node_id
                ):
                    raise ValueError(
                        f'Join node "{node_id}" joins "{joined}", which is not an elastic child '
                        f'of "{spec.parent_node_id}".'
                    )
        accepted = {
            record.child_node_id
            for record in self._spawn_records
            if record.status is GraphSpawnStatus.ACCEPTED
        }
        for node_id in sorted(self._nodes):
            spec = self._nodes[node_id].elastic
            if spec is not None and spec.role is ElasticNodeRole.CHILD and node_id not in accepted:
                raise ValueError(f'Elastic node "{node_id}" has no accepted spawn record.')
        for record in self._spawn_records:
            if (
                record.status is GraphSpawnStatus.ACCEPTED
                and record.child_node_id not in self._nodes
            ):
                raise ValueError(
                    f"Spawn record {record.sequence} accepts "
                    f'"{record.child_node_id}", which is not a node of this graph.'
                )
            if record.status is GraphSpawnStatus.DEFERRED:
                held = self._nodes.get(record.join_node_id or "")
                if held is None or held.elastic is None or held.elastic.joins:
                    raise ValueError(
                        f"Spawn record {record.sequence} is deferred behind "
                        f'"{record.join_node_id}", which is not a held join node.'
                    )
        for node_id in sorted(self._nodes):
            node = self._nodes[node_id]
            placeholder = (
                node.elastic is not None
                and node.elastic.role is ElasticNodeRole.JOIN
                and not node.elastic.joins
            )
            if node.elastic_depth > self._max_elastic_depth and not placeholder:
                raise ValueError(
                    f'Node "{node_id}": elastic depth {node.elastic_depth} exceeds '
                    f"max_elastic_depth {self._max_elastic_depth}."
                )
        if children > self._max_elastic_nodes:
            raise ValueError(
                f"{children} elastic child nodes exceed max_elastic_nodes "
                f"{self._max_elastic_nodes}."
            )
        self._elastic_nodes = children

    def _is_dependency_blocked(self, node_id: str) -> bool:
        result = self._results.get(node_id)
        if result is None:
            return False
        if DEPENDENCY_BLOCK_DIAGNOSTIC in result.diagnostics:
            return True
        return bool(result.reason) and result.reason.startswith('Dependency "')

    def _dependency_blocked_descendants(self, roots: Iterable[str]) -> list[str]:
        children: dict[str, list[str]] = {}
        for node_id in self._nodes:
            for dependency in self._dependencies_for(node_id):
                children.setdefault(dependency, []).append(node_id)
        reached: set[str] = set()
        stack = list(roots)
        while stack:
            for child in children.get(stack.pop(), []):
                if child not in reached and self._is_dependency_blocked(child):
                    reached.add(child)
                    stack.append(child)
        return sorted(
            node_id for node_id in reached if self._status[node_id] is GraphNodeStatus.BLOCKED
        )

    def _split_dependencies(self, node_id: str) -> tuple[set[str], set[str]]:
        hard = set(self._nodes[node_id].dependencies)
        soft: set[str] = set()
        for edge in self._edges:
            if edge.child_node_id != node_id or not edge.enabled:
                continue
            if edge.kind is GraphEdgeKind.FAN_IN:
                soft.add(edge.parent_node_id)
            else:
                hard.add(edge.parent_node_id)
        return hard, soft - hard

    def _dependencies_for(self, node_id: str) -> set[str]:
        hard, soft = self._split_dependencies(node_id)
        return hard | soft

    def _refresh_runnable(self) -> None:
        for node_id in sorted(self._nodes):
            if self._status[node_id] is not GraphNodeStatus.PENDING:
                continue
            hard, soft = self._split_dependencies(node_id)
            if all(self._status[item] is GraphNodeStatus.COMPLETED for item in hard) and all(
                self._status[item] in _SOFT_PROCEED_STATUSES for item in soft
            ):
                self._set_status(node_id, GraphNodeStatus.RUNNABLE)

    def _block_unreachable_nodes(self) -> None:
        changed = True
        while changed:
            changed = False
            for node_id in sorted(self._nodes):
                if self._status[node_id] not in {GraphNodeStatus.PENDING, GraphNodeStatus.RUNNABLE}:
                    continue
                hard, soft = self._split_dependencies(node_id)
                blocker = next(
                    (
                        item
                        for item in sorted(hard)
                        if self._status[item] in _HARD_BLOCKING_STATUSES
                    ),
                    None,
                )
                if blocker is not None:
                    reason = f'Dependency "{blocker}" did not complete.'
                else:
                    blocker = next(
                        (
                            item
                            for item in sorted(soft)
                            if self._status[item] in _SOFT_BLOCKING_STATUSES
                        ),
                        None,
                    )
                    if blocker is None:
                        continue
                    reason = (
                        f'Dependency "{blocker}" is {self._status[blocker].value} and this join '
                        "waits for it to settle."
                    )
                self._set_status(node_id, GraphNodeStatus.BLOCKED, reason)
                self._results[node_id] = GraphNodeResult(
                    status=GraphNodeStatus.BLOCKED,
                    reason=reason,
                    diagnostics=[DEPENDENCY_BLOCK_DIAGNOSTIC],
                )
                changed = True

    def _set_status(
        self,
        node_id: str,
        status: GraphNodeStatus,
        reason: str | None = None,
    ) -> None:
        self._status[node_id] = status
        self._events.append(
            GraphEvent(
                sequence=len(self._events) + 1, node_id=node_id, status=status, reason=reason
            )
        )

    @staticmethod
    def _assert_acyclic(dependencies: Mapping[str, set[str]]) -> None:
        cycles = deterministic_cycles(
            dependencies,
            [(node_id, parent) for node_id, parents in dependencies.items() for parent in parents],
        )
        if cycles:
            raise ValueError(f'Graph contains a dependency cycle at node "{cycles[0][0]}".')
