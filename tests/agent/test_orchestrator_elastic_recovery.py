import asyncio

import pytest

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.orchestrator import OrchestrationStatus, Orchestrator
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    ToolExecutionResult,
    VersionedInstructions,
)
from nailong_agent_sdk.state.elastic import ELASTIC_REQUEST_TOOL_NAME
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from tests.support.agents import COMPLETE_SCHEMA, arun, final, tool_call
from tests.support.elastic_orchestration import CORE, TOOLS, elastic_policy, graph_of
from tests.support.orchestration import close_all, make_request

ROOT = "node:single-plan"
SAFE = "elastic:node:single-plan:safe"
RISKY = "elastic:node:single-plan:risky"
JOIN = "join:node:single-plan"
NOTE = "Both dividers are verified; only the reset tree remains."


class Crash(BaseException):
    pass


class Ledger:
    def __init__(self):
        self.effects = []
        self.contexts = {}
        self.prompts = {}

    def effects_of(self, node_id):
        return [item for item in self.effects if item[0] == node_id]

    def text_of(self, node_id):
        return " ".join(self.prompts.get(node_id, []))


class Counting:
    def __init__(self, ledger, node_id):
        self.ledger = ledger
        self.node_id = node_id

    async def execute(self, tool, invocation):
        self.ledger.effects.append((self.node_id, tool.name, dict(invocation.call.arguments)))
        return ToolExecutionResult(
            status="succeeded", output={"performed": len(self.ledger.effects)}
        )


def spawn(request_id, **extra):
    return {
        "request_id": request_id,
        "scope": f"the {request_id} divider",
        "instructions": f"Trace the {request_id} divider.",
        "reason": "The section is ambiguous.",
        **extra,
    }


ROOT_TURNS = [
    tool_call("r1", ELASTIC_REQUEST_TOOL_NAME, spawn("safe", handoff=NOTE)),
    tool_call("r2", ELASTIC_REQUEST_TOOL_NAME, spawn("risky")),
    final({"status": "complete", "summary": "queued two probes"}),
]


def recording_model(ledger, node_id, turns, *, crash_when_effects=None, crash_on_start=False):
    class Model(ScriptedModel):
        async def next_turn(self, model_context):
            ledger.contexts.setdefault(node_id, []).append(model_context)
            ledger.prompts.setdefault(node_id, []).append(model_context.prompt.model_dump_json())
            if crash_on_start:
                raise Crash()
            if crash_when_effects is not None and model_context.iteration >= 2:
                while len(ledger.effects) < crash_when_effects:
                    await asyncio.sleep(0.01)
                raise Crash()
            return await super().next_turn(model_context)

    return Model(turns)


def factory_for(ledger, *, idempotent, crash=None):
    crash = crash or {}

    def factory(context):
        node_id = context.assignment.node_id
        role = "root" if context.elastic is None else context.elastic.role.value
        names = TOOLS if role == "root" else ("brief",)
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

        def model_factory(node, node_context):
            if role == "root":
                return recording_model(ledger, node_id, ROOT_TURNS)
            if role == "join":
                return recording_model(
                    ledger,
                    node_id,
                    [final({"status": "complete", "answer": "merged"})],
                    crash_on_start=crash.get("join", False),
                )
            work = [
                tool_call(f"c-{len(node_id)}", "brief", {"text": f"notes of {node_id}"}),
                final({"status": "complete", "finding": f"finding of {node_id}"}),
            ]
            return recording_model(
                ledger,
                node_id,
                work,
                crash_when_effects=crash.get("children_after_effects"),
            )

        return GraphAgentBinding(
            node_id=node_id,
            definition=definition,
            task_adapter=task_adapter,
            model_factory=model_factory,
            tool_executor_factory=lambda node, node_context: Counting(ledger, node_id),
            idempotent=idempotent(context),
        )

    return factory


def replay_safe(context):
    if context.elastic is None:
        return True
    request = context.elastic.request
    return request is not None and request.request_id == "safe"


def replay_everything(context):
    return True


def replay_nothing(context):
    return False


