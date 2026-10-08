import asyncio
import dataclasses

import pytest

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentExecutor
from nailong_agent_sdk.state.elastic import ELASTIC_REQUEST_TOOL_NAME, GraphSpawnStatus
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeStatus
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import Plan
from tests.support.agents import DynamicModel, arun, final, tool_call
from tests.support.elastic import done_with, req, statuses
from tests.support.elastic_agents import (
    binding,
    close,
    observed,
    request_arguments,
    services_for,
)
from tests.support.plans import AGENT, n, task

ELASTIC = GraphNodeKind.ELASTIC
BLOCKED = GraphNodeStatus.BLOCKED
FAILED = GraphNodeStatus.FAILED


class Crash(BaseException):
    pass


def requesting(seen, name, *request_ids):
    calls = []

    def script(context):
        calls.append(1)
        seen[name].append(observed(context))
        if len(calls) <= len(request_ids):
            return tool_call(
                f"r{len(calls)}",
                ELASTIC_REQUEST_TOOL_NAME,
                request_arguments(request_ids[len(calls) - 1]),
            )
        return final()

    return DynamicModel(script)


def test_siblings_running_together_cannot_both_be_promised_the_last_capacity(tmp_path):
    services = services_for(tmp_path)
    try:
        seen = {"p1": [], "p2": []}
        executor = GraphAgentExecutor(
            services,
            {
                "p1": binding("p1", model=requesting(seen, "p1", "probe")),
                "p2": binding("p2", model=requesting(seen, "p2", "probe")),
            },
            elastic_binding_factory=lambda node, context: binding(node.node_id, tool=False),
        )
        graph = StateGraph([n("p1"), n("p2")], max_elastic_depth=1, max_elastic_nodes=3)
        arun(graph.execute(executor.executors()))
        accepted = [r for r in graph.spawn_records if r.status is GraphSpawnStatus.ACCEPTED]
        assert len(accepted) == 1
        assert not any(r.status is GraphSpawnStatus.DEFERRED for r in graph.spawn_records)
        assert set(statuses(graph).values()) == {"completed"}
        told = [name for name in seen if "held by tasks running at the same time" in seen[name][1]]
        assert told == [name for name in ("p1", "p2") if name != accepted[0].parent_node_id]
        assert '"elastic_nodes_remaining_after_queue": 1' in seen[accepted[0].parent_node_id][1]
    finally:
        close(services)


def test_a_sibling_that_ends_blocked_hands_its_capacity_to_a_later_request(tmp_path):
    services = services_for(tmp_path)
    try:
        seen = {"p1": [], "p2": []}
        gate = None

        class Gated(DynamicModel):
            async def next_turn(self, context):
                await gate.wait()
                return await super().next_turn(context)

        def blocked_after_request():
            calls = []

            def script(context):
                calls.append(1)
                if len(calls) == 1:
                    return tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments("probe"))
                return {"type": "blocked", "reason": "waiting for the designer"}

            return DynamicModel(script)

        later = Gated(requesting(seen, "p2", "probe").fn)
        executor = GraphAgentExecutor(
            services,
            {
                "p1": binding("p1", model=blocked_after_request()),
                "p2": binding("p2", model=later),
            },
            elastic_binding_factory=lambda node, context: binding(node.node_id, tool=False),
        )

        async def run_node(node, context):
            result = await executor.execute(node, context)
            if node.node_id == "p1":
                gate.set()
            return result

        async def scenario():
            nonlocal gate
            gate = asyncio.Event()
            graph = StateGraph([n("p1"), n("p2")], max_elastic_depth=1, max_elastic_nodes=3)
            await graph.execute({AGENT: run_node, ELASTIC: run_node})
            return graph

        graph = arun(scenario())
        assert graph.status("p1") is BLOCKED
        assert [(r.parent_node_id, r.status) for r in graph.spawn_records] == [
            ("p2", GraphSpawnStatus.ACCEPTED)
        ]
        assert "held by tasks running" not in seen["p2"][1]
    finally:
        close(services)


def test_overflow_is_only_queued_for_a_decision_while_a_grant_could_still_fit_it(tmp_path):
    services = services_for(tmp_path)
    try:
        seen = {"root": []}
        executor = GraphAgentExecutor(
            services,
            {"root": binding("root", model=requesting(seen, "root", "a", "b", "c"), escalate=True)},
        )
        graph = StateGraph(
            [n("root"), n("after", ["root"])],
            max_elastic_depth=1,
            max_elastic_nodes=2,
            elastic_nodes_ceiling=3,
        )
        arun(graph.execute(executor.executors()))
        assert '"capacity_decision_required": false' in seen["root"][1]
        assert '"capacity_decision_required": true' in seen["root"][2]
        assert "ceiling" in seen["root"][3]
        assert [(r.request.request_id, r.status) for r in graph.spawn_records] == [
            ("a", GraphSpawnStatus.DEFERRED),
            ("b", GraphSpawnStatus.DEFERRED),
        ]
        graph.grant_elastic_capacity(max_elastic_nodes=3, reason="room for both")
        assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2
    finally:
        close(services)


