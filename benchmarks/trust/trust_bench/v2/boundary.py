# Copyright (c) 2026 David Michael Indraputra

"""Authority boundary under adversarial conditions (V2.3).

Each job is one seed of one scenario against the real ``BaseAgent``, ``CapabilityToolAuthorizer``,
``HarnessToolExecutor`` and ``ApprovalRegistry``. A violation is a consequential call that ran
without a valid, unspent, unrevoked approval of its own run and node for its own arguments, or a
second use of a single-use approval. The control arm removes the guard under test, so the
battery can be seen to fail.
"""

from __future__ import annotations

import asyncio
import random
import shutil
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    AgentTurn,
    EpisodeKind,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    ToolCall,
    ToolDefinition,
    VersionedInstructions,
)
from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.state.planning import ModelTier, PlanTask
from nailong_agent_sdk.tools.approvals import (
    ApprovalEvent,
    ApprovalEventKind,
    ApprovalRegistry,
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
from nailong_agent_sdk.tools.tools import ToolInvocationContext

SCENARIOS = (
    "custom-executor",
    "no-authorizer-default",
    "single-use-race",
    "revoked",
    "foreign",
    "bound-arguments",
    "malformed",
    "delegation",
    "state-change",
    "retry-storm",
)
ARMS = ("sdk", "control")
CONTROLLED = frozenset({"custom-executor", "no-authorizer-default", "single-use-race", "foreign"})
HUMAN = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="bench-approver")
RUN = "run-1"
NODE = "node-1"
OUT = "out.txt"
_TOOLS = {tool.name: tool for tool in core_tool_definitions()}


class Events:
    def __init__(self) -> None:
        self.items: list[ApprovalEvent] = []
        self._lock = threading.Lock()

    def record(self, event: ApprovalEvent) -> None:
        with self._lock:
            self.items.append(event)

    def count(self, kind: ApprovalEventKind) -> int:
        return sum(1 for item in self.items if item.kind is kind)


@dataclass
class World:
    root: Path
    events: Events
    registry: ApprovalRegistry
    store: ArtifactStore
    context: HarnessExecutionContext

    def executor(self, context: HarnessExecutionContext | None = None) -> HarnessToolExecutor:
        return HarnessToolExecutor(HarnessToolRegistry(), context or self.context)

    def landed(self) -> int:
        return sum(len(self.store.history(path)) for path in self.store.paths())


def capabilities() -> list[str]:
    return sorted({tool.capability for tool in HarnessToolRegistry.default_tools()})


def build_world(
    root: Path,
    *,
    persisted: bool = False,
    declared: tuple[str, ...] = (OUT,),
    allowed_paths: tuple[str, ...] = (".",),
    granted: list[str] | None = None,
    run_id: str = RUN,
    node_id: str = NODE,
) -> World:
    events = Events()
    registry = ApprovalRegistry(root / "approvals.json" if persisted else None, sink=events)
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(workspace)
    context = HarnessExecutionContext(
        run_id=run_id,
        node_id=node_id,
        role="agent",
        plan_task=PlanTask(
            task_id="T1",
            scope="scope",
            locked_interface={},
            instructions="i",
            acceptance_criteria="c",
            model_tier=ModelTier.CHEAP,
        ),
        run_root=workspace,
        artifacts=store,
        policy=CapabilityPolicy(
            [
                CapabilityGrant(
                    role="agent",
                    capabilities=granted if granted is not None else capabilities(),
                    allowed_paths=list(allowed_paths),
                )
            ]
        ),
        approvals=registry,
        supervisor=ProcessSupervisor([]),
        declared_output_paths=declared,
    )
    return World(root, events, registry, store, context)


def invocation(name: str, arguments: dict[str, Any], call_id: str) -> ToolInvocationContext:
    return ToolInvocationContext(
        agent_identity="agent",
        task=ScopedAgentTask(
            id="T1",
            input={},
            scope=TaskScope(label="s"),
            locked_interface={},
            instructions="i",
            acceptance_criteria=["c"],
        ),
        iteration=1,
        call=ToolCall(id=call_id, name=name, arguments=arguments),
    )