def run_until_crash(tmp_path, ledger, *, idempotent, crash):
    orchestrator = Orchestrator(tmp_path, elastic_policy(max_elastic_nodes=4))
    record = arun(orchestrator.prepare(make_request(blast=0)))
    orchestrator.submit_for_approval(record.orchestration_id)
    orchestrator.approve(record.orchestration_id, True, "ok")
    services = AgentRuntimeServices.open(tmp_path)
    factory = factory_for(ledger, idempotent=idempotent, crash=crash)
    with pytest.raises(Crash):
        arun(orchestrator.dispatch_and_execute(record.orchestration_id, services, factory))
    close_all(orchestrator, services)
    return record


def recover(tmp_path, ledger, record, *, idempotent):
    resumed = Orchestrator.resume(tmp_path, record.orchestration_id)
    services = AgentRuntimeServices.open(tmp_path)
    factory = factory_for(ledger, idempotent=idempotent)
    try:
        recovered = arun(resumed.recover_execution(record.orchestration_id, services, factory))
        return resumed, services, recovered
    except BaseException:
        close_all(resumed, services)
        raise


def test_recovery_repeats_a_side_effect_only_for_a_binding_declared_replay_safe(tmp_path):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_safe, crash={"children_after_effects": 2}
    )
    assert {item[0] for item in ledger.effects} == {SAFE, RISKY}
    assert len(ledger.effects) == 2
    before = list(ledger.effects)
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_safe)
    try:
        assert ledger.effects[:2] == before
        assert [item[0] for item in ledger.effects[2:]] == [SAFE]
        assert len(ledger.effects_of(SAFE)) == 2 and len(ledger.effects_of(RISKY)) == 1
        graph = graph_of(resumed, recovered)
        assert graph["statuses"] == {
            ROOT: "completed",
            SAFE: "completed",
            RISKY: "failed",
            JOIN: "completed",
        }
        assert graph["results"][RISKY]["diagnostics"] == ["interrupted-non-idempotent"]
        assert recovered.status is OrchestrationStatus.FAILED
    finally:
        close_all(resumed, services)


def test_a_replayed_node_starts_its_task_over_and_does_not_resume_its_conversation(tmp_path):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_safe, crash={"children_after_effects": 2}
    )
    first_attempt = list(ledger.contexts[SAFE])
    assert [context.iteration for context in first_attempt] == [1, 2]
    resumed, services, _ = recover(tmp_path, ledger, record, idempotent=replay_safe)
    try:
        attempts = ledger.contexts[SAFE]
        replay = attempts[len(first_attempt) :]
        assert replay[0].iteration == 1 and replay[0].continuation is None
        assert not any(o.kind == "tool-result" for o in replay[0].observations)
        assert [context.iteration for context in replay] == [1, 2]
        assert len(ledger.contexts.get(RISKY, [])) == len(first_attempt)
    finally:
        close_all(resumed, services)


def test_the_join_receives_the_findings_and_the_handoff_note_after_a_crash(tmp_path):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_everything, crash={"children_after_effects": 2}
    )
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_everything)
    try:
        assert recovered.status is OrchestrationStatus.EXECUTED
        join_text = ledger.text_of(JOIN)
        assert f"finding of {SAFE}" in join_text and f"finding of {RISKY}" in join_text
        assert NOTE in join_text
        assert NOTE not in ledger.text_of(SAFE) and NOTE not in ledger.text_of(RISKY)
        assert len(ledger.contexts[JOIN]) == 1
    finally:
        close_all(resumed, services)


def test_a_crash_inside_the_join_keeps_every_finished_child_and_runs_none_of_them_again(tmp_path):
    ledger = Ledger()
    record = run_until_crash(tmp_path, ledger, idempotent=replay_everything, crash={"join": True})
    assert len(ledger.effects) == 2 and len(ledger.contexts[JOIN]) == 1
    persisted = Orchestrator.resume(tmp_path, record.orchestration_id)
    try:
        run = persisted.controller_runtime()._harness.get_run_state(
            persisted.get(record.orchestration_id).graph_run_id
        )
        assert run.graph["statuses"][JOIN] == "running"
        assert run.graph["statuses"][SAFE] == run.graph["statuses"][RISKY] == "completed"
    finally:
        persisted._telemetry.close()
    before = list(ledger.effects)
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_everything)
    try:
        assert ledger.effects == before
        assert recovered.status is OrchestrationStatus.EXECUTED
        join_text = ledger.text_of(JOIN)
        assert f"finding of {SAFE}" in join_text and f"finding of {RISKY}" in join_text
        assert NOTE in join_text
        assert len(ledger.contexts[JOIN]) == 2
    finally:
        close_all(resumed, services)


