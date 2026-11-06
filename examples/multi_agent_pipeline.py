# Copyright (c) 2026 David Michael Indraputra

"""A governed research team: two researchers work in parallel, a writer waits for both.

This is the core `Orchestrator`: a policy says what may run, a plan lists the tasks and what each
depends on, a person approves, and the graph runs the tasks in waves. Models are scripted so it
runs offline; replace `model_factory` with a real provider to use it for real.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nailong_agent_sdk import (
    AgentDefinition,
    AgentExecutionProfile,
    AgentRuntimeServices,
    CapabilityGrant,
    ComplexityRoutingRules,
    DependencyProof,
    GapMetadata,
    GraphAgentBinding,
    GraphAgentBindingContext,
    ModelTier,
    OrchestrationPolicy,
    OrchestrationRequest,
    Orchestrator,
    Plan,
    PlanTask,
    RunStateStore,
    ScopedAgentTask,
    ScriptedModel,
    SharedSubstrateSnapshot,
    TaskScope,
    TaskSignalUse,
    TerminationPolicy,
    UserModelSelection,
    VersionedInstructions,
)
from nailong_agent_sdk.state.planning import DependencyRule, SignalRole

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"status": {"const": "complete"}, "summary": {"type": "string"}},
    "required": ["status", "summary"],
    "additionalProperties": False,
}
SCRIPTED_ANSWERS = {
    "pricing": "Per-seat pricing with a free tier.",
    "security": "SOC 2 report available; data stays in the region.",
    "brief": "Brief: per-seat pricing, a free tier, SOC 2 and regional data residency.",
}


def build_policy() -> OrchestrationPolicy:
    def profile(role: str, instances: int) -> AgentExecutionProfile:
        return AgentExecutionProfile(
            profile_id=role,
            stage="evaluation",
            role=role,
            allowed_model_keys=["team-model"],
            allowed_tool_names=[],
            capability_grant=CapabilityGrant(role=role),
            max_instances=instances,
        )

    return OrchestrationPolicy(
        policy_id="research-team-policy-v1",
        routing_rules=ComplexityRoutingRules(
            multi_agent_min_categories=2, multi_agent_min_blast_radius=3
        ),
        models=[
            UserModelSelection(
                model_key="team-model",
                binding={"provider": "host", "model": "replace-with-selected-provider"},
                allowed_tiers=[ModelTier.STANDARD],
            )
        ],
        profiles=[profile("researcher", 2), profile("writer", 1)],
        max_total_agents=3,
        max_parallel_agents=2,
    )


def build_request() -> OrchestrationRequest:
    def task(
        task_id: str,
        scope: str,
        instructions: str,
        signal: str,
        role: SignalRole,
        *needs: str,
        extra_signals: tuple[str, ...] = (),
    ) -> PlanTask:
        signals = [signal, *extra_signals]
        return PlanTask(
            task_id=task_id,
            scope=scope,
            locked_interface={"signals": [{"id": name} for name in signals]},
            instructions=instructions,
            dependencies=list(needs),
            acceptance_criteria="Return a one-sentence summary.",
            model_tier=ModelTier.STANDARD,
            signal_uses=[
                TaskSignalUse(
                    task_id=task_id,
                    signal_id=signal,
                    role=role,
                    source_spans=[f"brief:{scope}"],
                    scope_pointer=scope,
                )
            ],
        )

    pricing = task(
        "research-pricing",
        "pricing",
        "Summarise the pricing.",
        "pricing_summary",
        SignalRole.PRODUCE,
    )
    security = task(
        "research-security",
        "security",
        "Summarise the security.",
        "security_summary",
        SignalRole.PRODUCE,
    )
    writer = task(
        "write-brief",
        "brief",
        "Combine the researchers' summaries into a short brief.",
        "pricing_summary",
        SignalRole.CONSUME,
        "research-pricing",
        "research-security",
        extra_signals=("security_summary",),
    )
    writer.signal_uses.append(
        TaskSignalUse(
            task_id="write-brief",
            signal_id="security_summary",
            role=SignalRole.CONSUME,
            source_spans=["brief:brief"],
            scope_pointer="brief",
        )
    )
    return OrchestrationRequest(
        request_id="research-team-request-v1",
        stage="evaluation",
        snapshot=SharedSubstrateSnapshot(
            snapshot_id="product-brief-v1", version="1.0.0", content_hash="brief-hash"
        ),
        gap_metadata=GapMetadata(categories_touched=["pricing", "security"], blast_radius=1),
        plan=Plan(
            plan_id="research-team-plan-v1",
            tasks=[pricing, security, writer],
            dependency_proofs=[
                DependencyProof(
                    parent_task_id=parent,
                    child_task_id="write-brief",
                    shared_signal_ids=[signal],
                    applied_rule=DependencyRule.PRODUCER_TO_CONSUMER,
                    source_spans=["brief:brief"],
                )
                for parent, signal in (
                    ("research-pricing", "pricing_summary"),
                    ("research-security", "security_summary"),
                )
            ],
        ),
        profile_id_by_task_id={
            "research-pricing": "researcher",
            "research-security": "researcher",
            "write-brief": "writer",
        },
    )


def make_binding_factory(seen_by_writer: dict[str, Any]):
    def binding_factory(context: GraphAgentBindingContext) -> GraphAgentBinding:
        scope = context.execution_task.scope
        definition = AgentDefinition(
            identity=context.assignment.agent_identity,
            instructions=VersionedInstructions(
                version="v1", text=context.execution_task.instructions
            ),
            input_schema={"type": "object"},
            model_binding=context.model.binding,
            output_schema=SUMMARY_SCHEMA,
            termination_policy=TerminationPolicy(max_iterations=1, status_field="status"),
        )

        def task_adapter(node, graph_context) -> ScopedAgentTask:
            upstream = {
                node_id: result.output for node_id, result in graph_context.dependencies.items()
            }
            if scope == "brief":
                seen_by_writer.update(upstream)
            return ScopedAgentTask(
                id=node.task_id or node.node_id,
                input={"upstream": upstream},
                scope=TaskScope(label=scope),
                locked_interface=context.execution_task.locked_interface,
                instructions=context.execution_task.instructions,
                acceptance_criteria=[context.execution_task.acceptance_criteria],
            )

        def model_factory(_node, _graph_context) -> ScriptedModel:
            output = {"status": "complete", "summary": SCRIPTED_ANSWERS[scope]}
            return ScriptedModel([{"type": "final", "output": output}])

        return GraphAgentBinding(
            node_id=context.assignment.node_id,
            definition=definition,
            task_adapter=task_adapter,
            model_factory=model_factory,
            idempotent=True,
        )

    return binding_factory


async def run(run_root: Path) -> dict[str, Any]:
    orchestrator = Orchestrator(run_root, build_policy())
    prepared = await orchestrator.prepare(build_request())
    presented = orchestrator.submit_for_approval(prepared.orchestration_id)
    approved = orchestrator.approve(presented.orchestration_id, True, "Approved by the owner.")
    seen_by_writer: dict[str, Any] = {}
    completed = await orchestrator.dispatch_and_execute(
        approved.orchestration_id,
        AgentRuntimeServices.open(run_root),
        make_binding_factory(seen_by_writer),
    )
    graph = RunStateStore(run_root).load(completed.graph_run_id).graph
    return {
        "orchestration_id": completed.orchestration_id,
        "architecture": completed.architecture.value,
        "status": completed.status.value,
        "graph_run_id": completed.graph_run_id,
        "node_statuses": dict(graph["statuses"]),
        "writer_received": seen_by_writer,
    }


async def main() -> None:
    print(await run(Path(".agent-runs/research-team")))


if __name__ == "__main__":
    asyncio.run(main())
