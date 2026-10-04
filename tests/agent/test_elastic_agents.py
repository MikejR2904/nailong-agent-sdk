import json

import pytest

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding, GraphAgentExecutor
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import ScopedAgentTask, TaskScope
from nailong_agent_sdk.state.elastic import (
    ELASTIC_REQUEST_TOOL_NAME,
    ElasticSpawnRequest,
    GraphSpawnStatus,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeStatus
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from tests.support.agents import DynamicModel, arun, definition, final, tool_call
from tests.support.elastic import done_with, req, statuses
from tests.support.plans import AGENT, n

ELASTIC = GraphNodeKind.ELASTIC
COMPLETED = GraphNodeStatus.COMPLETED
FAILED = GraphNodeStatus.FAILED


def request_arguments(request_id="probe", **overrides):
    values = {
        "request_id": request_id,
        "scope": "clock tree",
        "instructions": "Trace the clock tree and list every divider.",
        "reason": "The section is ambiguous.",
    }
    values.update(overrides)
    return values


def elastic_tool():
    return next(item for item in core_tool_definitions() if item.name == ELASTIC_REQUEST_TOOL_NAME)


def binding(
    node_id, turns=None, *, tool=True, prompts=None, model=None, idempotent=False, escalate=False
):
    class Recording(ScriptedModel):
        async def next_turn(self, model_context):
            if prompts is not None:
                prompts.append(model_context.prompt.model_dump_json())
            return await super().next_turn(model_context)

    def task_adapter(node, context):
        return ScopedAgentTask(
            id=f"task-{node.node_id}",
            input={},
            scope=TaskScope(label=node.node_id),
            locked_interface={},
            instructions="original instructions",
            acceptance_criteria=["done"],
        )

    return GraphAgentBinding(
        node_id=node_id,
        definition=definition(tools=[elastic_tool()] if tool else []),
        task_adapter=task_adapter,
        model_factory=lambda node, context: model or Recording(turns or [final()]),
        idempotent=idempotent,
        escalate_elastic_overflow=escalate,
    )


def observed(context):
    parts = []
    for observation in context.observations:
        if observation.kind != "tool-result":
            continue
        parts.append(observation.message)
        if observation.result is not None:
            parts.append(json.dumps(observation.result.preview, sort_keys=True, default=str))
            parts.append(observation.result.error or "")
    return " ".join(parts)


def services_for(tmp_path):
    return AgentRuntimeServices.open(tmp_path)


def close(services):
    services.telemetry.close()


def test_an_agent_requests_exploration_and_resumes_with_the_findings(tmp_path):
    services = services_for(tmp_path)
    prompts = {}
    try:

        def factory(node, context):
            role = node.elastic.role.value
            turns = [
                final({"status": "complete", "finding": "four dividers"})
                if role == "child"
                else final({"status": "complete", "answer": "merged"})
            ]
            return binding(
                node.node_id, turns, tool=False, prompts=prompts.setdefault(node.node_id, [])
            )

        executor = GraphAgentExecutor(
            services,
            {
                "root": binding(
                    "root",
                    [
                        tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments()),
                        final({"status": "complete", "summary": "queued a probe"}),
                    ],
                ),
                "after": binding("after", tool=False),
            },
            elastic_binding_factory=factory,
        )
        graph = StateGraph(
            [n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=2
        )
        results = arun(graph.execute(executor.executors()))
        assert statuses(graph) == {node_id: "completed" for node_id in graph.nodes}
        assert sorted(graph.nodes) == ["after", "elastic:root:probe", "join:root", "root"]
        assert results["root"].spawn_requests == [ElasticSpawnRequest(**request_arguments())]
        assert "elastic-requests:1" in results["root"].diagnostics
        assert results["elastic:root:probe"].output == {
            "status": "complete",
            "finding": "four dividers",
        }
        assert results["join:root"].output == {"status": "complete", "answer": "merged"}
        child_prompt = " ".join(prompts["elastic:root:probe"])
        assert "Declared scope: clock tree" in child_prompt
        assert "queued a probe" in child_prompt
        join_prompt = " ".join(prompts["join:root"])
        assert "Elastic continuation of node" in join_prompt and "four dividers" in join_prompt
        assert "original instructions" in join_prompt and "queued a probe" in join_prompt
        order = [e.node_id for e in graph.events if e.status is GraphNodeStatus.RUNNING]
        assert order == ["root", "elastic:root:probe", "join:root", "after"]
        assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED]
    finally:
        close(services)