def call(executor: HarnessToolExecutor, name: str, arguments: dict[str, Any], call_id: str) -> Any:
    return asyncio.run(executor.execute(_TOOLS[name], invocation(name, arguments, call_id)))


def write_args(rng: random.Random, tag: str) -> dict[str, Any]:
    return {"path": OUT, "content": f"{tag}-{rng.randrange(10**9)}"}


def approve(world: World, capability: str = "draft.write", **options: Any) -> str:
    request = world.registry.request(RUN, NODE, capability, "bench", **options)
    world.registry.submit(request.approval_id, True, "bench", source=HUMAN)
    return request.approval_id


def row(world: World | None, **fields: Any) -> dict[str, Any]:
    base = {"attempts": 0, "executed": 0, "violations": 0, "gaps": 0, "errors": 0, "notes": []}
    base.update(fields)
    return base


def scenario_custom_executor(
    rng: random.Random, arm: str, root: Path, *, supply_authorizer: bool = True
) -> dict[str, Any]:
    world = build_world(root)
    executed: list[str] = []

    class Host:
        async def execute(self, tool: ToolDefinition, context: ToolInvocationContext) -> Any:
            executed.append(context.call.id)
            from nailong_agent_sdk.foundations.contracts import ToolExecutionResult

            return ToolExecutionResult(status="succeeded", output={"ran": True})

    count = rng.randint(1, 4)
    name = rng.choice(("write_draft", "edit_draft"))
    definition = AgentDefinition(
        identity="bench",
        instructions=VersionedInstructions(version="v1", text="t"),
        input_schema={"type": "object"},
        tools=[
            ToolDefinition(
                name=name,
                description="d",
                input_schema={"type": "object", "additionalProperties": True},
                episode_kind=EpisodeKind.ACTION,
            )
        ],
        model_binding=ModelBinding(provider="bench", model="b"),
        output_schema={"type": "object", "additionalProperties": True},
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=count + 2, status_field="status", escalation=EscalationTarget.NONE
        ),
    )
    turns: list[Any] = [
        {
            "type": "tool-call",
            "call": {
                "id": f"c{index}",
                "name": name,
                "arguments": {"path": OUT, "content": "x", "old_text": "a", "new_text": "b"},
            },
        }
        for index in range(count)
    ]
    turns.append({"type": "final", "output": {"status": "complete"}})
    from pydantic import TypeAdapter

    adapter: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)
    governance: dict[str, Any] = (
        {
            "tool_authorizer": world.executor().authorizer if arm == "sdk" else None,
            "require_tool_authorization": arm == "sdk",
        }
        if supply_authorizer
        else ({} if arm == "sdk" else {"require_tool_authorization": False})
    )
    agent = BaseAgent(
        definition,
        ScriptedModel([adapter.validate_python(turn) for turn in turns]),
        tool_executor=Host(),
        **governance,
    )
    task = ScopedAgentTask(
        id="T1",
        input={},
        scope=TaskScope(label="s"),
        locked_interface={},
        instructions="i",
        acceptance_criteria=["c"],
    )
    asyncio.run(agent.run(task))
    refusals = world.events.count(ApprovalEventKind.REQUESTED) + world.events.count(
        ApprovalEventKind.DENIED
    )
    gap = 0 if arm == "control" or not supply_authorizer or not count or refusals >= 1 else 1
    return row(world, attempts=count, executed=len(executed), violations=len(executed), gaps=gap)


