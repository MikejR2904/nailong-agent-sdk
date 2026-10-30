import asyncio

import pytest

from nailong_agent_sdk.agent.base_agent import AgentWatchdogPolicy, BaseAgent, ToolHookDecision
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.verification import VerificationGateRegistry
from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    EscalationTarget,
    MemoryScope,
    ToolExecutionResult,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError
from tests.support.agents import (
    DynamicModel,
    FnExecutor,
    agent,
    arun,
    batch,
    call,
    definition,
    final,
    ok,
    results_of,
    task,
    tool,
    tool_call,
)
from tests.support.base_agent_cases import ECHO, echo_executor


def test_final_output_accepted_with_events_state_and_profile():
    subject, model = agent([final()])
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 1
    assert result.output == {"status": "complete"} and result.failure is None
    types = [e.type for e in result.events]
    assert types[:4] == ["run-started", "context-assembled", "state-loaded", "context-projected"]
    assert types[-2:] == ["terminated", "state-updated"]
    work_items = {w["work_item_id"]: w["status"] for w in result.project_state["work_items"]}
    assert work_items == {"t1": "completed"}
    assert result.profile["integrity_hash"] and len(model.calls) == 1


def test_invalid_final_output_is_recoverable_with_observation():
    subject, model = agent([final({"status": "nope"}), final()])
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    assert "output-rejected" in [e.type for e in result.events]
    second_context = model.calls[1]
    assert second_context.observations[0].kind == "agent-error"
    assert (
        "candidate output does not match its declared schema"
        in second_context.observations[0].message
    )


def test_failed_tool_result_is_shown_to_the_model_not_terminal():
    executor = FnExecutor(lambda t, c: ToolExecutionResult(status="failed", error="disk exploded"))
    subject, model = agent(
        [tool_call("c1", "echo"), final()], executor=executor, definition_=definition(tools=[ECHO])
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED
    observation = model.calls[1].observations[0]
    assert observation.kind == "tool-result" and "disk exploded" in observation.message
    assert observation.result.handle.handle_id


def test_undeclared_tool_and_missing_executor_terminate_with_named_errors():
    undeclared, _ = agent(
        [tool_call("c1", "ghost")], executor=echo_executor(), definition_=definition(tools=[ECHO])
    )
    result = arun(undeclared.run(task()))
    assert (
        result.status is AgentRunStatus.FAILED and 'Tool "ghost" is not declared' in result.reason
    )
    no_executor, _ = agent([tool_call("c1", "echo")], definition_=definition(tools=[ECHO]))
    result = arun(no_executor.run(task()))
    assert (
        result.status is AgentRunStatus.FAILED and "No tool executor is configured" in result.reason
    )


def test_invalid_tool_arguments_reach_the_model_with_the_tool_and_json_path():
    strict = tool(
        "strict",
        schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        },
    )
    seen = []

    def respond(context):
        seen.append(results_of(context))
        if context.iteration == 1:
            return tool_call("c1", "strict", {"n": "x"})
        return final()

    executor = echo_executor()
    subject = BaseAgent(
        definition(tools=[strict]),
        DynamicModel(respond),
        require_tool_authorization=False,
        tool_executor=executor,
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    observation = seen[1]["c1"]
    assert 'tool "strict" arguments' in observation.message and "$.n" in observation.message
    assert observation.result.status == "failed"
    assert executor.calls == []


def test_invalid_tool_arguments_are_recoverable_like_invalid_final_output():
    strict = tool(
        "strict",
        schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        },
    )
    subject, _ = agent(
        [tool_call("c1", "strict", {"n": "x"}), tool_call("c2", "strict", {"n": 1}), final()],
        executor=echo_executor(),
        definition_=definition(tools=[strict]),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason


def test_blocked_turn_and_max_iterations_and_escalation():
    blocked, _ = agent(
        [{"type": "blocked", "reason": "need approval"}],
        definition_=definition(escalation=EscalationTarget.HUMAN),
    )
    result = arun(blocked.run(task()))
    assert result.status is AgentRunStatus.BLOCKED and result.reason == "need approval"
    assert result.escalation.target == "human"
    looping, _ = agent(
        [tool_call(f"c{i}", "echo") for i in range(3)],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO], max_iterations=3),
    )
    result = arun(looping.run(task()))
    assert (
        result.status is AgentRunStatus.FAILED
        and "Maximum iteration limit (3) reached" in result.reason
    )
    exhausted, _ = agent([], definition_=definition(max_iterations=2))
    result = arun(exhausted.run(task()))
    assert (
        result.status is AgentRunStatus.FAILED and result.failure.code == "SCRIPTED_MODEL_EXHAUSTED"
    )