def test_a_refused_request_comes_back_to_the_agent_and_nothing_is_spawned(tmp_path):
    services = services_for(tmp_path)
    try:
        seen = []

        def script(context):
            seen.append(observed(context))
            if len(seen) == 1:
                return tool_call(
                    "r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments(dependencies=["secret"])
                )
            return final()

        executor = GraphAgentExecutor(
            services, {"root": binding("root", model=DynamicModel(script))}
        )
        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
        results = arun(graph.execute(executor.executors()))
        assert "DEPENDENCY_NOT_VISIBLE" in seen[1] or '"secret"' in seen[1]
        assert sorted(graph.nodes) == ["root"]
        assert results["root"].spawn_requests == []
        assert not any(item.startswith("elastic-") for item in results["root"].diagnostics)
    finally:
        close(services)


def test_the_agent_is_told_at_once_when_the_plan_has_no_elastic_room(tmp_path):
    services = services_for(tmp_path)
    try:
        seen = []

        def script(context):
            seen.append(observed(context))
            if len(seen) == 1:
                return tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments())
            return final()

        executor = GraphAgentExecutor(
            services, {"root": binding("root", model=DynamicModel(script))}
        )
        graph = StateGraph([n("root")], max_elastic_depth=0, max_elastic_nodes=2)
        arun(graph.execute(executor.executors()))
        assert "max_elastic_depth 0" in seen[1] or "ELASTIC_DEPTH_CAP_REACHED" in seen[1]
        assert graph.spawn_records == ()
        assert statuses(graph) == {"root": "completed"}
    finally:
        close(services)


def test_an_escalating_binding_queues_overflow_and_the_graph_holds_it_for_a_decision(tmp_path):
    services = services_for(tmp_path)
    try:
        receipts = []

        def script(context):
            receipts.append(observed(context))
            if len(receipts) == 1:
                return tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments())
            return final({"status": "complete", "summary": "queued"})

        executor = GraphAgentExecutor(
            services,
            {
                "root": binding("root", model=DynamicModel(script), escalate=True),
                "after": binding("after", tool=False),
            },
        )
        graph = StateGraph(
            [n("root"), n("after", ["root"])], max_elastic_depth=0, max_elastic_nodes=2
        )
        arun(graph.execute(executor.executors()))
        assert "capacity_decision_required" in receipts[1]
        assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.DEFERRED]
        assert statuses(graph) == {"after": "blocked", "join:root": "blocked", "root": "completed"}
        graph.grant_elastic_capacity(max_elastic_depth=1, reason="designer approved")
        assert graph.status("elastic:root:probe") is GraphNodeStatus.RUNNABLE
    finally:
        close(services)


def test_requests_are_dropped_with_a_diagnostic_when_the_agent_does_not_complete(tmp_path):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(
            services,
            {
                "root": binding(
                    "root",
                    [
                        tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments()),
                        {"type": "blocked", "reason": "waiting for the designer"},
                    ],
                )
            },
        )
        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
        results = arun(graph.execute(executor.executors()))
        assert results["root"].status is GraphNodeStatus.BLOCKED
        assert "elastic-requests-dropped:1" in results["root"].diagnostics
        assert results["root"].spawn_requests == []
        assert sorted(graph.nodes) == ["root"]
    finally:
        close(services)


