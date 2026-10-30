import pytest

from nailong_agent_sdk.agent.orchestrator import OrchestrationStatus, Orchestrator
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.state.elastic import ELASTIC_REQUEST_TOOL_NAME
from tests.support.agents import arun, final, tool_call
from tests.support.elastic_orchestration import elastic_factory, elastic_policy, graph_of
from tests.support.orchestration import close_all, make_policy, make_request, pipeline, plan_of


class Crash(BaseException):
    pass


def request(request_id):
    return {
        "request_id": request_id,
        "scope": "clock tree",
        "instructions": f"Trace {request_id}.",
        "reason": "The section is ambiguous.",
    }


def requests_then_finish(*request_ids):
    calls = [
        tool_call(f"r{index}", ELASTIC_REQUEST_TOOL_NAME, request(request_id))
        for index, request_id in enumerate(request_ids)
    ]
    return [*calls, final({"status": "complete", "summary": "queued"})]


def plan_with(**caps):
    return plan_of().model_copy(update=caps)


def test_the_controller_carries_the_policy_ceilings(tmp_path):
    orchestrator = Orchestrator(
        tmp_path, make_policy(max_elastic_depth=2, max_elastic_nodes=5, policy_id="ceilings")
    )
    try:
        record = arun(orchestrator.prepare(make_request(plan=plan_with(max_elastic_nodes=5))))
        submitted = orchestrator.submit_for_approval(record.orchestration_id)
        controller = orchestrator.controller_runtime().get_controller(submitted.controller_id)
        assert (controller.elastic_depth_ceiling, controller.elastic_nodes_ceiling) == (2, 5)
    finally:
        orchestrator._telemetry.close()


def test_a_capacity_grant_cannot_pass_the_policy_ceiling(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        escalate=True,
        root_turns={"node:single-plan": requests_then_finish("a", "b")},
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path,
        elastic_policy(max_elastic_depth=1, max_elastic_nodes=4),
        make_request(plan=plan_with(max_elastic_nodes=2), blast=0),
        factory,
    )
    try:
        assert executed.status is OrchestrationStatus.BLOCKED
        runtime = orchestrator.controller_runtime()
        with pytest.raises(ValueError, match="max_elastic_nodes 5: it is above the ceiling 4"):
            runtime.grant_elastic_capacity(
                executed.controller_id, max_elastic_nodes=5, reason="more than the policy allows"
            )
        with pytest.raises(ValueError, match="max_elastic_depth 2: it is above the ceiling 1"):
            runtime.grant_elastic_capacity(
                executed.controller_id, max_elastic_depth=2, reason="deeper than the policy allows"
            )
        graph = graph_of(orchestrator, executed)
        assert graph["capacity_grants"] == [] and graph["max_elastic_nodes"] == 2
        runtime.grant_elastic_capacity(
            executed.controller_id, max_elastic_nodes=4, reason="up to the ceiling"
        )
        resumed = arun(orchestrator.resume_execution(record.orchestration_id, services, factory))
        assert resumed.status is OrchestrationStatus.EXECUTED
        final_graph = graph_of(orchestrator, resumed)
        assert (final_graph["elastic_depth_ceiling"], final_graph["elastic_nodes_ceiling"]) == (
            1,
            4,
        )
    finally:
        close_all(orchestrator, services)


def test_a_request_beyond_the_policy_ceiling_is_refused_to_the_agent_at_once(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        escalate=True,
        root_turns={"node:single-plan": requests_then_finish("a", "b", "c")},
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path,
        elastic_policy(max_elastic_depth=1, max_elastic_nodes=3),
        make_request(plan=plan_with(max_elastic_nodes=3), blast=0),
        factory,
    )
    try:
        assert executed.status is OrchestrationStatus.EXECUTED
        graph = graph_of(orchestrator, executed)
        outcomes = {
            item["request"]["request_id"]: item["status"] for item in graph["spawn_records"]
        }
        assert outcomes == {"a": "accepted", "b": "accepted"}
        told = " ".join(prompts["node:single-plan"])
        assert "which no capacity grant can raise" in told
    finally:
        close_all(orchestrator, services)


def crashed_orchestration(tmp_path, policy):
    seen, prompts = [], {}
    root_turns = {"node:single-plan": requests_then_finish("safe", "risky")}

    class Dying:
        async def next_turn(self, model_context):
            raise Crash()

    first = elastic_factory(
        seen, prompts, root_turns=root_turns, child_model=lambda context: Dying()
    )
    orchestrator = Orchestrator(tmp_path, policy)
    record = arun(orchestrator.prepare(make_request(blast=0)))
    orchestrator.submit_for_approval(record.orchestration_id)
    orchestrator.approve(record.orchestration_id, True, "ok")
    services = AgentRuntimeServices.open(tmp_path)
    with pytest.raises(Crash):
        arun(orchestrator.dispatch_and_execute(record.orchestration_id, services, first))
    close_all(orchestrator, services)
    return record, root_turns


