# Copyright (c) 2026 David Michael Indraputra

"""Durable run coordination over the deterministic typed graph.

A `RunRecord.graph` is the authoritative run state. It includes the scheduler
snapshot, terminal node results, typed shared discoveries and values, and
lateral dependency requests. The coordinator does not maintain a second
lateral-state persistence channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from ..tools.approvals import ApprovalRegistry, ApprovalRequest
from .coordination_records import RunRecord, _hash_run
from .graph import StateGraph
from .graph_models import (
    GraphNode,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
    NodeExecutor,
)
from .planning import Plan, PlanValidationReport, PlanValidator
from .run_state_store import RunStateStore
from .shared_state import ExploratoryDiscovery, LateralDependencyRequest, SharedStateWrite


class HarnessCoordinator:
    """Own plan validation, graph state, approvals, cancellation, and persistence.

    A `RunRecord.graph` is the authoritative run state. It includes the scheduler
    snapshot, terminal node results, typed shared discoveries and values, and
    lateral dependency requests. The coordinator does not maintain a second
    lateral-state persistence channel.
    """

    def __init__(self, run_root: Path) -> None:
        self._store = RunStateStore(run_root)
        self._validator = PlanValidator()
        self._graphs: dict[str, StateGraph] = {}
        self._approvals: dict[str, ApprovalRegistry] = {}
        self._counter = 1

    def start_run(self, plan: Plan, *, shared_state: GraphSharedState | None = None) -> RunRecord:
        validation = self._validator.validate(plan)
        if not validation.valid:
            raise ValueError("Plan is invalid; start_run requires a valid deterministic plan.")
        while self._store.exists(f"run-{self._counter}"):
            self._counter += 1
        run_id = f"run-{self._counter}"
        self._counter += 1
        graph = StateGraph(
            [
                GraphNode(
                    node_id=f"node:{task.task_id}",
                    kind="agent-invocation",
                    task_id=task.task_id,
                    dependencies=[f"node:{dependency}" for dependency in task.dependencies],
                    routing_refs=task.routing_refs,
                )
                for task in plan.tasks
            ],
            shared_state=shared_state,
            max_elastic_depth=plan.max_elastic_depth,
            max_elastic_nodes=plan.max_elastic_nodes,
        )
        self._graphs[run_id] = graph
        self._approvals[run_id] = ApprovalRegistry()
        return self._save(run_id, plan.plan_id, graph, validation)

    def get_run_state(self, run_id: str) -> RunRecord:
        if run_id in self._graphs:
            stored = self._store.load(run_id)
            return self._save(
                run_id,
                stored.plan_id,
                self._graphs[run_id],
                stored.plan_validation,
                stored.cancelled,
            )
        stored = self._store.load(run_id)
        self._graphs[run_id] = StateGraph.from_snapshot(stored.graph)
        self._approvals.setdefault(run_id, ApprovalRegistry())
        return stored

    def shared_state(self, run_id: str) -> GraphSharedState:
        """Return graph-owned state after integrity-checked rehydration."""

        self.get_run_state(run_id)
        return self._graphs[run_id].shared_state

    def cancel_run(self, run_id: str) -> RunRecord:
        record = self.get_run_state(run_id)
        graph = self._graphs.get(run_id)
        if graph is not None:
            for node in graph.runnable():
                graph.mark_started(node.node_id)
                graph.mark_terminal(
                    node.node_id,
                    GraphNodeResult(status=GraphNodeStatus.CANCELLED, reason="Run was cancelled."),
                )
            return self._save(run_id, record.plan_id, graph, record.plan_validation, cancelled=True)
        return RunRecord(
            run_id=record.run_id,
            plan_id=record.plan_id,
            graph=record.graph,
            plan_validation=record.plan_validation,
            cancelled=True,
            run_hash=_hash_run(
                record.run_id, record.plan_id, record.graph, record.plan_validation, True
            ),
        )

    def publish_discovery(self, run_id: str, discovery: ExploratoryDiscovery) -> RunRecord:
        """Persist a source-backed discovery in the graph snapshot."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.publish_discovery(discovery)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def write_shared_value(self, run_id: str, state_write: SharedStateWrite) -> RunRecord:
        """Persist an immutable typed shared value in the graph snapshot."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.write_shared_value(state_write)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def request_lateral_dependency(
        self, run_id: str, request: LateralDependencyRequest
    ) -> RunRecord:
        """Record a discovery consumer and its conditional graph edge atomically."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.request_lateral_dependency(request)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def add_lateral_dependency(
        self,
        run_id: str,
        producer_node_id: str,
        consumer_node_id: str,
        discovery_episode_id: str,
    ) -> RunRecord:
        """Compatibility wrapper; graph shared state must already carry the discovery."""

        record = self.get_run_state(run_id)
        graph = self._graphs.get(run_id)
        if graph is None:
            raise ValueError("Lateral graph updates require an active coordinator process.")
        graph.add_lateral_dependency(producer_node_id, consumer_node_id, discovery_episode_id)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def record_node_result(
        self,
        run_id: str,
        node_id: str,
        result: GraphNodeResult,
    ) -> RunRecord:
        """Commit a typed terminal node result through the scheduler state machine."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.mark_started(node_id)
        graph.mark_terminal(node_id, result)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    async def execute_run(
        self,
        run_id: str,
        executors: Mapping[GraphNodeKind, NodeExecutor],
        *,
        max_parallelism: int | None = None,
    ) -> RunRecord:
        """Execute graph waves while persisting starts and terminal commits.

        The scheduler commits terminal results in canonical node-ID order after
        each wave. A persisted `RUNNING` state is therefore always recoverable
        by ``recover_interrupted_run`` rather than being silently replayed.
        """

        record = self.get_run_state(run_id)
        if record.cancelled:
            return record
        graph = self._graphs[run_id]
        if max_parallelism is not None and max_parallelism < 1:
            raise ValueError("max_parallelism must be at least one.")
        while wave := graph.start_runnable_wave(max_parallelism=max_parallelism):
            record = self._save(
                run_id, record.plan_id, graph, record.plan_validation, record.cancelled
            )
            wave_state = graph.shared_state

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
                        graph.execution_context(node.node_id, shared_state=wave_state),
                    )
                except Exception as error:
                    return GraphNodeResult(status=GraphNodeStatus.FAILED, reason=str(error))

            results = await asyncio.gather(*(execute_one(node) for node in wave))
            for node, result in zip(wave, results, strict=True):
                graph.mark_terminal(node.node_id, result)
                record = self._save(
                    run_id, record.plan_id, graph, record.plan_validation, record.cancelled
                )
        return record

    def recover_interrupted_run(
        self,
        run_id: str,
        replayable_node_ids: set[str] = frozenset(),
    ) -> RunRecord:
        """Persist a conservative recovery of interrupted graph agent nodes."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.recover_interrupted(replayable_node_ids)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def submit_approval(
        self,
        run_id: str,
        approval_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> ApprovalRequest:
        registry = self._approvals.get(run_id)
        if registry is None:
            raise ValueError(
                "Approval registry is unavailable after process restart in this increment."
            )
        return registry.submit(approval_id, approved, reason)

    def approvals(self, run_id: str) -> ApprovalRegistry:
        registry = self._approvals.get(run_id)
        if registry is None:
            raise ValueError(f'Run "{run_id}" is not active in this coordinator.')
        return registry

    def resume_run(self, run_id: str) -> RunRecord:
        """Re-read and integrity-verify the persisted graph state before reporting it."""

        return self.get_run_state(run_id)

    def _save(
        self,
        run_id: str,
        plan_id: str,
        graph: StateGraph,
        validation: PlanValidationReport,
        cancelled: bool = False,
    ) -> RunRecord:
        snapshot = graph.snapshot()
        record = RunRecord(
            run_id=run_id,
            plan_id=plan_id,
            graph=snapshot,
            plan_validation=validation,
            cancelled=cancelled,
            run_hash=_hash_run(run_id, plan_id, snapshot, validation, cancelled),
        )
        self._store.save(record)
        return record
