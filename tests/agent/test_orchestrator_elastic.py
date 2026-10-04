import pytest

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.orchestrator import OrchestrationStatus, Orchestrator
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.state.elastic import (
    ELASTIC_REQUEST_TOOL_NAME,
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
)
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from tests.support.agents import COMPLETE_SCHEMA, arun, final, tool_call
from tests.support.orchestration import close_all, make_policy, make_request, pipeline, plan_of

TOOLS = ("brief", ELASTIC_REQUEST_TOOL_NAME)
CAPABILITIES = ("utility.brief", "graph.elastic.request")
CORE = {item.name: item for item in core_tool_definitions()}
REQUEST = {
    "request_id": "probe",
    "scope": "clock tree",
    "instructions": "Trace the clock tree and list every divider.",
    "reason": "The section is ambiguous.",
}


def elastic_policy(**overrides):
    values = {"tools": TOOLS, "capabilities": CAPABILITIES}
    values.update(overrides)
    return make_policy(**values)


def elastic_factory(
    seen,
    prompts,
    *,
    root_turns,
    child_turns=None,
    join_turns=None,
    elastic_tools=("brief",),
    escalate=False,
):
    def factory(context):
        seen.append(context)
        role = "root" if context.elastic is None else context.elastic.role.value
        node_id = context.assignment.node_id
        if role == "root":
            turns = root_turns.get(node_id, [final()])
            names = TOOLS
        elif role == "child":
            turns = child_turns or [final({"status": "complete", "finding": "none"})]
            names = elastic_tools
        else:
            turns = join_turns or [final()]
            names = elastic_tools
        definition = AgentDefinition(
            identity=context.assignment.agent_identity,
            instructions=VersionedInstructions(version="v1", text="work"),
            input_schema={"type": "object"},
            tools=[CORE[name] for name in names],
            model_binding=context.model.binding,
            output_schema=COMPLETE_SCHEMA,
            memory_scope=MemoryScope.TASK_SCOPED,
            termination_policy=TerminationPolicy(
                max_iterations=4, status_field="status", escalation=EscalationTarget.NONE
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

        class Recording(ScriptedModel):
            async def next_turn(self, model_context):
                prompts.setdefault(node_id, []).append(model_context.prompt.model_dump_json())
                return await super().next_turn(model_context)

        return GraphAgentBinding(
            node_id=node_id,
            definition=definition,
            task_adapter=task_adapter,
            model_factory=lambda node, node_context: Recording(turns),
            escalate_elastic_overflow=escalate,
        )

    return factory


def graph_of(orchestrator, executed):
    return orchestrator.controller_runtime()._harness.get_run_state(executed.graph_run_id).graph


def test_the_policy_carries_elastic_ceilings_with_hard_limits():
    policy = make_policy()
    assert (policy.max_elastic_depth, policy.max_elastic_nodes) == (1, 2)
    with pytest.raises(ValueError, match="max_elastic_depth"):
        make_policy(max_elastic_depth=MAX_ELASTIC_DEPTH_LIMIT + 1)
    with pytest.raises(ValueError, match="max_elastic_nodes"):
        make_policy(max_elastic_nodes=MAX_ELASTIC_NODES_LIMIT + 1)


def test_the_plans_elastic_caps_may_not_exceed_the_policy_ceilings(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    try:
        too_many = make_request(plan=plan_of().model_copy(update={"max_elastic_nodes": 3}))
        with pytest.raises(
            ValueError,
            match='Plan "plan" declares max_elastic_nodes 3, above the policy ceiling '
            "max_elastic_nodes 2",
        ):
            arun(orchestrator.prepare(too_many))
        too_deep = make_request(plan=plan_of().model_copy(update={"max_elastic_depth": 2}))
        with pytest.raises(
            ValueError,
            match='Plan "plan" declares max_elastic_depth 2, above the policy ceiling '
            "max_elastic_depth 1",
        ):
            arun(orchestrator.prepare(too_deep))
    finally:
        orchestrator._telemetry.close()
    raised = Orchestrator(
        tmp_path / "raised", make_policy(max_elastic_depth=2, max_elastic_nodes=4)
    )
    try:
        plan = plan_of().model_copy(update={"max_elastic_depth": 2, "max_elastic_nodes": 4})
        record = arun(raised.prepare(make_request(plan=plan)))
        assert (
            record.execution_plan.max_elastic_depth,
            record.execution_plan.max_elastic_nodes,
        ) == (
            2,
            4,
        )
    finally:
        raised._telemetry.close()


def test_a_profile_cannot_list_the_request_tool_without_granting_its_capability(tmp_path):
    with pytest.raises(ValueError, match='lacks capability "graph.elastic.request"'):
        Orchestrator(tmp_path, make_policy(tools=TOOLS, capabilities=("utility.brief",)))


def test_an_orchestrated_agent_requests_exploration_and_resumes_with_the_findings(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        root_turns={
            "node:single-plan": [
                tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, REQUEST),
                final({"status": "complete", "summary": "queued a probe"}),
            ]
        },
        child_turns=[final({"status": "complete", "finding": "four dividers"})],
        join_turns=[final({"status": "complete", "answer": "merged"})],
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(blast=0), factory
    )
    try:
        assert executed.status is OrchestrationStatus.EXECUTED
        graph = graph_of(orchestrator, executed)
        assert set(graph["statuses"].values()) == {"completed"}
        assert sorted(graph["statuses"]) == [
            "elastic:node:single-plan:probe",
            "join:node:single-plan",
            "node:single-plan",
        ]
        child = next(c for c in seen if c.elastic and c.elastic.role.value == "child")
        join = next(c for c in seen if c.elastic and c.elastic.role.value == "join")
        root = record.assignments[0]
        root_task = record.execution_plan.tasks[0]
        assert child.assignment.node_id == "elastic:node:single-plan:probe"
        assert (child.assignment.profile_id, child.assignment.model_key) == ("p1", "m1")
        assert child.assignment.agent_identity == f"{root.agent_identity}:elastic:probe"
        assert child.assignment.allowed_tool_names == root.allowed_tool_names
        assert child.assignment.capability_ids == root.capability_ids
        assert join.assignment.agent_identity == root.agent_identity
        assert child.execution_task.task_id == child.assignment.node_id
        assert child.execution_task.instructions == REQUEST["instructions"]
        assert child.execution_task.scope == root_task.scope
        assert child.execution_task.locked_interface == root_task.locked_interface
        assert child.execution_task.model_tier == root_task.model_tier
        assert child.execution_task.authorized_artifact_ids == root_task.authorized_artifact_ids
        assert child.elastic.request.scope == "clock tree"
        assert join.execution_task.instructions == root_task.instructions
        assert "four dividers" in " ".join(prompts["join:node:single-plan"])
        assert "Declared scope: clock tree" in " ".join(prompts["elastic:node:single-plan:probe"])
        items = {
            w.work_item_id: w.status.value
            for w in orchestrator.controller_runtime()
            .project_state(executed.controller_id)
            .work_items
        }
        assert items["elastic:node:single-plan:probe"] == "completed"
        assert orchestrator.controller_runtime().complete(executed.controller_id).phase is (
            ControllerPhase.COMPLETED
        )
    finally:
        close_all(orchestrator, services)


def test_in_a_multi_agent_plan_dependents_of_the_requesting_task_wait_for_the_join(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        root_turns={
            "node:T1": [
                tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, REQUEST),
                final({"status": "complete", "summary": "queued"}),
            ]
        },
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(blast=5), factory
    )
    try:
        assert executed.status is OrchestrationStatus.EXECUTED
        graph = graph_of(orchestrator, executed)
        started = [e["node_id"] for e in graph["events"] if e["status"] == "running"]
        assert started.index("join:node:T1") < started.index("node:T2")
        assert started.index("elastic:node:T1:probe") < started.index("join:node:T1")
        join = next(c for c in seen if c.elastic and c.elastic.role.value == "join")
        assert join.execution_task.instructions == "do it"
        assert join.assignment.task_id == "join:node:T1"
        assert join.elastic.root_node_id == "node:T1"
    finally:
        close_all(orchestrator, services)


def test_an_elastic_binding_cannot_exceed_the_authority_of_its_root(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        root_turns={
            "node:single-plan": [tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, REQUEST), final()]
        },
        elastic_tools=("brief", "web_fetch"),
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(blast=0), factory
    )
    try:
        graph = graph_of(orchestrator, executed)
        child = graph["results"]["elastic:node:single-plan:probe"]
        assert child["status"] == "failed"
        assert (
            'Elastic binding factory raised ValueError for node "elastic:node:single-plan:probe"'
            in child["reason"]
        )
        assert "tools outside the user profile" in child["reason"]
        assert graph["results"]["join:node:single-plan"]["status"] == "failed"
        assert executed.status is OrchestrationStatus.FAILED
    finally:
        close_all(orchestrator, services)


