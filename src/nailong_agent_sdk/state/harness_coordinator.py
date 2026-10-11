# Copyright (c) 2026 David Michael Indraputra

"""Durable run coordination over the deterministic typed graph.

A `RunRecord.graph` is the authoritative run state. It includes the scheduler
snapshot, terminal node results, typed shared discoveries and values, and
lateral dependency requests. The coordinator does not maintain a second
lateral-state persistence channel.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Concatenate

from ..tools.approvals import ApprovalRegistry, ApprovalRequest, ApprovalStatus
from .coordination_records import RunRecord, _hash_run
from .elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
from .graph import StateGraph, resolve_parallelism
from .graph_models import (
    GraphNode,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
    NodeExecutor,
    ReplayPolicy,
)
from .planning import Plan, PlanValidationReport, PlanValidator, render_plan_errors
from .run_state_store import RunStateStore
from .shared_state import ExploratoryDiscovery, LateralDependencyRequest, SharedStateWrite


def _serialized[**P, R](
    method: Callable[Concatenate[HarnessCoordinator, str, P], R],
) -> Callable[Concatenate[HarnessCoordinator, str, P], R]:
    @functools.wraps(method)
    def wrapper(self: HarnessCoordinator, run_id: str, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if not self._store.exists(run_id):
            return method(self, run_id, *args, **kwargs)
        with self._store.locked(run_id):
            return method(self, run_id, *args, **kwargs)

    return wrapper


def _approvals_resolved(requests: list[ApprovalRequest]) -> bool:
    if not requests:
        return False
    if any(request.status is ApprovalStatus.PENDING for request in requests):
        return False
    return any(request.status is ApprovalStatus.APPROVED for request in requests)


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
        self._fingerprints: dict[str, tuple[int, int, int] | None] = {}
        self._cancelled: set[str] = set()
        self._counter = 1

    def start_run(
        self,
        plan: Plan,
        *,
        shared_state: GraphSharedState | None = None,
        elastic_depth_ceiling: int = MAX_ELASTIC_DEPTH_LIMIT,
        elastic_nodes_ceiling: int = MAX_ELASTIC_NODES_LIMIT,
    ) -> RunRecord:
        validation = self._validator.validate(plan)
        if not validation.valid:
            raise ValueError(
                f'Plan "{plan.plan_id}" is invalid; start_run requires a valid deterministic '
                f"plan: {render_plan_errors(validation.errors)}"
            )
        run_id, self._counter = self._store.reserve_run_id(start=self._counter)
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
            elastic_depth_ceiling=elastic_depth_ceiling,
            elastic_nodes_ceiling=elastic_nodes_ceiling,
        )
        self._graphs[run_id] = graph
        self._approval_registry(run_id)
        return self._save(run_id, plan.plan_id, graph, validation)

    def get_run_state(self, run_id: str) -> RunRecord:
        fingerprint = self._store.fingerprint(run_id)
        stored = self._store.load(run_id)
        if run_id not in self._graphs or self._fingerprints.get(run_id) != fingerprint:
            self._graphs[run_id] = StateGraph.from_snapshot(stored.graph)
        self._fingerprints[run_id] = fingerprint
        self._approval_registry(run_id)
        if stored.cancelled:
            self._cancelled.add(run_id)
        return stored

    def shared_state(self, run_id: str) -> GraphSharedState:
        """Return graph-owned state after integrity-checked rehydration."""

        self.get_run_state(run_id)
        return self._graphs[run_id].shared_state

    @_serialized
    def cancel_run(self, run_id: str) -> RunRecord:
        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        self._cancelled.add(run_id)
        self._cancel_runnable_nodes(graph)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, cancelled=True)

    @_serialized
    def publish_discovery(self, run_id: str, discovery: ExploratoryDiscovery) -> RunRecord:
        """Persist a source-backed discovery in the graph snapshot."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.publish_discovery(discovery)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
    def write_shared_value(self, run_id: str, state_write: SharedStateWrite) -> RunRecord:
        """Persist an immutable typed shared value in the graph snapshot."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.write_shared_value(state_write)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
    def request_lateral_dependency(
        self, run_id: str, request: LateralDependencyRequest
    ) -> RunRecord:
        """Record a discovery consumer and its conditional graph edge atomically."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.request_lateral_dependency(request)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
    def add_lateral_dependency(
        self,
        run_id: str,
        producer_node_id: str,
        consumer_node_id: str,
        discovery_episode_id: str,
    ) -> RunRecord:
        """Compatibility wrapper; graph shared state must already carry the discovery."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.add_lateral_dependency(producer_node_id, consumer_node_id, discovery_episode_id)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
    def grant_elastic_capacity(
        self,
        run_id: str,
        *,
        max_elastic_depth: int | None = None,
        max_elastic_nodes: int | None = None,
        reason: str,
    ) -> RunRecord:
        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.grant_elastic_capacity(
            max_elastic_depth=max_elastic_depth,
            max_elastic_nodes=max_elastic_nodes,
            reason=reason,
        )
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
    def decline_elastic_requests(self, run_id: str, parent_node_id: str, reason: str) -> RunRecord:
        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.decline_elastic_requests(parent_node_id, reason)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
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
        each wave, and one save persists them together with the start of the
        next wave. A persisted `RUNNING` state is therefore always recoverable
        by ``recover_interrupted_run`` rather than being silently replayed.
        """

        record = self.get_run_state(run_id)
        if record.cancelled:
            return record
        graph = self._graphs[run_id]
        limit = resolve_parallelism(max_parallelism)
        unsaved = False
        while True:
            if run_id in self._cancelled:
                self._cancel_runnable_nodes(graph)
                return self._save(run_id, record.plan_id, graph, record.plan_validation, True)
            wave = graph.start_runnable_wave(max_parallelism=limit)
            if not wave:
                if unsaved:
                    return self._save(
                        run_id, record.plan_id, graph, record.plan_validation, record.cancelled
                    )
                return record
            record = self._save(
                run_id, record.plan_id, graph, record.plan_validation, record.cancelled
            )
            wave_state = graph.shared_state
            results = await asyncio.gather(
                *(graph.execute_node(node, executors, shared_state=wave_state) for node in wave)
            )
            for node, result in zip(wave, results, strict=True):
                graph.mark_terminal(node.node_id, result)
            unsaved = True

    @_serialized
    def recover_interrupted_run(
        self,
        run_id: str,
        replayable_node_ids: Collection[str] = frozenset(),
        *,
        is_replayable: ReplayPolicy | None = None,
    ) -> RunRecord:
        """Persist a conservative recovery of interrupted graph agent nodes."""

        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.recover_interrupted(replayable_node_ids, is_replayable=is_replayable)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def submit_approval(
        self,
        run_id: str,
        approval_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> ApprovalRequest:
        return self.approvals(run_id).submit(approval_id, approved, reason)

    def approvals(self, run_id: str) -> ApprovalRegistry:
        if not self._store.exists(run_id):
            raise ValueError(f'Run "{run_id}" is unknown.')
        return self._approval_registry(run_id)

    @_serialized
    def resume_run(self, run_id: str) -> RunRecord:
        """Re-read and integrity-verify the run, then re-open nodes whose approvals are decided.

        A node that ended blocked is re-opened once every approval it requested
        has a decision and at least one was granted, so ``execute_run`` can retry it.
        """

        record = self.get_run_state(run_id)
        if record.cancelled:
            return record
        registry = self._approval_registry(run_id)
        graph = self._graphs[run_id]
        decided = [
            node_id
            for node_id in graph.blocked_nodes()
            if _approvals_resolved(
                [item for item in registry.list(run_id) if item.node_id == node_id]
            )
        ]
        if not decided:
            return record
        graph.reopen_blocked(decided)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    @_serialized
    def reopen_blocked_nodes(self, run_id: str, node_ids: list[str] | None = None) -> RunRecord:
        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        graph.reopen_blocked(node_ids)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, record.cancelled)

    def _approval_registry(self, run_id: str) -> ApprovalRegistry:
        registry = self._approvals.get(run_id)
        if registry is None:
            registry = ApprovalRegistry(self._store.approvals_path(run_id))
            self._approvals[run_id] = registry
        return registry

    @staticmethod
    def _cancel_runnable_nodes(graph: StateGraph) -> None:
        for node in graph.runnable():
            graph.mark_started(node.node_id)
            graph.mark_terminal(
                node.node_id,
                GraphNodeResult(status=GraphNodeStatus.CANCELLED, reason="Run was cancelled."),
            )

    def _save(
        self,
        run_id: str,
        plan_id: str,
        graph: StateGraph,
        validation: PlanValidationReport,
        cancelled: bool = False,
    ) -> RunRecord:
        cancelled = cancelled or run_id in self._cancelled
        snapshot = graph.snapshot()
        record = RunRecord(
            run_id=run_id,
            plan_id=plan_id,
            graph=snapshot,
            plan_validation=validation,
            cancelled=cancelled,
            run_hash=_hash_run(run_id, plan_id, snapshot, validation, cancelled),
        )
        try:
            self._store.save(record)
        except BaseException:
            self._graphs.pop(run_id, None)
            self._fingerprints.pop(run_id, None)
            raise
        self._fingerprints[run_id] = self._store.fingerprint(run_id)
        return record