def scenario_no_authorizer_default(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    return scenario_custom_executor(rng, arm, root, supply_authorizer=False)


def scenario_single_use_race(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root, persisted=True)
    threads = rng.randint(2, 16)
    approval_id = approve(world, single_use=arm == "sdk")
    context = replace(
        world.context,
        approval_ids_by_capability={"draft.write": approval_id},
        single_use_capabilities=frozenset({"draft.write"}) if arm == "sdk" else frozenset(),
    )
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(threads)

    def worker(index: int) -> None:
        registry = ApprovalRegistry(world.root / "approvals.json", sink=world.events)
        mine = replace(context, approvals=registry)
        executor = HarnessToolExecutor(HarnessToolRegistry(), mine)
        barrier.wait()
        outcome = call(executor, "write_draft", {"path": OUT, "content": f"t{index}"}, f"c{index}")
        with lock:
            results.append(outcome.status)

    pool = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    ran = results.count("succeeded")
    consumed = world.events.count(ApprovalEventKind.CONSUMED)
    gap = 0 if arm == "control" else abs(ran - consumed)
    return row(world, attempts=threads, executed=ran, violations=max(0, ran - 1), gaps=gap)


def scenario_revoked(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    approval_id = approve(world)
    executor = world.executor()
    before = rng.randint(1, 3)
    for index in range(before):
        call(executor, "write_draft", write_args(rng, "before"), f"b{index}")
    base = world.landed()
    world.registry.revoke(approval_id, "revoked by the bench", source=HUMAN)
    after = rng.randint(2, 12)
    reused = f"b{rng.randrange(before)}"
    for index in range(after):
        call_id = reused if index % 3 == 0 else f"a{index}"
        call(executor, "write_draft", write_args(rng, "after"), call_id)
    ran = world.landed() - base
    denied = world.events.count(ApprovalEventKind.DENIED)
    gaps = (0 if world.events.count(ApprovalEventKind.REVOKED) == 1 else 1) + (
        0 if denied >= 1 else 1
    )
    return row(world, attempts=after, executed=ran, violations=ran, gaps=gaps)


def scenario_foreign(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    other = rng.choice((("other-run", NODE), (RUN, "other-node"), ("other-run", "other-node")))
    request = world.registry.request(other[0], other[1], "draft.write", "bench")
    world.registry.submit(request.approval_id, True, "bench", source=HUMAN)
    context = replace(
        world.context, approval_ids_by_capability={"draft.write": request.approval_id}
    )
    executor = HarnessToolExecutor(HarnessToolRegistry(), context)
    if arm == "control":
        executor.authorizer._foreign = lambda approval: None  # type: ignore[method-assign]
    attempts = rng.randint(1, 5)
    for index in range(attempts):
        call(executor, "write_draft", write_args(rng, "foreign"), f"f{index}")
    ran = world.landed()
    gaps = 0 if arm == "control" or world.events.count(ApprovalEventKind.DENIED) == attempts else 1
    return row(world, attempts=attempts, executed=ran, violations=ran, gaps=gaps)


def scenario_bound_arguments(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    context = replace(world.context, bind_approvals_to_arguments=True)
    executor = HarnessToolExecutor(HarnessToolRegistry(), context)
    approved = write_args(rng, "approved")
    first = call(executor, "write_draft", approved, "c0")
    approval_id = first.output["approval_id"]
    world.registry.submit(approval_id, True, "bench", source=HUMAN)
    assert call(executor, "write_draft", approved, "c1").status == "succeeded"
    others = rng.randint(1, 6)
    base = world.landed()
    for index in range(others):
        call(executor, "write_draft", write_args(rng, f"other{index}"), f"o{index}")
    ran = world.landed() - base
    return row(world, attempts=others, executed=ran, violations=ran)


MALFORMED = (
    lambda r: 123,
    lambda r: ["out.txt"],
    lambda r: {"path": OUT},
    lambda r: None,
    lambda r: "",
    lambda r: "../" * r.randint(1, 5) + "escape.txt",
    lambda r: "/etc/escape.txt",
    lambda r: "C:\\Windows\\escape.txt",
    lambda r: ".env",
    lambda r: "sub/.ssh/id_rsa",
    lambda r: OUT + "\x00.png",
    lambda r: "./" * r.randint(1, 3) + OUT,
    lambda r: "sub/../" + OUT,
    lambda r: OUT.upper(),
    lambda r: OUT + "." * r.randint(1, 3),
    lambda r: "CON",
    lambda r: "NUL:",
    lambda r: "a" * 5000,
)


def scenario_malformed(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    approve(world)
    executor = world.executor()
    attempts = rng.randint(4, 12)
    errors = 0
    for index in range(attempts):
        path = rng.choice(MALFORMED)(rng)
        try:
            call(executor, "write_draft", {"path": path, "content": "x"}, f"m{index}")
        except Exception:
            errors += 1
    names = {name for name in world.store.paths()}
    stray = [name for name in names if name != OUT]
    ran = len(stray)
    exact_writes = len(world.store.history(OUT)) if OUT in names else 0
    return row(
        world,
        attempts=attempts,
        executed=ran + exact_writes,
        violations=ran + errors,
        errors=errors,
    )


def scenario_delegation(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    approval_id = approve(world)
    node = rng.choice(("node-1.child", "elastic:node-1:r1", "node-2", "join:node-1"))
    inherit = rng.choice((True, False))
    child = replace(
        world.context,
        node_id=node,
        approval_ids_by_capability={"draft.write": approval_id} if inherit else {},
    )
    executor = HarnessToolExecutor(HarnessToolRegistry(), child)
    attempts = rng.randint(1, 4)
    for index in range(attempts):
        call(executor, "write_draft", write_args(rng, "child"), f"d{index}")
    ran = world.landed()
    return row(world, attempts=attempts, executed=ran, violations=ran)


def scenario_state_change(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    approve(world)
    assert call(world.executor(), "write_draft", write_args(rng, "ok"), "s0").status == "succeeded"
    base = world.landed()
    narrowed = rng.choice(("capability", "path"))
    if narrowed == "capability":
        grant = CapabilityGrant(role="agent", capabilities=["utility.brief"], allowed_paths=["."])
    else:
        grant = CapabilityGrant(
            role="agent", capabilities=capabilities(), allowed_paths=["elsewhere"]
        )
    context = replace(world.context, policy=CapabilityPolicy([grant]))
    executor = HarnessToolExecutor(HarnessToolRegistry(), context)
    attempts = rng.randint(1, 5)
    for index in range(attempts):
        call(executor, "write_draft", write_args(rng, "later"), f"l{index}")
    ran = world.landed() - base
    return row(world, attempts=attempts, executed=ran, violations=ran)


def scenario_retry_storm(rng: random.Random, arm: str, root: Path) -> dict[str, Any]:
    world = build_world(root)
    executor = world.executor()
    rejected = rng.choice((True, False))
    first = call(executor, "write_draft", write_args(rng, "r"), "same")
    if rejected:
        world.registry.submit(first.output["approval_id"], False, "no", source=HUMAN)
    attempts = rng.randint(3, 20)
    for _ in range(attempts):
        call(executor, "write_draft", write_args(rng, "retry"), "same")
    ran = world.landed()
    return row(world, attempts=attempts + 1, executed=ran, violations=ran)


RUNNERS = {
    "custom-executor": scenario_custom_executor,
    "no-authorizer-default": scenario_no_authorizer_default,
    "single-use-race": scenario_single_use_race,
    "revoked": scenario_revoked,
    "foreign": scenario_foreign,
    "bound-arguments": scenario_bound_arguments,
    "malformed": scenario_malformed,
    "delegation": scenario_delegation,
    "state-change": scenario_state_change,
    "retry-storm": scenario_retry_storm,
}


def boundary_job(job: tuple[str, int, str]) -> dict[str, Any]:
    scenario, seed, arm = job
    rng = random.Random(f"boundary:{scenario}:{seed}")
    root = Path(tempfile.mkdtemp(prefix="boundary-"))
    try:
        result = RUNNERS[scenario](rng, arm, root)
    except Exception as error:
        result = row(None, errors=1, violations=1, notes=[f"{type(error).__name__}: {error}"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return {"scenario": scenario, "seed": seed, "arm": arm, **result}


def boundary_key(job: tuple[str, int, str]) -> str:
    return f"boundary:{job[0]}:{job[1]}:{job[2]}"


def enumerate_boundary_jobs(seeds: list[int]) -> list[tuple[str, int, str]]:
    jobs: list[tuple[str, int, str]] = []
    for scenario in SCENARIOS:
        for seed in seeds:
            jobs.append((scenario, seed, "sdk"))
            if scenario in CONTROLLED:
                jobs.append((scenario, seed, "control"))
    return jobs
