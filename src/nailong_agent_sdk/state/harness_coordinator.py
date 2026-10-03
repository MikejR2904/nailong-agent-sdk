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

from ..foundations.errors import AgentSdkError
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
        self._approval_root = run_root / ".agent-approvals"
        self._store = RunStateStore(run_root)
        self._validator = PlanValidator()
        self._graphs: dict[str, StateGraph] = {}
        self._approvals: dict[str, ApprovalRegistry] = {}
        self._fingerprints: dict[str, tuple[int, int, int] | None] = {}
        self._records: dict[str, RunRecord] = {}
        self._cancelled: set[str] = set()
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
        self._approvals[run_id] = self._approval_registry(run_id)
        return self._save(run_id, plan.plan_id, graph, validation)

    def get_run_state(self, run_id: str) -> RunRecord:
        fingerprint = self._store.fingerprint(run_id)
        cached = self._records.get(run_id)
        if (
            cached is not None
            and fingerprint is not None
            and (self._fingerprints.get(run_id) == fingerprint)
        ):
            # Unchanged on disk since this coordinator verified or wrote it.
            stored = cached
        else:
            stored = self._store.load(run_id)
            self._graphs[run_id] = StateGraph.from_snapshot(stored.graph)
            self._records[run_id] = stored
        self._fingerprints[run_id] = fingerprint
        if run_id not in self._approvals:
            self._approvals[run_id] = self._approval_registry(run_id)
        if stored.cancelled:
            self._cancelled.add(run_id)
        return stored

    def shared_state(self, run_id: str) -> GraphSharedState:
        """Return graph-owned state after integrity-checked rehydration."""

        self.get_run_state(run_id)
        return self._graphs[run_id].shared_state

    def cancel_run(self, run_id: str) -> RunRecord:
        record = self.get_run_state(run_id)
        graph = self._graphs[run_id]
        self._cancelled.add(run_id)
        self._cancel_runnable_nodes(graph)
        return self._save(run_id, record.plan_id, graph, record.plan_validation, cancelled=True)

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
        graph = self._graphs[run_id]
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
        while True:
            if run_id in self._cancelled:
                self._cancel_runnable_nodes(graph)
                return self._save(run_id, record.plan_id, graph, record.plan_validation, True)
            wave = graph.start_runnable_wave(max_parallelism=max_parallelism)
            if not wave:
                return record
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
                    return GraphNodeResult(
                        status=GraphNodeStatus.FAILED,
                        reason=f'Node "{node.node_id}" ({node.kind.value}) executor raised '
                        f"{type(error).__name__}: {error}",
                    )

            results = await asyncio.gather(*(execute_one(node) for node in wave))
            for node, result in zip(wave, results, strict=True):
                graph.mark_terminal(node.node_id, result)
                record = self._save(
                    run_id, record.plan_id, graph, record.plan_validation, record.cancelled
                )

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
        return self.approvals(run_id).submit(approval_id, approved, reason)

    def approvals(self, run_id: str) -> ApprovalRegistry:
        """The run's durable approval registry; works after a process restart."""

        if run_id not in self._approvals:
            self.get_run_state(run_id)  # verifies the run exists and its integrity
        return self._approvals[run_id]

    def _approval_registry(self, run_id: str) -> ApprovalRegistry:
        return ApprovalRegistry(self._approval_root / f"{run_id}.json")

    def resume_run(self, run_id: str) -> RunRecord:
        """Re-read and integrity-verify the persisted graph state before reporting it."""

        return self.get_run_state(run_id)

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
        # The fingerprint check and the save happen under one cross-process lock,
        # so no other writer can slip in between them.
        with self._store.lock(run_id).hold():
            expected = self._fingerprints.get(run_id)
            if expected is not None and self._store.fingerprint(run_id) != expected:
                raise AgentSdkError(
                    "RUN_STATE_CONFLICT",
                    f'Run "{run_id}" was changed on disk by another writer after this coordinator '
                    "last read or wrote it, so saving now would overwrite that change. Use one "
                    "HarnessCoordinator per run root (share it with ControllerRuntime) or call "
                    "get_run_state to reload the run before changing it.",
                    {"run_id": run_id},
                )
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
            self._store.save(record)
            self._fingerprints[run_id] = self._store.fingerprint(run_id)
            self._records[run_id] = record
            return record