def test_failed_dependency_reaches_the_model_instead_of_blocking_the_run():
    failing = FnExecutor(
        lambda t, c: (
            ToolExecutionResult(status="failed", error="bad regex")
            if c.call.id == "a"
            else ok({"read": True})
        )
    )
    subject, model = agent(
        [batch(call("a", "echo"), call("b", "echo", depends_on_call_ids=["a"])), final()],
        executor=failing,
        definition_=definition(tools=[ECHO]),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, (result.status, result.reason)


def test_pre_tool_hook_denial_blocks_and_post_hook_runs():
    seen = []

    async def deny(context):
        return ToolHookDecision(False, "policy says no")

    async def after(context, result):
        seen.append(result.status)

    denied, _ = agent(
        [tool_call("c1", "echo"), final()],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
        pre_tool_hooks=[deny],
    )
    result = arun(denied.run(task()))
    assert result.status is AgentRunStatus.BLOCKED and result.reason == "policy says no"
    allowed, _ = agent(
        [tool_call("c1", "echo"), final()],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
        post_tool_hooks=[after],
    )
    arun(allowed.run(task()))
    assert seen == ["succeeded"]


def test_cancellation_token_and_task_cancellation():
    class Token:
        def __init__(self):
            self.flag = False

        def is_cancelled(self):
            return self.flag

    token = Token()

    def cancel_after_first(tool_definition, context):
        token.flag = True
        return ok()

    subject, _ = agent(
        [tool_call("c1", "echo"), final()],
        executor=FnExecutor(cancel_after_first),
        definition_=definition(tools=[ECHO]),
    )
    result = arun(subject.run(task(), cancellation=token))
    assert result.status is AgentRunStatus.CANCELLED and result.reason == "Execution was cancelled."
    started = asyncio.Event()

    class Slow:
        async def next_turn(self, context):
            started.set()
            await asyncio.sleep(30)

    async def scenario():
        cancelling = BaseAgent(definition(), Slow())
        running = asyncio.create_task(cancelling.run(task()))
        await started.wait()
        running.cancel()
        try:
            await running
            return "returned"
        except asyncio.CancelledError:
            return "CancelledError"

    assert arun(scenario()) == "CancelledError"


def test_agent_instance_reuse_message():
    subject, _ = agent([final(), final()])
    arun(subject.run(task()))
    try:
        again = arun(subject.run(task("t2")))
        outcome = ("second run returned", again.status.value)
    except Exception as error:
        outcome = (type(error).__name__, str(error))
    assert outcome[0] in {"second run returned", "RuntimeError", "AgentSdkError", "ValueError"}
    assert "profile" not in outcome[1].lower() or "BaseAgent" in outcome[1], outcome


def test_cross_session_scope_is_rejected_up_front():
    with pytest.raises(AgentSdkError) as excinfo:
        BaseAgent(
            definition(
                memory_scope=MemoryScope.CROSS_SESSION, memory_rationale="needs persistence"
            ),
            ScriptedModel([]),
        )
    assert excinfo.value.code == "CROSS_SESSION_STORE_REQUIRED"


def test_verification_gate_outcomes():
    registry = VerificationGateRegistry()
    registry.register_callable("bool-false", lambda ctx: False)
    registry.register_callable("tuple", lambda ctx: (False, "custom reason"))
    registry.register_callable("raises", lambda ctx: 1 / 0)
    registry.register_callable("bad-type", lambda ctx: "yes")

    async def slow(ctx):
        await asyncio.sleep(30)

    registry.register_callable("slow", slow)

    def run_with(gate_id, watchdog=None):
        subject, _ = agent(
            [final()],
            definition_=definition(gate=gate_id),
            verification_gates=registry,
            watchdog_policy=watchdog,
        )
        return arun(subject.run(task()))

    assert run_with("status-is-complete").status is AgentRunStatus.COMPLETED
    assert run_with("bool-false").status is AgentRunStatus.FAILED
    tuple_result = run_with("tuple")
    assert tuple_result.status is AgentRunStatus.FAILED and tuple_result.reason == "custom reason"
    raised = run_with("raises")
    assert (
        raised.failure.code == "VERIFICATION_GATE_UNEXPECTED_ERROR"
        and "ZeroDivisionError" in raised.reason
    )
    bad = run_with("bad-type")
    assert bad.failure.code == "VERIFICATION_GATE_UNEXPECTED_ERROR" and "TypeError" in bad.reason
    unknown = run_with("not-registered")
    assert (
        unknown.failure.code == "VERIFICATION_GATE_UNKNOWN" and "not-registered" in unknown.reason
    )
    timed = run_with("slow", AgentWatchdogPolicy(verification_timeout_seconds=0.5))
    assert timed.failure.code == "WATCHDOG_VERIFICATION_TIMEOUT"
