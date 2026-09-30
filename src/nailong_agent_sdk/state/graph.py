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
from collections.abc import Mapping
from typing import Any

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
    ) -> None:
        self._nodes = {node.node_id: node for node in nodes}
        if len(self._nodes) != len(nodes):
            raise ValueError("graph node IDs must be unique")
        self._edges = list(edges or [])
        self._shared_state = shared_state or GraphSharedState.unbound()
        self._max_elastic_depth = max_elastic_depth
        self._max_elastic_nodes = max_elastic_nodes
        self._elastic_nodes = 0
        self._status = {node_id: GraphNodeStatus.PENDING for node_id in self._nodes}
        self._results: dict[str, GraphNodeResult] = {}
        self._events: list[GraphEvent] = []
        self._validate_graph()
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
        )
        statuses = payload.get("statuses", {})
        if set(statuses) != set(graph._nodes):
            raise ValueError("Graph snapshot statuses do not match graph node IDs.")
        graph._status = {node_id: GraphNodeStatus(status) for node_id, status in statuses.items()}
        graph._results = {
            node_id: GraphNodeResult.model_validate(result)
            for node_id, result in payload.get("results", {}).items()
        }
        graph._events = [GraphEvent.model_validate(event) for event in payload.get("events", [])]
        graph._elastic_nodes = sum(
            node.kind is GraphNodeKind.ELASTIC for node in graph._nodes.values()
        )
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
            if max_parallelism < 1:
                raise ValueError("max_parallelism must be at least one.")
            wave = wave[:max_parallelism]
        for node in wave:
            self.mark_started(node.node_id)
        return wave

    def execution_context(
        self,
        node_id: str,
        *,
        shared_state: GraphSharedState | None = None,
    ) -> GraphNodeExecutionContext:
        """Return only declared predecessor results and a frozen state view."""

        if self._status[node_id] is not GraphNodeStatus.RUNNING:
            raise ValueError(f'Node "{node_id}" is not running.')
        dependencies = {
            dependency: self._results[dependency].model_copy(deep=True)
            for dependency in self._dependencies_for(node_id)
        }
        return GraphNodeExecutionContext(
            dependencies=dependencies,
            shared_state=self._filtered_shared_state_for(node_id, shared_state),
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
        self._block_unreachable_nodes()
        self._refresh_runnable()

    async def execute(
        self, executors: Mapping[GraphNodeKind, NodeExecutor]
    ) -> dict[str, GraphNodeResult]:
        """Execute graph waves with a frozen graph-shared-state view per wave."""

        while wave := self.runnable():
            for node in wave:
                self.mark_started(node.node_id)
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
                except Exception as error:  # Convert execution errors into typed terminal state.
                    return GraphNodeResult(status=GraphNodeStatus.FAILED, reason=str(error))

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

    def spawn_elastic_child(self, parent_node_id: str, child: GraphNode) -> None:
        """Add a scoped runtime extension subject to declared plan caps."""

        parent = self._nodes.get(parent_node_id)
        if parent is None:
            raise ValueError(f'Elastic child parent "{parent_node_id}" is unknown.')
        if child.node_id in self._nodes:
            raise ValueError(f'Elastic child "{child.node_id}" already exists.')
        if child.kind is not GraphNodeKind.ELASTIC:
            raise ValueError("Runtime-spawned children must use the elastic-node kind.")
        if self._elastic_nodes >= self._max_elastic_nodes:
            raise ValueError("Elastic node cap reached; controller escalation is required.")
        required_depth = parent.elastic_depth + 1
        if required_depth > self._max_elastic_depth:
            raise ValueError("Elastic depth cap reached; controller escalation is required.")
        if child.elastic_depth != required_depth:
            raise ValueError("Elastic child depth must equal parent depth plus one.")
        if parent_node_id not in child.dependencies:
            raise ValueError("Elastic child must explicitly depend on its spawning parent.")
        unknown = set(child.dependencies) - set(self._nodes)
        if unknown:
            raise ValueError(f"Elastic child has unknown dependencies: {sorted(unknown)}")
        self._nodes[child.node_id] = child
        self._status[child.node_id] = GraphNodeStatus.PENDING
        self._edges.append(
            GraphEdge(
                parent_node_id=parent_node_id,
                child_node_id=child.node_id,
                kind=GraphEdgeKind.DYNAMIC_FAN_OUT,
            )
        )
        self._elastic_nodes += 1
        self._rebuild_routing_index()
        self._refresh_runnable()

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
            self._append_route_decision(
                discovery,
                consumer_node_id=discovery.producer_node_id,
                status=DiscoveryRouteStatus.REJECTED,
                reason="Discovery declares no exact affected references.",
            )
            return
        candidates = set(relevant_postings[0])
        for posting in sorted(relevant_postings[1:], key=len):
            candidates.intersection_update(posting)
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

    def _dependencies_for(self, node_id: str) -> set[str]:
        node_dependencies = set(self._nodes[node_id].dependencies)
        edge_dependencies = {
            edge.parent_node_id
            for edge in self._edges
            if edge.child_node_id == node_id and edge.enabled
        }
        return node_dependencies | edge_dependencies

    def _refresh_runnable(self) -> None:
        for node_id in sorted(self._nodes):
            if self._status[node_id] is not GraphNodeStatus.PENDING:
                continue
            if all(
                self._status[dependency] is GraphNodeStatus.COMPLETED
                for dependency in self._dependencies_for(node_id)
            ):
                self._set_status(node_id, GraphNodeStatus.RUNNABLE)

    def _block_unreachable_nodes(self) -> None:
        changed = True
        while changed:
            changed = False
            for node_id in sorted(self._nodes):
                if self._status[node_id] not in {GraphNodeStatus.PENDING, GraphNodeStatus.RUNNABLE}:
                    continue
                failed_dependency = next(
                    (
                        dependency
                        for dependency in sorted(self._dependencies_for(node_id))
                        if self._status[dependency]
                        in {
                            GraphNodeStatus.FAILED,
                            GraphNodeStatus.BLOCKED,
                            GraphNodeStatus.CANCELLED,
                        }
                    ),
                    None,
                )
                if failed_dependency is not None:
                    self._set_status(
                        node_id,
                        GraphNodeStatus.BLOCKED,
                        f'Dependency "{failed_dependency}" did not complete.',
                    )
                    self._results[node_id] = GraphNodeResult(
                        status=GraphNodeStatus.BLOCKED,
                        reason=f'Dependency "{failed_dependency}" did not complete.',
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
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError(f'Graph contains a dependency cycle at node "{node_id}".')
            if node_id in visited:
                return
            visiting.add(node_id)
            for parent in dependencies[node_id]:
                visit(parent)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in sorted(dependencies):
            visit(node_id)
