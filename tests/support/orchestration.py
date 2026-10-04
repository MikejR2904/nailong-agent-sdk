import asyncio

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.orchestrator import (
    AgentExecutionProfile,
    OrchestrationPolicy,
    OrchestrationRequest,
    Orchestrator,
    UserModelSelection,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    SkillContext,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.state.orchestration_models import ComplexityRoutingRules, GapMetadata
from nailong_agent_sdk.state.planning import ModelTier, Plan
from nailong_agent_sdk.state.shared_state import SharedSubstrateSnapshot
from nailong_agent_sdk.tools.policy import CapabilityGrant
from tests.support.agents import COMPLETE_SCHEMA, arun, tool
from tests.support.plans import K, P, proof
from tests.support.plans import task as plan_task

BINDING = ModelBinding(provider="fake", model="fake-1")
TIERS = [ModelTier.CHEAP, ModelTier.STANDARD, ModelTier.STRONG]


def make_policy(
    *,
    max_total=8,
    max_parallel=4,
    max_repair=1,
    multi=True,
    max_instances=8,
    tools=("brief",),
    capabilities=("utility.brief",),
    policy_id="pol",
    max_elastic_depth=1,
    max_elastic_nodes=2,
):
    return OrchestrationPolicy(
        policy_id=policy_id,
        routing_rules=ComplexityRoutingRules(
            multi_agent_min_categories=3,
            multi_agent_min_blast_radius=3,
            multi_agent_gap_types=["inconsistency"],
        ),
        skills=[
            SkillContext(id="s1", version="1", content="skill one"),
            SkillContext(id="s2", version="1", content="skill two"),
        ],
        models=[UserModelSelection(model_key="m1", binding=BINDING, allowed_tiers=TIERS)],
        profiles=[
            AgentExecutionProfile(
                profile_id="p1",
                stage="design",
                role="worker",
                allowed_skill_ids=["s1"],
                allowed_model_keys=["m1"],
                allowed_tool_names=list(tools),
                capability_grant=CapabilityGrant(
                    role="worker", capabilities=list(capabilities), allowed_paths=[]
                ),
                max_instances=max_instances,
            )
        ],
        max_total_agents=max_total,
        max_parallel_agents=max_parallel,
        max_repair_attempts=max_repair,
        multi_agent_enabled=multi,
        max_elastic_depth=max_elastic_depth,
        max_elastic_nodes=max_elastic_nodes,
    )


def plan_of(count=3):
    tasks = [plan_task("T1", ["a"], [("a", P)])]
    proofs = []
    for i in range(2, count + 1):
        tasks.append(
            plan_task(
                f"T{i}",
                ["a"] if i == 2 else [f"z{i}"],
                [("a", K)] if i == 2 else [(f"z{i}", P)],
                deps=["T1"] if i == 2 else [],
            )
        )
    if count >= 2:
        proofs.append(proof("T1", "T2", ["a"]))
    return Plan(plan_id="plan", tasks=tasks, dependency_proofs=proofs)


def independent_plan(count):
    return Plan(
        plan_id="wide",
        tasks=[plan_task(f"T{i:02d}", [f"z{i}"], [(f"z{i}", P)]) for i in range(count)],
    )


def make_request(plan=None, blast=0, skills=("s1",)):
    return OrchestrationRequest(
        request_id="req-1",
        stage="design",
        snapshot=SharedSubstrateSnapshot(snapshot_id="snap", version="1", content_hash="h"),
        gap_metadata=GapMetadata(blast_radius=blast),
        plan=plan or plan_of(),
        selected_skill_ids=list(skills),
    )


def make_factory(
    model_turns=None,
    concurrency=None,
    identity_override=None,
    tools=("brief",),
    node_override=None,
    binding_override=None,
):
    def factory(context):
        definition = AgentDefinition(
            identity=identity_override or context.assignment.agent_identity,
            instructions=VersionedInstructions(version="v1", text="work"),
            input_schema={"type": "object"},
            tools=[tool(name) for name in tools],
            model_binding=binding_override or context.model.binding,
            output_schema=COMPLETE_SCHEMA,
            memory_scope=MemoryScope.TASK_SCOPED,
            termination_policy=TerminationPolicy(
                max_iterations=3, status_field="status", escalation=EscalationTarget.NONE
            ),
        )

        def task_adapter(node, node_context):
            return ScopedAgentTask(
                id=f"task-{node.node_id}",
                input={},
                scope=TaskScope(label=node.node_id),
                locked_interface={},
                instructions=context.execution_task.instructions,
                acceptance_criteria=["done"],
            )

        class Model(ScriptedModel):
            async def next_turn(self, model_context):
                if concurrency is not None:
                    concurrency["now"] += 1
                    concurrency["max"] = max(concurrency["max"], concurrency["now"])
                    await asyncio.sleep(0.15)
                    concurrency["now"] -= 1
                return await super().next_turn(model_context)

        turns = model_turns or [{"type": "final", "output": {"status": "complete"}}]
        return GraphAgentBinding(
            node_id=node_override or context.assignment.node_id,
            definition=definition,
            task_adapter=task_adapter,
            model_factory=lambda node, node_context: Model(turns),
        )

    return factory


def pipeline(tmp_path, policy, request, factory, *, services=None):
    orchestrator = Orchestrator(tmp_path, policy)
    record = arun(orchestrator.prepare(request))
    orchestrator.submit_for_approval(record.orchestration_id)
    orchestrator.approve(record.orchestration_id, True, "ok")
    services = services or AgentRuntimeServices.open(tmp_path)
    executed = arun(
        orchestrator.dispatch_and_execute(record.orchestration_id, services, factory), timeout=300
    )
    return orchestrator, record, executed, services


def close_all(orchestrator, services):
    orchestrator._telemetry.close()
    services.telemetry.close()