def test_a_join_that_is_not_declared_replay_safe_fails_and_keeps_the_findings(tmp_path):
    ledger = Ledger()
    record = run_until_crash(tmp_path, ledger, idempotent=replay_nothing, crash={"join": True})
    before = list(ledger.effects)
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_nothing)
    try:
        assert ledger.effects == before and len(ledger.contexts[JOIN]) == 1
        graph = graph_of(resumed, recovered)
        assert graph["statuses"][JOIN] == "failed"
        assert graph["results"][JOIN]["diagnostics"] == ["interrupted-non-idempotent"]
        assert graph["results"][SAFE]["output"]["finding"] == f"finding of {SAFE}"
        assert graph["results"][RISKY]["output"]["finding"] == f"finding of {RISKY}"
        assert recovered.status is OrchestrationStatus.FAILED
    finally:
        close_all(resumed, services)


def test_a_failed_non_idempotent_node_enters_the_bounded_repair_path(tmp_path):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_safe, crash={"children_after_effects": 2}
    )
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_safe)
    try:
        controller = resumed.controller_runtime().get_controller(recovered.controller_id)
        assert controller.phase is ControllerPhase.REPAIR_REQUIRED
        assert controller.repair_attempts == 1
        failures = [e for e in controller.events if e.type == "bounded-repair-requested"]
        assert "Graph execution failed at nodes" in failures[0].details["reason"]
        assert RISKY in failures[0].details["reason"]
    finally:
        close_all(resumed, services)


def test_the_host_can_replay_one_interrupted_node_explicitly_instead_of_declaring_it(tmp_path):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_nothing, crash={"children_after_effects": 2}
    )
    resumed = Orchestrator.resume(tmp_path, record.orchestration_id)
    services = AgentRuntimeServices.open(tmp_path)
    try:
        controller_id = resumed.get(record.orchestration_id).controller_id
        run = resumed.controller_runtime().recover_interrupted_graph(
            controller_id, replayable_node_ids={RISKY}
        )
        assert run.graph["statuses"][RISKY] in {"pending", "runnable"}
        assert run.graph["statuses"][SAFE] == "failed"
        assert run.graph["results"][SAFE]["diagnostics"] == ["interrupted-non-idempotent"]
        before = list(ledger.effects)
        factory = factory_for(ledger, idempotent=replay_nothing)
        recovered = arun(resumed.recover_execution(record.orchestration_id, services, factory))
        assert [item[0] for item in ledger.effects[len(before) :]] == [RISKY]
        assert graph_of(resumed, recovered)["statuses"][RISKY] == "completed"
    finally:
        close_all(resumed, services)


def test_recovering_again_after_the_outcome_is_recorded_repeats_nothing(tmp_path):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_everything, crash={"children_after_effects": 2}
    )
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_everything)
    try:
        assert recovered.status is OrchestrationStatus.EXECUTED
        effects = list(ledger.effects)
        built = {node: len(items) for node, items in ledger.contexts.items()}
        with pytest.raises(ValueError, match="Only a dispatched orchestration can recover"):
            arun(
                resumed.recover_execution(
                    record.orchestration_id,
                    services,
                    factory_for(ledger, idempotent=replay_everything),
                )
            )
        assert ledger.effects == effects
        assert {node: len(items) for node, items in ledger.contexts.items()} == built
    finally:
        close_all(resumed, services)


def test_a_second_process_that_lost_the_binding_declaration_fails_the_node_rather_than_guess(
    tmp_path,
):
    ledger = Ledger()
    record = run_until_crash(
        tmp_path, ledger, idempotent=replay_everything, crash={"children_after_effects": 2}
    )
    before = list(ledger.effects)
    resumed, services, recovered = recover(tmp_path, ledger, record, idempotent=replay_nothing)
    try:
        assert ledger.effects == before
        graph = graph_of(resumed, recovered)
        assert graph["statuses"][SAFE] == graph["statuses"][RISKY] == "failed"
        assert recovered.status is OrchestrationStatus.FAILED
    finally:
        close_all(resumed, services)