def test_by_default_an_agent_without_elastic_room_is_refused_and_the_run_completes(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        root_turns={
            "node:single-plan": [
                tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, REQUEST),
                final({"status": "complete", "summary": "went on without it"}),
            ]
        },
    )
    plan = plan_of().model_copy(update={"max_elastic_depth": 0})
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(plan=plan, blast=0), factory
    )
    try:
        assert executed.status is OrchestrationStatus.EXECUTED
        graph = graph_of(orchestrator, executed)
        assert list(graph["statuses"]) == ["node:single-plan"] and graph["spawn_records"] == []
    finally:
        close_all(orchestrator, services)


def test_a_deferred_batch_blocks_the_orchestration_until_capacity_is_granted_and_resumed(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        escalate=True,
        root_turns={
            "node:single-plan": [
                tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, REQUEST),
                final({"status": "complete", "summary": "queued"}),
            ]
        },
    )
    plan = plan_of().model_copy(update={"max_elastic_depth": 0})
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(plan=plan, blast=0), factory
    )
    try:
        assert executed.status is OrchestrationStatus.BLOCKED
        graph = graph_of(orchestrator, executed)
        assert graph["statuses"]["join:node:single-plan"] == "blocked"
        assert [item["status"] for item in graph["spawn_records"]] == ["deferred"]
        orchestrator.controller_runtime().grant_elastic_capacity(
            executed.controller_id, max_elastic_depth=1, reason="designer approved"
        )
        resumed = arun(orchestrator.resume_execution(record.orchestration_id, services, factory))
        assert resumed.status is OrchestrationStatus.EXECUTED
        assert set(graph_of(orchestrator, resumed)["statuses"].values()) == {"completed"}
        with pytest.raises(ValueError, match=f'"{record.orchestration_id}" is executed'):
            arun(orchestrator.resume_execution(record.orchestration_id, services, factory))
    finally:
        close_all(orchestrator, services)


def test_declining_a_deferred_batch_lets_the_orchestration_finish_without_exploration(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        escalate=True,
        root_turns={
            "node:single-plan": [tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, REQUEST), final()]
        },
    )
    plan = plan_of().model_copy(update={"max_elastic_depth": 0})
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(plan=plan, blast=0), factory
    )
    try:
        assert executed.status is OrchestrationStatus.BLOCKED
        orchestrator.controller_runtime().decline_elastic_requests(
            executed.controller_id, "node:single-plan", "out of budget"
        )
        resumed = arun(orchestrator.resume_execution(record.orchestration_id, services, factory))
        assert resumed.status is OrchestrationStatus.EXECUTED
        assert not any(c.elastic and c.elastic.role.value == "child" for c in seen)
    finally:
        close_all(orchestrator, services)
