import asyncio

from nailong_agent_sdk.state.graph_models import (
    GraphNode,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
)
from nailong_agent_sdk.state.planning import (
    DependencyProof,
    DependencyRule,
    ModelTier,
    Plan,
    PlanTask,
    SignalRole,
    TaskSignalUse,
)

A, F, B, C = (
    GraphNodeKind.AGENT,
    GraphNodeStatus.FAILED,
    GraphNodeStatus.BLOCKED,
    GraphNodeStatus.COMPLETED,
)
AGENT = GraphNodeKind.AGENT

P, K, D, X = (
    SignalRole.PRODUCE,
    SignalRole.CONSUME,
    SignalRole.DEFINE,
    SignalRole.CONSTRAINT_REFERENCE,
)


def arun(coro, timeout=300):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())


def n(node_id, deps=(), kind=A, **extra):
    return GraphNode(node_id=node_id, kind=kind, dependencies=list(deps), **extra)


def ok(output=None):
    return GraphNodeResult(status=C, output=output)


async def run_ok(node, context):
    return ok({"node": node.node_id, "deps": sorted(context.dependencies)})


def task(task_id, signals=(), uses=(), deps=(), rank=0, authorized=()):
    return PlanTask(
        task_id=task_id,
        scope=f"scope-{task_id}",
        locked_interface={"signals": list(signals)},
        instructions="do it",
        dependencies=list(deps),
        acceptance_criteria="passes",
        model_tier=ModelTier.CHEAP,
        signal_uses=[
            TaskSignalUse(
                task_id=task_id, signal_id=s, role=r, source_spans=["span"], scope_pointer="p"
            )
            for s, r in uses
        ],
        generality_rank=rank,
    )


def proof(parent, child, signals, rule=DependencyRule.PRODUCER_TO_CONSUMER):
    return DependencyProof(
        parent_task_id=parent,
        child_task_id=child,
        shared_signal_ids=list(signals),
        applied_rule=rule,
        source_spans=["s"],
    )


def chain_plan(count, plan_id="plan"):
    tasks = [task("T0000", ["s0"], [("s0", P)])]
    proofs = []
    for i in range(1, count):
        tasks.append(
            task(
                f"T{i:04d}",
                [f"s{i - 1}", f"s{i}"],
                [(f"s{i - 1}", K), (f"s{i}", P)],
                deps=[f"T{i - 1:04d}"],
            )
        )
        proofs.append(proof(f"T{i - 1:04d}", f"T{i:04d}", [f"s{i - 1}"]))
    return Plan(plan_id=plan_id, tasks=tasks, dependency_proofs=proofs)


def simple_plan(plan_id="plan"):
    tasks = [
        task("T1", ["a"], [("a", P)]),
        task("T2", ["a"], [("a", K)], deps=["T1"]),
        task("T3", ["z"], [("z", P)]),
    ]
    return Plan(plan_id=plan_id, tasks=tasks, dependency_proofs=[proof("T1", "T2", ["a"])])
