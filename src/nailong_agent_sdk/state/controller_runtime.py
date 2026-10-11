# Copyright (c) 2026 David Michael Indraputra

"""Durable façade joining vertical controller authority and graph-owned run state.

The controller binds source snapshot, plan, approval, dispatch, repair, and
escalation. Lateral data are published into `StateGraph.shared_state` via
`HarnessCoordinator`; this class deliberately owns no secondary discovery
substrate and never permits agent-to-agent conversation.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Concatenate

from ..foundations.hashing import canonical_hash
from ..observability.metrics import record_metric_value, register_standard_metric_definitions
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..observability.telemetry_store import TelemetryStore
from .coordination_records import RunRecord
from .elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
from .graph_models import (
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
    NodeExecutor,
    ReplayPolicy,
)
from .harness_coordinator import HarnessCoordinator
from .orchestration import ControllerStateMachine, ControllerStateStore
from .orchestration_models import (
    ComplexityRoutingRules,
    ControllerPhase,
    ControllerRecord,
    GapMetadata,
    ReconcileAction,
    ReconcileReport,
    SkillToolProfile,
    WorkflowArchitecture,
)
from .planning import Plan
from .project_state_models import (
    ArtifactStatus,
    ProjectState,
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
)
from .project_state_store import FileProjectStateStore
from .shared_state import (
    ExploratoryDiscovery,
    LateralDependencyRequest,
    ProvenanceContractGate,
    ProvenanceGateDecision,
    ProvenanceRecord,
    SharedStateWrite,
    SharedSubstrateSnapshot,
)


def _serialized[**P, R](
    method: Callable[Concatenate[ControllerRuntime, str, P], R],
) -> Callable[Concatenate[ControllerRuntime, str, P], R]:
    @functools.wraps(method)
    def wrapper(
        self: ControllerRuntime, controller_id: str, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        if not self._controller_store.exists(controller_id):
            return method(self, controller_id, *args, **kwargs)
        with self._controller_store.locked(controller_id):
            return method(self, controller_id, *args, **kwargs)

    return wrapper


class ControllerRuntime:
    """The downward/upward control path around one graph-owned run state.

    The controller binds source snapshot, plan, approval, dispatch, repair, and
    escalation. Lateral data are published into `StateGraph.shared_state` via
    `HarnessCoordinator`; this class deliberately owns no secondary discovery
    substrate and never permits agent-to-agent conversation.
    """

    def __init__(
        self,
        run_root: Path,
        *,
        telemetry: TelemetryStore | None = None,
        coordinator: HarnessCoordinator | None = None,
        project_state_store: FileProjectStateStore | None = None,
    ) -> None:
        self._controller_store = ControllerStateStore(run_root)
        self._project_state_store = project_state_store or FileProjectStateStore(run_root)
        self._harness = coordinator or HarnessCoordinator(run_root)
        self._controllers: dict[str, ControllerStateMachine] = {}
        self._counter = 1
        self._telemetry = telemetry
        if self._telemetry is not None:
            register_standard_metric_definitions(self._telemetry)

    def create_controller(
        self,
        snapshot: SharedSubstrateSnapshot,
        profile: SkillToolProfile,
        routing_rules: ComplexityRoutingRules,
        gap_metadata: GapMetadata,
        *,
        max_repair_attempts: int,
        elastic_depth_ceiling: int = MAX_ELASTIC_DEPTH_LIMIT,
        elastic_nodes_ceiling: int = MAX_ELASTIC_NODES_LIMIT,
    ) -> ControllerRecord:
        controller_id = self._next_controller_id()
        project_state_id = snapshot.snapshot_id
        self._project_state_store.ensure(
            project_state_id,
            StageStateSchema(schema_id=f"{profile.stage}-v1", stage=profile.stage),
        )
        machine = ControllerStateMachine(
            controller_id,
            snapshot,
            profile,
            routing_rules,
            gap_metadata,
            project_state_id=project_state_id,
            max_repair_attempts=max_repair_attempts,
            elastic_depth_ceiling=elastic_depth_ceiling,
            elastic_nodes_ceiling=elastic_nodes_ceiling,
        )
        self._controller_store.save(machine.record)
        self._controllers[controller_id] = machine
        self._emit(machine.record, "controller.created", "planning")
        return machine.record

    def get_controller(self, controller_id: str) -> ControllerRecord:
        return self._machine(controller_id).record

    def get_run(self, controller_id: str) -> RunRecord | None:
        run_id = self._machine(controller_id).record.run_id
        return None if run_id is None else self._harness.get_run_state(run_id)

    @_serialized
    def submit_plan(self, controller_id: str, plan: Plan) -> ControllerRecord:
        machine = self._machine(controller_id)
        with _transaction(machine):
            record = machine.submit_plan(plan)
            self._controller_store.save(record)
        self._emit(
            record, "controller.plan-presented", record.phase.value, {"plan_id": plan.plan_id}
        )
        return record

    @_serialized
    def apply_advisory_architecture(
        self,
        controller_id: str,
        architecture: str,
        reason: str,
    ) -> ControllerRecord:
        """Record a bounded single-to-multi advisory lift before plan approval."""

        machine = self._machine(controller_id)
        with _transaction(machine):
            record = machine.apply_advisory_architecture(
                WorkflowArchitecture(architecture), reason=reason
            )
            self._controller_store.save(record)
        self._emit(
            record,
            "controller.architecture-advisory-applied",
            record.architecture.value,
            {"reason": reason},
        )
        return record

    @_serialized
    def approve_plan(
        self, controller_id: str, approved: bool, reason: str | None = None
    ) -> ControllerRecord:
        machine = self._machine(controller_id)
        with _transaction(machine):
            record = machine.approve_plan(approved, reason)
            self._controller_store.save(record)
        self._emit(
            record,
            "controller.plan-approved",
            record.phase.value,
            {"approved": approved, "reason": reason},
        )
        return record

    @_serialized
    def dispatch(self, controller_id: str) -> tuple[ControllerRecord, RunRecord]:
        machine = self._machine(controller_id)
        with _transaction(machine):
            machine.begin_dispatch()
            if machine.record.plan is None:
                raise ValueError("Controller cannot dispatch without an approved plan.")
            run = self._harness.start_run(
                machine.record.plan,
                shared_state=GraphSharedState(substrate=machine.record.snapshot),
                elastic_depth_ceiling=machine.record.elastic_depth_ceiling,
                elastic_nodes_ceiling=machine.record.elastic_nodes_ceiling,
            )
            record = machine.bind_run(run.run_id)
            self._controller_store.save(record)
        self._emit(
            record, "controller.dispatched", record.phase.value, {"graph_run_id": run.run_id}
        )
        return record, run

    @_serialized
    def publish_discovery(
        self,
        controller_id: str,
        discovery: ExploratoryDiscovery,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_open_run(machine.record, "Discoveries require")
        run = self._harness.publish_discovery(run_id, discovery)
        self._emit(
            machine.record,
            "graph.discovery-published",
            "published",
            {"episode_id": discovery.episode_id},
        )
        return run

    @_serialized
    def write_shared_value(
        self,
        controller_id: str,
        state_write: SharedStateWrite,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_open_run(machine.record, "Shared graph values require")
        run = self._harness.write_shared_value(run_id, state_write)
        self._emit(
            machine.record,
            "graph.shared-value-published",
            "published",
            {"key": state_write.key},
        )
        return run

    @_serialized
    def record_node_result(
        self,
        controller_id: str,
        node_id: str,
        result: GraphNodeResult,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_open_run(machine.record, "Node results require")
        spawned_before = self._spawn_record_count(run_id)
        run = self._harness.record_node_result(run_id, node_id, result)
        self._project_state_store.apply(
            machine.record.project_state_id,
            StateTransition(
                kind=StateTransitionKind.WORK_ITEM_UPDATED,
                actor=StateAuthority.CONTROLLER,
                action_id=f"graph-result:{node_id}",
                payload={
                    "work_item_id": node_id,
                    "status": _project_work_item_status(result.status.value),
                    "owner": "controller",
                },
                evidence=[
                    StateEvidence(
                        evidence_id=f"graph-result:{node_id}",
                        kind="graph-node-result",
                        content_hash=result.provenance_hash or _graph_result_hash(result),
                    )
                ],
            ),
        )
        self._emit(machine.record, "graph.node-result", result.status.value, {"node_id": node_id})
        self._emit_spawn_records(
            machine.record, run.graph.get("spawn_records", [])[spawned_before:]
        )
        return run

    @_serialized
    def grant_elastic_capacity(
        self,
        controller_id: str,
        *,
        max_elastic_depth: int | None = None,
        max_elastic_nodes: int | None = None,
        reason: str,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_executing_run(machine.record, "Elastic capacity decisions require")
        run = self._harness.grant_elastic_capacity(
            run_id,
            max_elastic_depth=max_elastic_depth,
            max_elastic_nodes=max_elastic_nodes,
            reason=reason,
        )
        grant = run.graph["capacity_grants"][-1]
        self._emit(machine.record, "graph.elastic-capacity-granted", "granted", grant)
        self._emit_spawn_records(
            machine.record,
            [
                item
                for item in run.graph["spawn_records"]
                if item.get("grant_sequence") == grant["sequence"]
            ],
        )
        return run

    @_serialized
    def decline_elastic_requests(
        self, controller_id: str, parent_node_id: str, reason: str
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_executing_run(machine.record, "Elastic capacity decisions require")
        run = self._harness.decline_elastic_requests(run_id, parent_node_id, reason)
        self._emit(
            machine.record,
            "graph.elastic-requests-declined",
            "declined",
            {
                "parent_node_id": parent_node_id,
                "request_ids": [
                    item["request"]["request_id"]
                    for item in run.graph["spawn_records"]
                    if item["parent_node_id"] == parent_node_id and item["status"] == "discarded"
                ],
                "reason": reason,
            },
        )
        return run

    @_serialized
    def recover_interrupted_graph(
        self,
        controller_id: str,
        replayable_node_ids: Collection[str] = frozenset(),
        *,
        is_replayable: ReplayPolicy | None = None,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_executing_run(
            machine.record, "Recovering interrupted graph nodes requires"
        )
        interrupted = sorted(
            node_id
            for node_id, status in self._harness.get_run_state(run_id).graph["statuses"].items()
            if status == GraphNodeStatus.RUNNING.value
        )
        run = self._harness.recover_interrupted_run(
            run_id, replayable_node_ids, is_replayable=is_replayable
        )
        statuses = run.graph["statuses"]
        self._emit(
            machine.record,
            "graph.interrupted-recovered",
            "recovered",
            {
                "replayed_node_ids": [
                    node_id
                    for node_id in interrupted
                    if statuses[node_id] != GraphNodeStatus.FAILED.value
                ],
                "failed_node_ids": [
                    node_id
                    for node_id in interrupted
                    if statuses[node_id] == GraphNodeStatus.FAILED.value
                ],
            },
        )
        return run

    async def execute_graph(
        self,
        controller_id: str,
        executors: Mapping[GraphNodeKind, NodeExecutor],
        *,
        max_parallelism: int | None = None,
    ) -> RunRecord:
        """Execute dispatched graph waves through registered host-owned executors.

        The coordinator persists each wave transition.  This façade reduces the
        resulting terminal node states into controller project state after graph
        execution; it never lets workers mutate that state directly.  A failed
        node triggers the already-bounded controller repair/escalation path.
        """

        machine = self._machine(controller_id)
        if machine.record.phase is not ControllerPhase.EXECUTING:
            raise ValueError("Graph execution requires an executing controller.")
        if machine.record.run_id is None:
            raise ValueError("Graph execution requires a dispatched graph run.")
        spawned_before = self._spawn_record_count(machine.record.run_id)
        run = await self._harness.execute_run(
            machine.record.run_id,
            executors,
            max_parallelism=max_parallelism,
        )
        return self._conclude_graph(controller_id, run, spawned_before)

    @_serialized
    def _conclude_graph(self, controller_id: str, run: RunRecord, spawned_before: int) -> RunRecord:
        machine = self._machine(controller_id)
        self._emit_spawn_records(
            machine.record, run.graph.get("spawn_records", [])[spawned_before:]
        )
        results = _terminal_results(run)
        self._reduce_results(machine.record, results)
        failed = _node_ids_with_status(results, GraphNodeStatus.FAILED)
        blocked = _node_ids_with_status(results, GraphNodeStatus.BLOCKED)
        if failed and machine.record.phase is ControllerPhase.EXECUTING:
            self._record_stage_failure(
                machine, f"Graph execution failed at nodes: {', '.join(failed)}"
            )
        self._emit(
            machine.record,
            "graph.executed",
            "failed" if failed else "blocked" if blocked else "completed",
            {
                "terminal_node_count": len(results),
                "failed_node_ids": failed,
                "blocked_node_ids": blocked,
            },
        )
        return run

    @_serialized
    def verify_provenance_contract(
        self,
        controller_id: str,
        records: list[ProvenanceRecord],
        required_schema_version: str,
    ) -> ProvenanceGateDecision:
        """Mechanically validate a fan-in handoff before accepting stage output."""

        machine = self._machine(controller_id)
        decision = ProvenanceContractGate().verify(
            records,
            expected_snapshot_ids={machine.record.snapshot.snapshot_id},
            required_schema_version=required_schema_version,
        )
        if not decision.accepted and machine.record.phase is ControllerPhase.EXECUTING:
            self._record_stage_failure(machine, "; ".join(decision.reasons))
        self._emit(
            machine.record, "provenance.checked", "accepted" if decision.accepted else "rejected"
        )
        return decision

    @_serialized
    def request_lateral_dependency(
        self,
        controller_id: str,
        request: LateralDependencyRequest,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        run_id = self._require_open_run(machine.record, "Lateral dependencies require")
        run = self._harness.request_lateral_dependency(run_id, request)
        self._emit(
            machine.record,
            "graph.lateral-dependency-added",
            "completed",
            {"episode_id": request.discovery_episode_id},
        )
        return run

    @_serialized
    def record_stage_failure(self, controller_id: str, reason: str) -> ControllerRecord:
        return self._record_stage_failure(self._machine(controller_id), reason)

    def _record_stage_failure(
        self, machine: ControllerStateMachine, reason: str
    ) -> ControllerRecord:
        with _transaction(machine):
            record = machine.record_stage_failure(reason)
            self._controller_store.save(record)
        self._emit(record, "controller.stage-failure", record.phase.value, {"reason": reason})
        return record

    @_serialized
    def complete(self, controller_id: str) -> ControllerRecord:
        machine = self._machine(controller_id)
        if machine.record.phase is ControllerPhase.EXECUTING:
            self._require_completed_graph(machine.record)
        with _transaction(machine):
            record = machine.complete()
            self._controller_store.save(record)
        self._emit(record, "controller.completed", record.phase.value)
        return record

    @_serialized
    def cancel(self, controller_id: str, reason: str) -> ControllerRecord:
        machine = self._machine(controller_id)
        machine.require_cancellable()
        if machine.record.run_id is not None:
            cancelled_run = self._harness.cancel_run(machine.record.run_id)
            self._reduce_results(machine.record, _terminal_results(cancelled_run))
        with _transaction(machine):
            record = machine.cancel(reason)
            self._controller_store.save(record)
        self._emit(record, "controller.cancelled", record.phase.value, {"reason": reason})
        return record

    @_serialized
    def reconcile(self, controller_id: str) -> ReconcileReport:
        machine = self._machine(controller_id)
        record = machine.record
        actions: list[ReconcileAction] = []
        if record.run_id is not None:
            run = self._harness.get_run_state(record.run_id)
            for node_id, applied in self._reduce_results(record, _terminal_results(run)):
                actions.append(
                    ReconcileAction(
                        store="project-state",
                        record_id=record.project_state_id,
                        change=f'work item "{node_id}" set to {applied} from the run result',
                    )
                )
            if run.cancelled and record.phase not in {
                ControllerPhase.COMPLETED,
                ControllerPhase.CANCELLED,
            }:
                with _transaction(machine):
                    machine.cancel(f'Graph run "{record.run_id}" was cancelled.')
                    self._controller_store.save(machine.record)
                actions.append(
                    ReconcileAction(
                        store="controller",
                        record_id=controller_id,
                        change=(
                            f'phase "{record.phase.value}" -> "cancelled" because graph run '
                            f'"{record.run_id}" is cancelled'
                        ),
                    )
                )
        report = ReconcileReport(subject=controller_id, actions=actions)
        if actions:
            self._emit(
                machine.record,
                "controller.reconciled",
                "repaired",
                {"actions": [action.model_dump(mode="json") for action in actions]},
            )
        return report

    def shared_state(self, controller_id: str) -> dict[str, object]:
        """Return the single persisted graph state, including lateral discoveries."""

        machine = self._machine(controller_id)
        if machine.record.run_id is None:
            raise ValueError("Graph state requires a dispatched graph run.")
        return self._harness.shared_state(machine.record.run_id).model_dump(mode="json")

    def project_state(self, controller_id: str) -> ProjectState:
        """Return the current-state working memory for this controller's project."""

        return self._project_state_store.load(self._machine(controller_id).record.project_state_id)

    @_serialized
    def set_artifact_status(
        self,
        controller_id: str,
        relative_path: str,
        status: ArtifactStatus,
        *,
        reason: str,
    ) -> ProjectState:
        machine = self._machine(controller_id)
        project_state_id = machine.record.project_state_id
        with self._project_state_store.locked(project_state_id):
            current = self._project_state_store.load(project_state_id)
            artifact = next(
                (item for item in current.artifacts if item.relative_path == relative_path), None
            )
            if artifact is None or artifact.artifact_id is None:
                raise ValueError(
                    f'Artifact "{relative_path}" is not recorded with an artifact id in the '
                    f'project state "{project_state_id}" of controller "{controller_id}".'
                )
            decision = {
                "relative_path": relative_path,
                "artifact_id": artifact.artifact_id,
                "status": status.value,
            }
            state = self._project_state_store.apply(
                project_state_id,
                StateTransition(
                    kind=StateTransitionKind.ARTIFACT_STATUS_UPDATED,
                    actor=StateAuthority.CONTROLLER,
                    action_id=f"artifact-status:{relative_path}:{status.value}:{current.revision}",
                    payload={**decision, "reason": reason},
                    evidence=[
                        StateEvidence(
                            evidence_id=f"artifact-status:{controller_id}:{current.revision}",
                            kind="controller-artifact-decision",
                            content_hash=canonical_hash(decision),
                        )
                    ],
                ),
            )
        self._emit(
            machine.record,
            "controller.artifact-status-set",
            status.value,
            {"relative_path": relative_path, "artifact_id": artifact.artifact_id, "reason": reason},
        )
        return state

    @_serialized
    def resolve_question(
        self, controller_id: str, question_id: str, *, resolution: str
    ) -> ProjectState:
        machine = self._machine(controller_id)
        project_state_id = machine.record.project_state_id
        decision = {"question_id": question_id, "resolution": resolution}
        with self._project_state_store.locked(project_state_id):
            current = self._project_state_store.load(project_state_id)
            state = self._project_state_store.apply(
                project_state_id,
                StateTransition(
                    kind=StateTransitionKind.QUESTION_RESOLVED,
                    actor=StateAuthority.CONTROLLER,
                    action_id=f"question-resolved:{question_id}:{current.revision}",
                    payload=decision,
                    evidence=[
                        StateEvidence(
                            evidence_id=f"question-resolution:{controller_id}:{current.revision}",
                            kind="controller-question-resolution",
                            content_hash=canonical_hash(decision),
                        )
                    ],
                ),
            )
        self._emit(
            machine.record,
            "controller.question-resolved",
            "resolved",
            {"question_id": question_id, "resolution": resolution},
        )
        return state

    @_serialized
    def clear_blocker(self, controller_id: str, blocker_id: str, *, reason: str) -> ProjectState:
        machine = self._machine(controller_id)
        project_state_id = machine.record.project_state_id
        decision = {"blocker_id": blocker_id, "reason": reason}
        with self._project_state_store.locked(project_state_id):
            current = self._project_state_store.load(project_state_id)
            state = self._project_state_store.apply(
                project_state_id,
                StateTransition(
                    kind=StateTransitionKind.BLOCKER_CLEARED,
                    actor=StateAuthority.CONTROLLER,
                    action_id=f"blocker-cleared:{blocker_id}:{current.revision}",
                    payload=decision,
                    evidence=[
                        StateEvidence(
                            evidence_id=f"blocker-clearance:{controller_id}:{current.revision}",
                            kind="controller-blocker-clearance",
                            content_hash=canonical_hash(decision),
                        )
                    ],
                ),
            )
        self._emit(
            machine.record,
            "controller.blocker-cleared",
            "cleared",
            {"blocker_id": blocker_id, "reason": reason},
        )
        return state

    def _spawn_record_count(self, run_id: str) -> int:
        if self._telemetry is None:
            return 0
        return len(self._harness.get_run_state(run_id).graph.get("spawn_records", []))

    def _require_open_run(self, record: ControllerRecord, requirement: str) -> str:
        if record.run_id is None:
            raise ValueError(f"{requirement} a dispatched graph run.")
        if record.phase in {ControllerPhase.COMPLETED, ControllerPhase.CANCELLED}:
            raise ValueError(
                f"{requirement} a controller that is not finished; "
                f'"{record.controller_id}" is in phase "{record.phase.value}".'
            )
        return record.run_id

    def _require_executing_run(self, record: ControllerRecord, requirement: str) -> str:
        if record.run_id is None:
            raise ValueError(f"{requirement} a dispatched graph run.")
        if record.phase is not ControllerPhase.EXECUTING:
            raise ValueError(
                f"{requirement} an executing controller; "
                f'"{record.controller_id}" is in phase "{record.phase.value}".'
            )
        return record.run_id

    def _emit_spawn_records(self, record: ControllerRecord, items: list[dict[str, Any]]) -> None:
        for item in items:
            self._emit(
                record,
                "graph.elastic-spawn",
                item["status"],
                {
                    "sequence": item["sequence"],
                    "parent_node_id": item["parent_node_id"],
                    "request_id": item["request"]["request_id"],
                    "depth": item["depth"],
                    "child_node_id": item["child_node_id"],
                    "join_node_id": item["join_node_id"],
                    "code": item["code"],
                },
            )

    def _next_controller_id(self) -> str:
        controller_id, self._counter = self._controller_store.reserve_controller_id(
            start=self._counter
        )
        return controller_id

    def _require_completed_graph(self, record: ControllerRecord) -> None:
        if record.run_id is None:
            return
        statuses = self._harness.get_run_state(record.run_id).graph["statuses"]
        unfinished = [
            f"{node_id} ({status})"
            for node_id, status in sorted(statuses.items())
            if status != GraphNodeStatus.COMPLETED.value
        ]
        if unfinished:
            raise ValueError(
                f'Controller "{record.controller_id}" cannot complete: graph run '
                f'"{record.run_id}" has nodes that did not complete: {", ".join(unfinished)}.'
            )

    def _machine(self, controller_id: str) -> ControllerStateMachine:
        machine = self._controllers.get(controller_id)
        if machine is None or self._controller_store.changed_since_read(controller_id):
            record = self._controller_store.load(controller_id)
            machine = ControllerStateMachine.from_record(record)
            self._controllers[controller_id] = machine
        return machine

    def _reduce_results(
        self, record: ControllerRecord, results: Mapping[str, GraphNodeResult]
    ) -> list[tuple[str, str]]:
        applied: list[tuple[str, str]] = []
        for node_id in sorted(results):
            status = self._reduce_graph_result(record, node_id, results[node_id])
            if status is not None:
                applied.append((node_id, status))
        return applied

    def _reduce_graph_result(
        self,
        record: ControllerRecord,
        node_id: str,
        result: GraphNodeResult,
    ) -> str | None:
        """Upsert one terminal graph result into bounded project working state."""

        mapped_status = _project_work_item_status(result.status.value)
        with self._project_state_store.locked(record.project_state_id):
            current = self._project_state_store.load(record.project_state_id)
            existing = next(
                (item for item in current.work_items if item.work_item_id == node_id),
                None,
            )
            if existing is not None and existing.status.value == mapped_status:
                return None
            self._project_state_store.apply(
                record.project_state_id,
                StateTransition(
                    kind=StateTransitionKind.WORK_ITEM_UPDATED,
                    actor=StateAuthority.CONTROLLER,
                    action_id=f"graph-execution-result:{node_id}",
                    payload={
                        "work_item_id": node_id,
                        "status": mapped_status,
                        "owner": "controller",
                    },
                    evidence=[
                        StateEvidence(
                            evidence_id=f"graph-execution-result:{node_id}",
                            kind="graph-node-result",
                            content_hash=result.provenance_hash or _graph_result_hash(result),
                        )
                    ],
                ),
            )
            return mapped_status

    def _emit(
        self,
        record: ControllerRecord,
        event_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._telemetry is None:
            return
        event = self._telemetry.emit(
            event_type,
            TelemetryContext(
                run_id=record.run_id or record.controller_id,
                controller_id=record.controller_id,
                stage=record.profile.stage,
            ),
            actor=TelemetryActor(kind="system", identifier="controller-runtime", role="controller"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status=status,
            payload=payload or {},
        )
        if event_type == "controller.stage-failure":
            record_metric_value(
                self._telemetry,
                event.context,
                "controller.repair_attempt_count",
                float(record.repair_attempts),
                "count",
                source_event_id=event.event_id,
            )
            record_metric_value(
                self._telemetry,
                event.context,
                "controller.escalation_count",
                float(record.phase is ControllerPhase.ESCALATED),
                "count",
                source_event_id=event.event_id,
            )


@contextmanager
def _transaction(machine: ControllerStateMachine) -> Iterator[None]:
    before = machine.record
    try:
        yield
    except BaseException:
        machine.record = before
        raise


def _terminal_results(run: RunRecord) -> dict[str, GraphNodeResult]:
    return {
        node_id: GraphNodeResult.model_validate(payload)
        for node_id, payload in run.graph["results"].items()
    }


def _node_ids_with_status(
    results: Mapping[str, GraphNodeResult], status: GraphNodeStatus
) -> list[str]:
    return [node_id for node_id, result in sorted(results.items()) if result.status is status]


def _project_work_item_status(graph_status: str) -> str:
    return {
        "completed": "completed",
        "blocked": "blocked",
        "failed": "failed",
        "cancelled": "cancelled",
    }[graph_status]


def _graph_result_hash(result: GraphNodeResult) -> str:
    return canonical_hash(result.model_dump(mode="json"))