def replay_safe(context):
    if context.elastic is None:
        return True
    return context.elastic.request is not None and context.elastic.request.request_id == "safe"


def test_a_crashed_orchestration_recovers_by_replaying_only_what_is_replay_safe(tmp_path):
    record, root_turns = crashed_orchestration(tmp_path, elastic_policy(max_elastic_nodes=4))
    resumed = Orchestrator.resume(tmp_path, record.orchestration_id)
    services = AgentRuntimeServices.open(tmp_path)
    try:
        dispatched = resumed.get(record.orchestration_id)
        assert dispatched.status is OrchestrationStatus.DISPATCHED
        run = resumed.controller_runtime()._harness.get_run_state(dispatched.graph_run_id)
        assert run.graph["statuses"]["elastic:node:single-plan:safe"] == "running"
        seen, prompts = [], {}
        factory = elastic_factory(seen, prompts, root_turns=root_turns, idempotent=replay_safe)
        recovered = arun(resumed.recover_execution(record.orchestration_id, services, factory))
        graph = graph_of(resumed, recovered)
        assert graph["statuses"] == {
            "node:single-plan": "completed",
            "elastic:node:single-plan:safe": "completed",
            "elastic:node:single-plan:risky": "failed",
            "join:node:single-plan": "completed",
        }
        risky = graph["results"]["elastic:node:single-plan:risky"]
        assert risky["diagnostics"] == ["interrupted-non-idempotent"]
        assert recovered.status is OrchestrationStatus.FAILED
        built = [context.assignment.node_id for context in seen]
        assert "elastic:node:single-plan:safe" in built and "node:single-plan" in built
    finally:
        close_all(resumed, services)


def test_recovery_needs_services_on_the_orchestrator_run_root(tmp_path):
    run_root = tmp_path / "run"
    record, root_turns = crashed_orchestration(run_root, elastic_policy(max_elastic_nodes=4))
    resumed = Orchestrator.resume(run_root, record.orchestration_id)
    elsewhere = AgentRuntimeServices.open(tmp_path / "elsewhere")
    try:
        factory = elastic_factory([], {}, root_turns=root_turns)
        with pytest.raises(
            ValueError, match="AgentRuntimeServices must use the orchestrator run_root."
        ):
            arun(resumed.recover_execution(record.orchestration_id, elsewhere, factory))
        assert resumed.get(record.orchestration_id).status is OrchestrationStatus.DISPATCHED
    finally:
        close_all(resumed, elsewhere)


def test_resuming_needs_services_on_the_orchestrator_run_root(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(
        seen,
        prompts,
        escalate=True,
        root_turns={"node:single-plan": requests_then_finish("a", "b")},
    )
    orchestrator, record, executed, services = pipeline(
        tmp_path / "run",
        elastic_policy(max_elastic_depth=1, max_elastic_nodes=4),
        make_request(plan=plan_with(max_elastic_nodes=2), blast=0),
        factory,
    )
    elsewhere = AgentRuntimeServices.open(tmp_path / "elsewhere")
    try:
        assert executed.status is OrchestrationStatus.BLOCKED
        with pytest.raises(
            ValueError, match="AgentRuntimeServices must use the orchestrator run_root."
        ):
            arun(orchestrator.resume_execution(record.orchestration_id, elsewhere, factory))
        assert orchestrator.get(record.orchestration_id).status is OrchestrationStatus.BLOCKED
    finally:
        close_all(orchestrator, services)
        elsewhere.telemetry.close()


def test_only_a_dispatched_orchestration_can_recover_execution(tmp_path):
    seen, prompts = [], {}
    factory = elastic_factory(seen, prompts, root_turns={})
    orchestrator, record, executed, services = pipeline(
        tmp_path, elastic_policy(), make_request(blast=0), factory
    )
    try:
        assert executed.status is OrchestrationStatus.EXECUTED
        with pytest.raises(
            ValueError,
            match="Only a dispatched orchestration can recover execution; "
            f'"{record.orchestration_id}" is executed',
        ):
            arun(orchestrator.recover_execution(record.orchestration_id, services, factory))
    finally:
        close_all(orchestrator, services)