def test_the_executor_resolves_replay_safety_for_a_node_it_has_not_built_yet(tmp_path):
    services = services_for(tmp_path)
    try:
        built = []

        def factory(node, context):
            built.append(node.node_id)
            return binding(node.node_id, tool=False, idempotent=node.elastic.role.value == "child")

        executor = GraphAgentExecutor(
            services,
            {"root": binding("root", idempotent=True), "plain": binding("plain")},
            elastic_binding_factory=factory,
        )
        graph = StateGraph(
            [n("root"), n("plain"), n("ghost")], max_elastic_depth=1, max_elastic_nodes=3
        )
        graph.start_runnable_wave()
        graph.mark_terminal("root", done_with(req("a")))
        graph.start_runnable_wave()
        child = graph.nodes["elastic:root:a"]
        child_context = graph.execution_context(child.node_id)
        assert executor.is_replayable(child, child_context) is True
        assert executor.is_replayable(child, child_context) is True
        assert built == ["elastic:root:a"]
        assert executor.idempotent_node_ids() == {"root", "elastic:root:a"}
        solo = StateGraph([n("root")])
        solo.start_runnable_wave()
        assert executor.is_replayable(solo.nodes["root"], solo.execution_context("root")) is True
        assert executor.is_replayable(graph.nodes["plain"], graph.execution_context("plain")) is (
            False
        )
        assert executor.is_replayable(graph.nodes["ghost"], graph.execution_context("ghost")) is (
            False
        )
        assert built == ["elastic:root:a"]
    finally:
        close(services)


def test_an_elastic_node_is_not_replayable_without_a_factory_and_a_failing_factory_raises(
    tmp_path,
):
    services = services_for(tmp_path)
    try:
        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
        graph.start_runnable_wave()
        graph.mark_terminal("root", done_with(req("a")))
        graph.start_runnable_wave()
        child = graph.nodes["elastic:root:a"]
        context = graph.execution_context(child.node_id)
        assert GraphAgentExecutor(services, {}).is_replayable(child, context) is False

        def raising(node, node_context):
            raise RuntimeError("policy store unreachable")

        executor = GraphAgentExecutor(services, {}, elastic_binding_factory=raising)
        with pytest.raises(RuntimeError, match="policy store unreachable"):
            executor.is_replayable(child, context)
    finally:
        close(services)


def test_a_factory_that_returns_another_nodes_binding_fails_the_replay_check(tmp_path):
    services = services_for(tmp_path)
    try:
        graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
        graph.start_runnable_wave()
        graph.mark_terminal("root", done_with(req("a")))
        graph.start_runnable_wave()
        child = graph.nodes["elastic:root:a"]
        executor = GraphAgentExecutor(
            services,
            {},
            elastic_binding_factory=lambda node, context: binding("elastic:root:b", tool=False),
        )
        with pytest.raises(
            ValueError,
            match='binding for node "elastic:root:b" instead of "elastic:root:a"',
        ):
            executor.is_replayable(child, graph.execution_context(child.node_id))
        assert executor.idempotent_node_ids() == set()
    finally:
        close(services)


def test_a_binding_that_raises_while_the_task_is_built_fails_the_node_naming_the_cause(
    tmp_path,
):
    services = services_for(tmp_path)
    try:
        broken = binding("root")

        def adapter(node, context):
            raise ValueError("the task adapter has no input")

        executor = GraphAgentExecutor(
            services, {"root": dataclasses.replace(broken, task_adapter=adapter)}
        )
        graph = StateGraph([n("root")])
        arun(graph.execute(executor.executors()))
        result = graph.result("root")
        assert result.status is FAILED
        assert result.reason == (
            "Graph agent invocation raised ValueError: the task adapter has no input"
        )
        assert result.diagnostics == ["error-type:ValueError"]
        assert result.spawn_requests == []
    finally:
        close(services)


def test_recovery_after_a_crash_replays_idempotent_exploration_and_fails_the_rest(tmp_path):
    services = services_for(tmp_path)
    try:
        plan = Plan(plan_id="p", tasks=[task("T1")], max_elastic_depth=1, max_elastic_nodes=4)

        def build_executor():
            def factory(node, context):
                request_id = node.elastic.request.request_id if node.elastic.request else ""
                return binding(
                    node.node_id,
                    [final({"status": "complete", "finding": node.node_id})],
                    tool=False,
                    idempotent=request_id == "safe",
                )

            return GraphAgentExecutor(
                services,
                {
                    "node:T1": binding(
                        "node:T1",
                        [
                            tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, request_arguments("safe")),
                            tool_call("r2", ELASTIC_REQUEST_TOOL_NAME, request_arguments("risky")),
                            final({"status": "complete", "summary": "queued two probes"}),
                        ],
                        idempotent=True,
                    )
                },
                elastic_binding_factory=factory,
            )

        async def dying(node, context):
            raise Crash()

        first = HarnessCoordinator(tmp_path)
        run_id = first.start_run(plan).run_id
        before = build_executor()
        with pytest.raises(Crash):
            arun(first.execute_run(run_id, {AGENT: before.execute, ELASTIC: dying}))
        persisted = HarnessCoordinator(tmp_path).get_run_state(run_id).graph["statuses"]
        assert persisted["elastic:node:T1:safe"] == "running"
        assert persisted["elastic:node:T1:risky"] == "running"

        second = HarnessCoordinator(tmp_path)
        after = build_executor()
        assert after.idempotent_node_ids() == {"node:T1"}
        second.recover_interrupted_run(
            run_id, after.idempotent_node_ids(), is_replayable=after.is_replayable
        )
        recovered = second.get_run_state(run_id).graph["statuses"]
        assert recovered["elastic:node:T1:safe"] == "runnable"
        assert recovered["elastic:node:T1:risky"] == "failed"
        arun(second.execute_run(run_id, after.executors()))
        final_state = second.get_run_state(run_id).graph
        assert final_state["statuses"] == {
            "node:T1": "completed",
            "elastic:node:T1:safe": "completed",
            "elastic:node:T1:risky": "failed",
            "join:node:T1": "completed",
        }
        risky = final_state["results"]["elastic:node:T1:risky"]
        assert risky["diagnostics"] == ["interrupted-non-idempotent"]
    finally:
        close(services)
