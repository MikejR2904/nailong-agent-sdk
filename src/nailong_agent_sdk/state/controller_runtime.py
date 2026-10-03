# Copyright (c) 2026 David Michael Indraputra

"""Durable façade joining vertical controller authority and graph-owned run state.

The controller binds source snapshot, plan, approval, dispatch, repair, and
escalation. Lateral data are published into `StateGraph.shared_state` via
`HarnessCoordinator`; this class deliberately owns no secondary discovery
substrate and never permits agent-to-agent conversation.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..observability.metrics import record_metric_value, register_standard_metric_definitions
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..observability.telemetry_store import TelemetryStore
from .coordination_records import RunRecord
from .graph_models import (
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
    NodeExecutor,
)
from .harness_coordinator import HarnessCoordinator
from .orchestration import ControllerStateMachine, ControllerStateStore
from .orchestration_models import (
    ComplexityRoutingRules,
    ControllerPhase,
    ControllerRecord,
    GapMetadata,
    SkillToolProfile,
    WorkflowArchitecture,
)
from .planning import Plan
from .project_state_models import (
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
    canonical_hash,
)
from .stage_gates import StageCompletenessDecision, StageCompletenessGate, StageCompletenessPolicy


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
        )
        self._controllers[controller_id] = machine
        self._controller_store.save(machine.record)
        self._emit(machine.record, "controller.created", "planning")
        return machine.record

    def get_controller(self, controller_id: str) -> ControllerRecord:
        return self._machine(controller_id).record

    def submit_plan(self, controller_id: str, plan: Plan) -> ControllerRecord:
        machine = self._machine(controller_id)
        record = machine.submit_plan(plan)
        self._controller_store.save(record)
        self._emit(
            record, "controller.plan-presented", record.phase.value, {"plan_id": plan.plan_id}
        )
        return record

    def apply_advisory_architecture(
        self,
        controller_id: str,
        architecture: str,
        reason: str,
    ) -> ControllerRecord:
        """Record a bounded single-to-multi advisory lift before plan approval."""

        machine = self._machine(controller_id)
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

    def approve_plan(
        self, controller_id: str, approved: bool, reason: str | None = None
    ) -> ControllerRecord:
        machine = self._machine(controller_id)
        record = machine.approve_plan(approved, reason)
        self._controller_store.save(record)
        self._emit(
            record,
            "controller.plan-approved",
            record.phase.value,
            {"approved": approved, "reason": reason},
        )
        return record

    def dispatch(self, controller_id: str) -> tuple[ControllerRecord, RunRecord]:
        machine = self._machine(controller_id)
        machine.begin_dispatch()
        if machine.record.plan is None:
            raise ValueError("Controller cannot dispatch without an approved plan.")
        run = self._harness.start_run(
            machine.record.plan,
            shared_state=GraphSharedState(substrate=machine.record.snapshot),
        )
        record = machine.bind_run(run.run_id)
        self._controller_store.save(record)
        self._emit(
            record, "controller.dispatched", record.phase.value, {"graph_run_id": run.run_id}
        )
        return record, run

    def publish_discovery(
        self,
        controller_id: str,
        discovery: ExploratoryDiscovery,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        if machine.record.run_id is None:
            raise ValueError("Discoveries require a dispatched graph run.")
        run = self._harness.publish_discovery(machine.record.run_id, discovery)
        self._emit(
            machine.record,
            "graph.discovery-published",
            "published",
            {"episode_id": discovery.episode_id},
        )
        return run

    def write_shared_value(
        self,
        controller_id: str,
        state_write: SharedStateWrite,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        if machine.record.run_id is None:
            raise ValueError("Shared graph values require a dispatched graph run.")
        run = self._harness.write_shared_value(machine.record.run_id, state_write)
        self._emit(
            machine.record,
            "graph.shared-value-published",
            "published",
            {"key": state_write.key},
        )
        return run

    def record_node_result(
        self,
        controller_id: str,
        node_id: str,
        result: GraphNodeResult,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        if machine.record.run_id is None:
            raise ValueError("Node results require a dispatched graph run.")
        run = self._harness.record_node_result(machine.record.run_id, node_id, result)
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
        run = await self._harness.execute_run(
            machine.record.run_id,
            executors,
            max_parallelism=max_parallelism,
        )
        results = {
            node_id: GraphNodeResult.model_validate(payload)
            for node_id, payload in run.graph["results"].items()
        }
        for node_id in sorted(results):
            self._reduce_graph_result(machine.record, node_id, results[node_id])
        failed = [
            node_id
            for node_id, result in sorted(results.items())
            if result.status is GraphNodeStatus.FAILED
        ]
        if failed and machine.record.phase is ControllerPhase.EXECUTING:
            self._fail_stage(
                machine, f"Graph execution failed at nodes: {', '.join(failed)}", "graph"
            )
        self._emit(
            machine.record,
            "graph.executed",
            "failed" if failed else "completed",
            {"terminal_node_count": len(results), "failed_node_ids": failed},
        )
        return run

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
            self._fail_stage(machine, "; ".join(decision.reasons), "provenance")
        self._emit(
            machine.record, "provenance.checked", "accepted" if decision.accepted else "rejected"
        )
        return decision

    def request_lateral_dependency(
        self,
        controller_id: str,
        request: LateralDependencyRequest,
    ) -> RunRecord:
        machine = self._machine(controller_id)
        if machine.record.run_id is None:
            raise ValueError("Lateral dependencies require a dispatched graph run.")
        run = self._harness.request_lateral_dependency(machine.record.run_id, request)
        self._emit(
            machine.record,
            "graph.lateral-dependency-added",
            "completed",
            {"episode_id": request.discovery_episode_id},
        )
        return run

    def record_stage_failure(self, controller_id: str, reason: str) -> ControllerRecord:
        return self._fail_stage(self._machine(controller_id), reason, "explicit")

    def _fail_stage(
        self, machine: ControllerStateMachine, reason: str, trigger: str
    ) -> ControllerRecord:
        """Enter repair (or escalation), persist it, and record the repair metrics.

        Every trigger, explicit or automatic, goes through here so repair-attempt
        metrics count every repair the controller actually entered.
        """

        record = machine.record_stage_failure(reason)
        self._controller_store.save(record)
        self._emit(
            record,
            "controller.stage-failure",
            record.phase.value,
            {"reason": reason, "trigger": trigger},
        )
        return record

    def evaluate_stage_completeness(
        self,
        controller_id: str,
        policy: StageCompletenessPolicy,
    ) -> StageCompletenessDecision:
        """Evaluate declared state readiness and enter bounded repair/escalation on failure."""

        machine = self._machine(controller_id)
        decision = StageCompletenessGate().evaluate(self.project_state(controller_id), policy)
        if not decision.complete and machine.record.phase is ControllerPhase.EXECUTING:
            self._fail_stage(machine, "; ".join(decision.reasons), "stage-completeness")
        self._emit(
            machine.record,
            "controller.stage-completeness-checked",
            "accepted" if decision.complete else "rejected",
            {"policy_id": policy.policy_id, "reasons": decision.reasons},
        )
        return decision

    def complete(self, controller_id: str) -> ControllerRecord:
        machine = self._machine(controller_id)
        record = machine.complete()
        self._controller_store.save(record)
        self._emit(record, "controller.completed", record.phase.value)
        return record

    def cancel(self, controller_id: str, reason: str) -> ControllerRecord:
        machine = self._machine(controller_id)
        if machine.record.run_id is not None:
            self._harness.cancel_run(machine.record.run_id)
        record = machine.cancel(reason)
        self._controller_store.save(record)
        self._emit(record, "controller.cancelled", record.phase.value, {"reason": reason})
        return record

    def shared_state(self, controller_id: str) -> dict[str, object]:
        """Return the single persisted graph state, including lateral discoveries."""

        machine = self._machine(controller_id)
        if machine.record.run_id is None:
            raise ValueError("Graph state requires a dispatched graph run.")
        return self._harness.shared_state(machine.record.run_id).model_dump(mode="json")

    def project_state(self, controller_id: str) -> ProjectState:
        """Return the current-state working memory for this controller's project."""

        return self._project_state_store.load(self._machine(controller_id).record.project_state_id)

    def _next_controller_id(self) -> str:
        while True:
            candidate = f"controller-{self._counter}"
            self._counter += 1
            if candidate not in self._controllers and not self._controller_store.exists(candidate):
                return candidate

    def _machine(self, controller_id: str) -> ControllerStateMachine:
        if controller_id not in self._controllers:
            record = self._controller_store.load(controller_id)
            self._controllers[controller_id] = ControllerStateMachine.from_record(record)
        return self._controllers[controller_id]

    def _reduce_graph_result(
        self,
        record: ControllerRecord,
        node_id: str,
        result: GraphNodeResult,
    ) -> None:
        """Upsert one terminal graph result into bounded project working state."""

        current = self._project_state_store.load(record.project_state_id)
        mapped_status = _project_work_item_status(result.status.value)
        existing = next(
            (item for item in current.work_items if item.work_item_id == node_id),
            None,
        )
        if existing is not None and existing.status.value == mapped_status:
            return
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


def _project_work_item_status(graph_status: str) -> str:
    return {
        "completed": "completed",
        "blocked": "blocked",
        "failed": "failed",
        "cancelled": "cancelled",
    }[graph_status]


def _graph_result_hash(result: GraphNodeResult) -> str:
    return canonical_hash(result.model_dump(mode="json"))
