# Copyright (c) 2026 David Michael Indraputra

"""Compile user configuration into a governed controller and graph execution.

The orchestrator is a deterministic composition layer, not another autonomous
agent.  It validates a user-selected skill/model/tool/permission profile, routes
an already-proposed plan, presents the executable plan for approval, and creates
host-bound graph workers only after that approval.  The underlying controller
continues to own the authoritative vertical lifecycle; ``StateGraph`` continues
to own lateral coordination.

It also implements the framework's deterministic complexity route between
single- and multi-agent workflows (systems design, p. 42), while permitting a
bounded, receipt-backed Jev advisory only to *raise* a single-agent route to
multi-agent execution.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Any

from ...foundations.contracts import AgentDefinition, SkillContext
from ...integrations.jev.architecture import JevArchitectureAdvice, JevArchitectureRouter
from ...observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ...observability.telemetry_store import TelemetryStore
from ...state.controller_runtime import ControllerRuntime
from ...state.orchestration_models import (
    ComplexityRouter,
    SkillToolProfile,
    WorkflowArchitecture,
)
from ...state.planning import ModelTier, Plan, PlanTask, PlanValidator
from ...state.shared_state import SharedSubstrateSnapshot
from ...tools.registry import HarnessToolRegistry
from ..graph_agent_executor import GraphAgentBinding, GraphAgentExecutor
from ..runtime import AgentRuntimeServices
from .models import (
    AgentExecutionProfile,
    OrchestrationPolicy,
    OrchestrationRecord,
    OrchestrationRequest,
    OrchestrationStatus,
    UserModelSelection,
    WorkerAssignment,
)
from .state_store import OrchestrationStateStore


@dataclass(frozen=True)
class GraphAgentBindingContext:
    """Host-only context delivered to a binding factory after approval."""

    assignment: WorkerAssignment
    execution_task: PlanTask
    selected_skills: tuple[SkillContext, ...]
    model: UserModelSelection
    profile: AgentExecutionProfile
    snapshot: SharedSubstrateSnapshot


GraphAgentBindingFactory = Callable[[GraphAgentBindingContext], GraphAgentBinding]


class Orchestrator:
    """Compile user configuration into a governed controller and graph execution.

    The class deliberately never asks a model to create a plan, never selects an
    undisclosed provider, and never turns a Jev answer into an approval.  A host
    first supplies a plan, then explicitly approves the compiled execution plan,
    then supplies executable graph bindings for already-authorized workers.
    """

    def __init__(
        self,
        run_root: Path,
        policy: OrchestrationPolicy,
        *,
        controller_runtime: ControllerRuntime | None = None,
        jev_router: JevArchitectureRouter | None = None,
        telemetry: TelemetryStore | None = None,
        tool_registry: HarnessToolRegistry | None = None,
    ) -> None:
        self._policy = policy
        self._run_root = run_root.resolve()
        self._store = OrchestrationStateStore(self._run_root)
        self._store.save_policy(policy)
        self._telemetry = telemetry or TelemetryStore(self._run_root)
        self._controller_runtime = controller_runtime or ControllerRuntime(
            self._run_root,
            telemetry=self._telemetry,
        )
        self._tool_registry = tool_registry or HarnessToolRegistry()
        self._validate_policy_tool_authority()
        self._jev_router = jev_router
        self._counter = 1

    @classmethod
    def resume(
        cls,
        run_root: Path,
        orchestration_id: str,
        *,
        controller_runtime: ControllerRuntime | None = None,
        telemetry: TelemetryStore | None = None,
        tool_registry: HarnessToolRegistry | None = None,
    ) -> Orchestrator:
        """Reconstruct a deterministic policy shell for a persisted orchestration.

        External Jev evaluators and executable host bindings are intentionally
        excluded: receipt-backed advice is already stored in the record, and a
        host must re-register executable callbacks before graph dispatch.
        """

        store = OrchestrationStateStore(run_root)
        record = store.load(orchestration_id)
        return cls(
            run_root,
            store.load_policy(record.policy_id),
            controller_runtime=controller_runtime,
            telemetry=telemetry,
            tool_registry=tool_registry,
        )

    async def prepare(self, request: OrchestrationRequest) -> OrchestrationRecord:
        """Validate and compile an execution plan before human approval.

        The deterministic route is always calculated first.  Jev receives only
        bounded public planning metadata, and may only lift single-agent routing
        to multi-agent routing under its local confidence policy.
        """

        PlanValidator().assert_valid(request.plan)
        self._validate_request_selection(request)
        deterministic = ComplexityRouter(self._policy.routing_rules).route(request.gap_metadata)
        if (
            deterministic is WorkflowArchitecture.MULTI_AGENT
            and not self._policy.multi_agent_enabled
        ):
            raise ValueError("Policy disables multi-agent execution for this request.")
        advice: JevArchitectureAdvice | None = None
        selected = deterministic
        if self._jev_router is not None:
            advice = await self._jev_router.advise(
                run_id=request.request_id,
                deterministic_architecture=deterministic.value,
                state=_routing_state(request),
            )
            selected = WorkflowArchitecture(advice.architecture)
            if (
                deterministic is WorkflowArchitecture.MULTI_AGENT
                and selected is not WorkflowArchitecture.MULTI_AGENT
            ):
                raise ValueError(
                    "An advisory architecture route may not lower a deterministic route."
                )
        if selected is WorkflowArchitecture.MULTI_AGENT and not self._policy.multi_agent_enabled:
            raise ValueError("Jev cannot override a policy prohibition on multi-agent execution.")
        execution_plan = _execution_plan(request.plan, selected)
        assignments = self._assign_workers(request, execution_plan)
        if len(assignments) > self._policy.max_total_agents:
            raise ValueError("Execution plan exceeds policy max_total_agents.")
        used_skills = {skill_id for assignment in assignments for skill_id in assignment.skill_ids}
        unused_skills = set(request.selected_skill_ids) - used_skills
        if unused_skills:
            raise ValueError(
                "Selected skills are not authorized by any assignment profile: "
                f"{sorted(unused_skills)}"
            )
        orchestration_id = self._next_id()
        record = OrchestrationRecord(
            orchestration_id=orchestration_id,
            request=request,
            policy_id=self._policy.policy_id,
            deterministic_architecture=deterministic,
            architecture=selected,
            routing_advice=advice,
            execution_plan=execution_plan,
            assignments=assignments,
        )
        self._store.save(record)
        self._emit(
            record,
            "orchestration.prepared",
            record.status.value,
            {
                "deterministic_architecture": deterministic.value,
                "selected_architecture": selected.value,
                "assignment_count": len(assignments),
                "jev_advisory_used": advice is not None,
            },
        )
        return record

    def submit_for_approval(self, orchestration_id: str) -> OrchestrationRecord:
        """Create a controller and present the compiled plan to the human designer."""

        record = self.get(orchestration_id)
        if record.status is not OrchestrationStatus.PREPARED:
            raise ValueError("Only a prepared orchestration can be submitted for approval.")
        profile = SkillToolProfile(
            stage=record.request.stage,
            skill_ids=sorted(
                {skill_id for item in record.assignments for skill_id in item.skill_ids}
            ),
            capability_ids=sorted(
                {capability for item in record.assignments for capability in item.capability_ids}
            ),
            source_snapshot_id=record.request.snapshot.snapshot_id,
        )
        controller = self._controller_runtime.create_controller(
            record.request.snapshot,
            profile,
            self._policy.routing_rules,
            record.request.gap_metadata,
            max_repair_attempts=self._policy.max_repair_attempts,
        )
        if controller.architecture is not record.architecture:
            controller = self._controller_runtime.apply_advisory_architecture(
                controller.controller_id,
                record.architecture.value,
                record.routing_advice.reason
                if record.routing_advice is not None
                else "Approved orchestration architecture selection.",
            )
        self._controller_runtime.submit_plan(controller.controller_id, record.execution_plan)
        updated = record.model_copy(
            update={
                "status": OrchestrationStatus.AWAITING_PLAN_APPROVAL,
                "controller_id": controller.controller_id,
            }
        )
        self._store.save(updated)
        self._emit(
            updated,
            "orchestration.plan-presented",
            updated.status.value,
            {"controller_id": controller.controller_id, "plan_id": record.execution_plan.plan_id},
        )
        return updated

    def approve(
        self,
        orchestration_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> OrchestrationRecord:
        """Forward a typed human plan decision to the underlying controller."""

        record = self.get(orchestration_id)
        if record.status is not OrchestrationStatus.AWAITING_PLAN_APPROVAL:
            raise ValueError("The orchestration is not awaiting a plan decision.")
        if record.controller_id is None:
            raise ValueError("Approval requires a bound controller.")
        self._controller_runtime.approve_plan(record.controller_id, approved, reason)
        if approved:
            updated = record.model_copy(update={"status": OrchestrationStatus.APPROVED})
            self._store.save(updated)
            self._emit(
                updated,
                "orchestration.plan-decision",
                "approved",
                {"controller_id": updated.controller_id, "reason": reason},
            )
            return updated
        updated = record.model_copy(update={"status": OrchestrationStatus.PREPARED})
        self._store.save(updated)
        self._emit(
            updated,
            "orchestration.plan-decision",
            "rejected",
            {"controller_id": updated.controller_id, "reason": reason},
        )
        return updated

    async def dispatch_and_execute(
        self,
        orchestration_id: str,
        services: AgentRuntimeServices,
        binding_factory: GraphAgentBindingFactory,
    ) -> OrchestrationRecord:
        """Dispatch an approved graph and run only host-bound, policy-checked workers."""

        record = self.get(orchestration_id)
        if record.status is not OrchestrationStatus.APPROVED:
            raise ValueError("Only an approved orchestration can dispatch.")
        if record.controller_id is None:
            raise ValueError("Dispatch requires a bound controller.")
        if services.run_root.resolve() != self._run_root:
            raise ValueError("AgentRuntimeServices must use the orchestrator run_root.")
        controller = self._controller_runtime.get_controller(record.controller_id)
        if controller.phase.value != "dispatch-ready":
            raise ValueError("The designer must approve the plan before dispatch.")
        controller, run = self._controller_runtime.dispatch(record.controller_id)
        dispatched = record.model_copy(
            update={
                "status": OrchestrationStatus.DISPATCHED,
                "graph_run_id": run.run_id,
            }
        )
        self._store.save(dispatched)
        self._emit(
            dispatched,
            "orchestration.dispatched",
            dispatched.status.value,
            {"controller_id": dispatched.controller_id, "graph_run_id": dispatched.graph_run_id},
        )
        bindings = self._build_bindings(record, binding_factory)
        executor = GraphAgentExecutor(services, bindings)
        executed = await self._controller_runtime.execute_graph(
            controller.controller_id,
            executor.executors(),
            max_parallelism=self._policy.max_parallel_agents,
        )
        updated = dispatched.model_copy(
            update={
                "status": OrchestrationStatus.EXECUTED,
                "graph_run_id": executed.run_id,
            }
        )
        self._store.save(updated)
        self._emit(
            updated,
            "orchestration.executed",
            updated.status.value,
            {"controller_id": updated.controller_id, "graph_run_id": executed.run_id},
        )
        return updated

    def cancel(self, orchestration_id: str, reason: str) -> OrchestrationRecord:
        """Cancel a nonterminal controller and retain the decision record."""

        record = self.get(orchestration_id)
        if record.controller_id is not None:
            self._controller_runtime.cancel(record.controller_id, reason)
        updated = record.model_copy(update={"status": OrchestrationStatus.CANCELLED})
        self._store.save(updated)
        self._emit(
            updated,
            "orchestration.cancelled",
            updated.status.value,
            {"reason": reason, "controller_id": updated.controller_id},
        )
        return updated

    def get(self, orchestration_id: str) -> OrchestrationRecord:
        return self._store.load(orchestration_id)

    def controller_runtime(self) -> ControllerRuntime:
        """Expose the controller facade for explicit gate/repair/completion actions."""

        return self._controller_runtime

    def _validate_request_selection(self, request: OrchestrationRequest) -> None:
        available_skills = {skill.id for skill in self._policy.skills}
        unknown_skills = set(request.selected_skill_ids) - available_skills
        if unknown_skills:
            raise ValueError(f"Request selected unknown skills: {sorted(unknown_skills)}")
        PlanValidator().assert_valid(request.plan)

    def _validate_policy_tool_authority(self) -> None:
        """Require every declared worker tool to map to a granted registry capability."""

        for profile in self._policy.profiles:
            grant = set(profile.capability_grant.capabilities)
            for tool_name in profile.allowed_tool_names:
                registered = self._tool_registry.resolve(tool_name)
                if registered is None:
                    raise ValueError(
                        f'Profile "{profile.profile_id}" names unregistered tool "{tool_name}".'
                    )
                if registered.capability not in grant:
                    raise ValueError(
                        f'Profile "{profile.profile_id}" lacks capability '
                        f'"{registered.capability}" for tool "{tool_name}".'
                    )

    def _assign_workers(
        self,
        request: OrchestrationRequest,
        execution_plan: Plan,
    ) -> list[WorkerAssignment]:
        original_tasks = {task.task_id: task for task in request.plan.tasks}
        assignments: list[WorkerAssignment] = []
        counts: dict[str, int] = {}
        for task in execution_plan.tasks:
            source_task_ids = task.locked_interface.get("orchestrated_task_ids", [task.task_id])
            if not isinstance(source_task_ids, list) or not all(
                isinstance(value, str) for value in source_task_ids
            ):
                raise ValueError("Execution task lacks valid orchestrated task IDs.")
            profile = self._profile_for(task, source_task_ids, request)
            model = self._model_for(task.model_tier, profile)
            selected_skills = _profile_skills(profile, request.selected_skill_ids)
            identity = _worker_identity(task, source_task_ids, profile)
            counts[profile.profile_id] = counts.get(profile.profile_id, 0) + 1
            if counts[profile.profile_id] > profile.max_instances:
                raise ValueError(
                    f'Profile "{profile.profile_id}" exceeds its max_instances allocation.'
                )
            assignments.append(
                WorkerAssignment(
                    node_id=f"node:{task.task_id}",
                    task_id=task.task_id,
                    profile_id=profile.profile_id,
                    model_key=model.model_key,
                    skill_ids=selected_skills,
                    allowed_tool_names=sorted(profile.allowed_tool_names),
                    capability_ids=sorted(profile.capability_grant.capabilities),
                    agent_identity=identity,
                )
            )
            for source_task_id in source_task_ids:
                if source_task_id not in original_tasks:
                    raise ValueError(
                        "Execution task references a task absent from the original plan."
                    )
        return assignments

    def _profile_for(
        self,
        task: PlanTask,
        source_task_ids: list[str],
        request: OrchestrationRequest,
    ) -> AgentExecutionProfile:
        overrides = {request.profile_id_by_task_id.get(task_id) for task_id in source_task_ids}
        overrides.discard(None)
        if len(overrides) > 1:
            raise ValueError(
                "A collapsed single-agent task cannot combine different profile overrides."
            )
        if overrides:
            profile_id = next(iter(overrides))
            profile = next(
                (item for item in self._policy.profiles if item.profile_id == profile_id), None
            )
            if profile is None:
                raise ValueError(f'Unknown requested profile "{profile_id}".')
            if profile.stage != request.stage:
                raise ValueError("A requested profile does not match the orchestration stage.")
            if not set(profile.required_skill_ids).issubset(request.selected_skill_ids):
                raise ValueError("A requested profile requires skills not selected for this run.")
            return profile
        candidates = sorted(
            (item for item in self._policy.profiles if item.stage == request.stage),
            key=lambda item: item.profile_id,
        )
        if not candidates:
            raise ValueError(f'No user profile is available for stage "{request.stage}".')
        for profile in candidates:
            try:
                self._model_for(task.model_tier, profile)
            except ValueError:
                continue
            if set(profile.required_skill_ids).issubset(request.selected_skill_ids):
                return profile
        raise ValueError("No stage profile satisfies the selected skills and model tier.")

    def _model_for(
        self,
        tier: ModelTier,
        profile: AgentExecutionProfile,
    ) -> UserModelSelection:
        for model in sorted(self._policy.models, key=lambda item: item.model_key):
            if model.model_key in profile.allowed_model_keys and tier in model.allowed_tiers:
                return model
        raise ValueError(
            f'Profile "{profile.profile_id}" has no user-selected model for tier "{tier.value}".'
        )

    def _build_bindings(
        self,
        record: OrchestrationRecord,
        factory: GraphAgentBindingFactory,
    ) -> dict[str, GraphAgentBinding]:
        profiles = {profile.profile_id: profile for profile in self._policy.profiles}
        models = {model.model_key: model for model in self._policy.models}
        skills = {skill.id: skill for skill in self._policy.skills}
        tasks = {task.task_id: task for task in record.execution_plan.tasks}
        bindings: dict[str, GraphAgentBinding] = {}
        for assignment in record.assignments:
            profile = profiles[assignment.profile_id]
            model = models[assignment.model_key]
            task = tasks[assignment.task_id]
            selected_skills = tuple(skills[skill_id] for skill_id in assignment.skill_ids)
            context = GraphAgentBindingContext(
                assignment=assignment,
                execution_task=task,
                selected_skills=selected_skills,
                model=model,
                profile=profile,
                snapshot=record.request.snapshot,
            )
            binding = factory(context)
            self._validate_binding(binding, context)
            bindings[assignment.node_id] = _with_selected_skills(binding, selected_skills)
        return bindings

    @staticmethod
    def _validate_binding(binding: GraphAgentBinding, context: GraphAgentBindingContext) -> None:
        if binding.node_id != context.assignment.node_id:
            raise ValueError("A host binding must target the assignment's declared graph node.")
        definition: AgentDefinition = binding.definition
        if definition.identity != context.assignment.agent_identity:
            raise ValueError("Host binding identity does not equal the assigned worker identity.")
        if definition.model_binding != context.model.binding:
            raise ValueError("Host binding model does not equal the user-selected model binding.")
        undeclared = {tool.name for tool in definition.tools} - set(
            context.assignment.allowed_tool_names
        )
        if undeclared:
            raise ValueError(
                f"Host binding declares tools outside the user profile: {sorted(undeclared)}"
            )

    def _next_id(self) -> str:
        while self._store.exists(f"orchestration-{self._counter}"):
            self._counter += 1
        value = f"orchestration-{self._counter}"
        self._counter += 1
        return value

    def _emit(
        self,
        record: OrchestrationRecord,
        event_type: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        self._telemetry.emit(
            event_type,
            TelemetryContext(
                run_id=record.orchestration_id,
                controller_id=record.controller_id,
                stage=record.request.stage,
            ),
            actor=TelemetryActor(
                kind="system",
                identifier="agent-sdk-orchestrator",
                role="orchestrator",
            ),
            authority=TelemetryAuthority.DETERMINISTIC,
            status=status,
            payload=payload,
        )


def _profile_skills(profile: AgentExecutionProfile, selected_skill_ids: list[str]) -> list[str]:
    return sorted(set(profile.allowed_skill_ids) & set(selected_skill_ids))


def _worker_identity(
    task: PlanTask,
    source_task_ids: list[str],
    profile: AgentExecutionProfile,
) -> str:
    """Derive the binding identity without allowing a host to improvise authority."""

    if len(source_task_ids) == 1:
        return f"{profile.role}:{source_task_ids[0]}"
    return f"{profile.role}:orchestrated:{task.task_id}"


def _routing_state(request: OrchestrationRequest) -> dict[str, Any]:
    """Build bounded non-secret routing metadata for an optional Jev advisory."""

    return {
        "stage": request.stage,
        "gap_metadata": request.gap_metadata.model_dump(mode="json"),
        "plan": {
            "task_count": len(request.plan.tasks),
            "dependency_count": sum(len(task.dependencies) for task in request.plan.tasks),
            "model_tiers": sorted({task.model_tier.value for task in request.plan.tasks}),
            "elastic_limits": {
                "max_depth": request.plan.max_elastic_depth,
                "max_nodes": request.plan.max_elastic_nodes,
            },
        },
    }


def _execution_plan(plan: Plan, architecture: WorkflowArchitecture) -> Plan:
    if architecture is WorkflowArchitecture.MULTI_AGENT:
        return plan
    ordered = sorted(plan.tasks, key=lambda task: task.task_id)
    tiers = {task.model_tier for task in ordered}
    tier = (
        ModelTier.STRONG
        if ModelTier.STRONG in tiers
        else ModelTier.STANDARD
        if ModelTier.STANDARD in tiers
        else ModelTier.CHEAP
    )
    source_ids = [task.task_id for task in ordered]
    return Plan(
        plan_id=f"{plan.plan_id}:single-agent",
        tasks=[
            PlanTask(
                task_id=f"single-{plan.plan_id}",
                scope=f"orchestrated:{plan.plan_id}",
                locked_interface={
                    "orchestrated_task_ids": source_ids,
                    "original_plan_id": plan.plan_id,
                },
                instructions="\n\n".join(
                    f"[{task.task_id}] {task.instructions}" for task in ordered
                ),
                acceptance_criteria="\n".join(
                    f"[{task.task_id}] {task.acceptance_criteria}" for task in ordered
                ),
                model_tier=tier,
                authorized_artifact_ids=sorted(
                    {artifact for task in ordered for artifact in task.authorized_artifact_ids}
                ),
            )
        ],
        max_elastic_depth=plan.max_elastic_depth,
        max_elastic_nodes=plan.max_elastic_nodes,
    )


def _with_selected_skills(
    binding: GraphAgentBinding,
    selected_skills: tuple[SkillContext, ...],
) -> GraphAgentBinding:
    """Wrap a host task adapter so the approved skill selection is exact and visible."""

    adapter = binding.task_adapter

    def scoped_task(node, context):
        task = adapter(node, context)
        return task.model_copy(update={"skills": list(selected_skills)})

    return dataclass_replace(binding, task_adapter=scoped_task)