def test_an_elastic_node_without_a_binding_or_factory_fails_with_the_fix(tmp_path):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(services, {})

        async def root(node, context):
            return done_with(req("a"))

        graph = StateGraph(
            [n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=2
        )
        results = arun(graph.execute({AGENT: root, ELASTIC: executor.execute}))
        reason = results["elastic:root:a"].reason
        assert results["elastic:root:a"].status is FAILED
        assert 'No GraphAgentBinding is registered for node "elastic:root:a"' in reason
        assert "elastic_binding_factory" in reason
        assert results["join:root"].status is FAILED
    finally:
        close(services)


def test_a_planned_node_without_a_binding_keeps_the_original_message(tmp_path):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(services, {})
        graph = StateGraph([n("root")])
        results = arun(graph.execute(executor.executors()))
        assert results["root"].reason == 'No GraphAgentBinding is registered for node "root".'
    finally:
        close(services)


def test_a_failing_or_mismatched_factory_fails_the_node_naming_the_cause(tmp_path):
    services = services_for(tmp_path)
    try:

        async def root(node, context):
            return done_with(req("a"))

        def raising(node, context):
            raise RuntimeError("policy store unreachable")

        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
        executor = GraphAgentExecutor(services, {}, elastic_binding_factory=raising)
        results = arun(graph.execute({AGENT: root, ELASTIC: executor.execute}))
        assert (
            'Elastic binding factory raised RuntimeError for node "elastic:root:a"'
            in results["elastic:root:a"].reason
        )
        assert "policy store unreachable" in results["elastic:root:a"].reason

        def wrong(node, context):
            return binding("somebody-else")

        other = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
        executor = GraphAgentExecutor(services, {}, elastic_binding_factory=wrong)
        results = arun(other.execute({AGENT: root, ELASTIC: executor.execute}))
        assert (
            'returned a binding for node "somebody-else" instead of "elastic:root:a"'
            in results["elastic:root:a"].reason
        )
    finally:
        close(services)


def test_each_elastic_binding_is_built_once_and_its_idempotency_is_remembered(tmp_path):
    services = services_for(tmp_path)
    try:
        built = []

        def factory(node, context):
            built.append(node.node_id)
            return binding(node.node_id, tool=False, idempotent=node.elastic.role.value == "child")

        executor = GraphAgentExecutor(
            services,
            {
                "root": binding(
                    "root",
                    [
                        tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments("a")),
                        final(),
                    ],
                )
            },
            elastic_binding_factory=factory,
        )
        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
        arun(graph.execute(executor.executors()))
        assert sorted(built) == ["elastic:root:a", "join:root"]
        assert executor.idempotent_node_ids() == {"elastic:root:a"}
    finally:
        close(services)


def test_an_exploration_node_can_request_further_exploration_until_the_depth_cap(tmp_path):
    services = services_for(tmp_path)
    try:

        def factory(node, context):
            if node.node_id == "elastic:root:a":
                return binding(
                    node.node_id,
                    [
                        tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments("deeper")),
                        final({"status": "complete", "finding": "needs more"}),
                    ],
                )
            return binding(node.node_id, tool=False)

        executor = GraphAgentExecutor(
            services,
            {
                "root": binding(
                    "root",
                    [
                        tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments("a")),
                        final(),
                    ],
                )
            },
            elastic_binding_factory=factory,
        )
        graph = StateGraph([n("root")], max_elastic_depth=2, max_elastic_nodes=3)
        arun(graph.execute(executor.executors()))
        assert statuses(graph) == {node_id: "completed" for node_id in graph.nodes}
        assert "elastic:elastic:root:a:deeper" in graph.nodes
        assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2
        assert graph.nodes["elastic:elastic:root:a:deeper"].elastic_depth == 2
    finally:
        close(services)


@pytest.mark.parametrize("with_tool", [True, False])
def test_the_request_tool_is_only_wired_when_the_definition_declares_it(tmp_path, with_tool):
    services = services_for(tmp_path)
    try:
        calls = []

        def script(context):
            calls.append(len(calls))
            if len(calls) == 1:
                return tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments())
            return final()

        executor = GraphAgentExecutor(
            services, {"root": binding("root", model=DynamicModel(script), tool=with_tool)}
        )
        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
        results = arun(graph.execute(executor.executors()))
        assert bool(results["root"].spawn_requests) is with_tool
    finally:
        close(services)
