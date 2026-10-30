# Copyright (c) 2026 David Michael Indraputra

"""The nailong-agent-sdk arm for multi-agent cases: one graph run, one agent run per node."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding, GraphAgentExecutor
from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import (
    DependencyProof,
    DependencyRule,
    ModelTier,
    Plan,
    PlanTask,
    SignalRole,
    TaskSignalUse,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from nailong_agent_sdk.tools.core import core_tool_definitions
from nailong_agent_sdk.tools.policy import CapabilityGrant, CapabilityPolicy
from nailong_agent_sdk.tools.registry import (
    HarnessExecutionContext,
    HarnessToolExecutor,
    HarnessToolRegistry,
)
from nailong_agent_sdk.tools.supervisor import ProcessSupervisor

from ..acceptance import Acceptance
from ..cases import Case, FaultSpec
from ..nodes import NodeSpec
from ..recorder import BoundaryRecorder, WorkspaceWatcher
from ..truth import build_truth
from ..wire import PolicyEndpoint, SdkTransport
from .nailong import COMPLETE_SCHEMA, ENDPOINT, GATE_ID, SdkRun, gate_registry, run_id_for

CAPABILITIES = ["filesystem.read", "draft.write"]
SOURCE = DecisionSource(authority=TelemetryAuthority.DETERMINISTIC, identifier="trust-bench")


def signal_of(label: str) -> str:
    return f"signal:{label}"


def plan_for(case: Case) -> Plan:
    nodes = case.nodes()
    tasks = []
    proofs = []
    for node in nodes:
        produced = signal_of(node.label)
        consumed = [signal_of(dependency) for dependency in node.dependencies]
        uses = [(produced, SignalRole.PRODUCE), *((s, SignalRole.CONSUME) for s in consumed)]
        tasks.append(
            PlanTask(
                task_id=node.label,
                scope=node.label,
                locked_interface={"signals": [produced, *consumed]},
                instructions=node.task_text,
                dependencies=list(node.dependencies),
                acceptance_criteria="every declared output is written",
                model_tier=ModelTier.CHEAP,
                signal_uses=[
                    TaskSignalUse(
                        task_id=node.label,
                        signal_id=signal,
                        role=role,
                        source_spans=["span"],
                        scope_pointer="p",
                    )
                    for signal, role in uses
                ],
            )
        )
        proofs.extend(
            DependencyProof(
                parent_task_id=dependency,
                child_task_id=node.label,
                shared_signal_ids=[signal_of(dependency)],
                applied_rule=DependencyRule.PRODUCER_TO_CONSUMER,
                source_spans=["span"],
            )
            for dependency in node.dependencies
        )
    return Plan(plan_id=case.case_id, tasks=tasks, dependency_proofs=proofs)


def definition_for(case: Case, node: NodeSpec, fault: FaultSpec | None) -> AgentDefinition:
    available = {tool.name: tool for tool in core_tool_definitions()}
    return AgentDefinition(
        identity=node.label,
        instructions=VersionedInstructions(version="v1", text=node.task_text),
        input_schema={"type": "object"},
        tools=[available[name] for name in node.tools],
        model_binding=ModelBinding(provider="bench", model="policy-1"),
        output_schema=COMPLETE_SCHEMA,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=case.node_max_iterations(node.label, fault),
            status_field="status",
            escalation=EscalationTarget.NONE,
        ),
    )


def agent_run_id(base: str, label: str) -> str:
    return f"{base}.{label}"


def run_sdk_graph(
    case: Case, fault: FaultSpec | None, run_dir: Path, *, acceptance: Acceptance | None = None
) -> SdkRun:
    base = run_id_for(case, fault)
    run_root = run_dir / "run"
    workspace = run_root / "workspaces" / file_safe_name(base)
    case.materialize(workspace, fault)
    recorder = BoundaryRecorder(run_dir / "truth.jsonl")
    nodes = {node.label: node for node in case.nodes()}
    endpoints = {
        label: PolicyEndpoint(
            policy=case.node_policy(label, fault),
            recorder=recorder,
            agent_id=label,
            watcher=WorkspaceWatcher(workspace, paths=frozenset(node.outputs)),
        )
        for label, node in nodes.items()
    }
    services = AgentRuntimeServices.open(run_root)
    coordinator = HarnessCoordinator(run_root)
    plan = plan_for(case)
    plan_tasks = {task.task_id: task for task in plan.tasks}
    final = case.nodes()[-1].label
    gates = None if acceptance is None else gate_registry(acceptance, workspace)
    started = coordinator.start_run(plan)
    graph_run_id = started.run_id
    approvals = coordinator.approvals(graph_run_id)
    endpoint = OpenAICompatibleEndpoint(
        base_url=str(ENDPOINT["base_url"]),
        api_key=str(ENDPOINT["api_key"]),
        timeout_seconds=120.0,
        allow_insecure_http=True,
    )

    def binding_for(label: str) -> GraphAgentBinding:
        node = nodes[label]
        node_id = f"node:{label}"

        def task_adapter(graph_node, context) -> ScopedAgentTask:
            return ScopedAgentTask(
                id=agent_run_id(base, label),
                input={},
                scope=TaskScope(label=label, boundaries={}),
                locked_interface={},
                instructions=node.task_text,
                acceptance_criteria=["every declared output is written"],
            )

        def model_factory(graph_node, context) -> OpenAICompatibleAgentModel:
            return OpenAICompatibleAgentModel(
                endpoint,
                provider="bench",
                model="policy-1",
                transport=SdkTransport(endpoints[label]),
            )

        def tool_executor_factory(graph_node, context) -> HarnessToolExecutor:
            approval_ids: dict[str, str] = {}
            for capability in CAPABILITIES:
                request = approvals.request(
                    graph_run_id, node_id, capability, "Approved by the benchmark harness."
                )
                approvals.submit(
                    request.approval_id, True, "Approved by the benchmark harness.", source=SOURCE
                )
                approval_ids[capability] = request.approval_id
            return HarnessToolExecutor(
                HarnessToolRegistry(),
                HarnessExecutionContext(
                    run_id=graph_run_id,
                    node_id=node_id,
                    role="agent",
                    plan_task=plan_tasks[label],
                    run_root=workspace,
                    artifacts=ArtifactStore(workspace),
                    policy=CapabilityPolicy(
                        [
                            CapabilityGrant(
                                role="agent", capabilities=list(CAPABILITIES), allowed_paths=["."]
                            )
                        ]
                    ),
                    approvals=approvals,
                    supervisor=ProcessSupervisor([]),
                    declared_output_paths=tuple(node.outputs),
                    approval_ids_by_capability=approval_ids,
                    result_journal=services.result_journal,
                ),
            )

        definition = definition_for(case, node, fault)
        gated = gates is not None and label == final
        if gated:
            definition = definition.model_copy(update={"verification_gate_id": GATE_ID})
        return GraphAgentBinding(
            node_id=node_id,
            definition=definition,
            task_adapter=task_adapter,
            model_factory=model_factory,
            tool_executor_factory=tool_executor_factory,
            verification_gates=gates if gated else None,
        )

    executor = GraphAgentExecutor(
        services, {f"node:{label}": binding_for(label) for label in nodes}
    )
    try:
        record = asyncio.run(coordinator.execute_run(graph_run_id, executor.executors()))
    finally:
        for item in endpoints.values():
            item.finish()
        recorder.close()
        services.audit_logs.close()
        services.telemetry.close()
    statuses = {str(node): str(state) for node, state in record.graph["statuses"].items()}
    failed = {node: state for node, state in statuses.items() if state != "completed"}
    reason = None
    if failed:
        reason = "; ".join(f"{node} {state}" for node, state in sorted(failed.items()))
    return SdkRun(
        case=case,
        fault=fault,
        run_id=graph_run_id,
        run_dir=run_dir,
        run_root=run_root,
        workspace=workspace,
        status="failed" if failed else "completed",
        reason=reason,
        truth=build_truth(case, recorder.events),
        anchor_dir=None,
        agent_run_ids=tuple(agent_run_id(base, label) for label in nodes),
        graph_run_id=graph_run_id,
    )
