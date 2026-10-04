import asyncio
import time

import pytest

from nailong_agent_sdk.agent.base_agent import AgentWatchdogPolicy, BaseAgent, ToolHookDecision
from nailong_agent_sdk.agent.model import (
    FailoverAgentModel,
    ModelStreamCompleted,
    ModelTextDelta,
    ModelTurnResponse,
    ProviderUsage,
    ScriptedModel,
)
from nailong_agent_sdk.agent.verification import VerificationGateRegistry
from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    EscalationTarget,
    FinalTurn,
    MemoryScope,
    ToolConcurrency,
    ToolExecutionResult,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
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

ECHO = tool("echo")


def echo_executor():
    return FnExecutor(lambda t, c: ok({"echo": c.call.arguments}))


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
    subject = BaseAgent(definition(tools=[strict]), DynamicModel(respond), tool_executor=executor)
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


def test_watchdogs_model_tool_and_run_deadline():
    class SlowModel:
        async def next_turn(self, context):
            await asyncio.sleep(30)

    started = time.monotonic()
    result = arun(
        BaseAgent(
            definition(),
            SlowModel(),
            watchdog_policy=AgentWatchdogPolicy(model_turn_timeout_seconds=0.5),
        ).run(task())
    )
    assert result.status is AgentRunStatus.FAILED and "WATCHDOG_MODEL_TIMEOUT" in result.reason
    assert time.monotonic() - started < 5

    async def slow_tool(tool_definition, context):
        await asyncio.sleep(30)

    subject, _ = agent(
        [tool_call("c1", "echo"), final()],
        executor=FnExecutor(slow_tool),
        definition_=definition(tools=[ECHO]),
        watchdog_policy=AgentWatchdogPolicy(tool_call_timeout_seconds=0.5),
    )
    result = arun(subject.run(task()))
    assert result.failure.code == "WATCHDOG_TOOL_TIMEOUT" and '"echo"' in result.reason

    class Plodding:
        async def next_turn(self, context):
            await asyncio.sleep(0.4)
            return {"type": "tool-call", "call": call(f"c{context.iteration}", "echo")}

    deadline_agent = BaseAgent(
        definition(tools=[ECHO], max_iterations=50),
        Plodding(),
        tool_executor=echo_executor(),
        watchdog_policy=AgentWatchdogPolicy(run_deadline_seconds=1.5),
    )
    result = arun(deadline_agent.run(task()))
    assert result.status is AgentRunStatus.FAILED and "WATCHDOG" in result.reason
    assert result.failure is not None and result.failure.code.startswith("WATCHDOG_"), (
        result.failure
    )
    assert result.iterations < 10


def test_transient_provider_errors_are_not_hammered_without_backoff():
    attempts = []

    class Flaky:
        async def next_turn(self, context):
            attempts.append(time.monotonic())
            if len(attempts) < 4:
                raise TransientProviderError("HTTP_429", "rate limited", retry_after_seconds=1.0)
            return {"type": "final", "output": {"status": "complete"}}

    result = arun(BaseAgent(definition(max_iterations=5), Flaky()).run(task()))
    gaps = [round(b - a, 3) for a, b in zip(attempts, attempts[1:])]
    assert result.status is AgentRunStatus.COMPLETED
    assert min(gaps) >= 0.9, f"retried after {min(gaps)}s although the server asked for 1.0s"


def test_provider_timeouterror_is_not_reported_as_a_watchdog_timeout():
    class SocketTimeout:
        async def next_turn(self, context):
            raise TimeoutError("read timed out")

    result = arun(BaseAgent(definition(), SocketTimeout()).run(task()))
    assert "read timed out" in result.reason or "TimeoutError" in result.reason, result.reason


def test_failover_model_semantics_and_concurrent_attempt_attribution():
    class Failing:
        def __init__(self, tag):
            self.tag = tag

        async def next_turn(self, context):
            await asyncio.sleep(0.2 if context.iteration == 1 else 0.02)
            raise RuntimeError(f"{self.tag}-error-for-iteration-{context.iteration}")

    failover = FailoverAgentModel([Failing("A"), Failing("B")], max_retries_per_model=0)

    class Ctx:
        def __init__(self, iteration):
            self.iteration = iteration

    async def scenario():
        results = await asyncio.gather(
            failover.next_turn(Ctx(1)), failover.next_turn(Ctx(2)), return_exceptions=True
        )
        return results

    first, second = arun(scenario())
    assert "iteration-1" in str(first), str(first)
    assert "iteration-2" in str(second), str(second)
    for failure in (first, second):
        assert "failed after 2 attempt(s) total" in str(failure), str(failure)
        assert len(failure.details["attempts"]) == 2

    calls = {"n": 0}

    class Transient:
        async def next_turn(self, context):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientProviderError("X", "boom", retry_after_seconds=0.05)
            return {"type": "final", "output": {"status": "complete"}}

    retrying = FailoverAgentModel([Transient()], max_retries_per_model=3, base_backoff_seconds=0.01)
    outcome = arun(retrying.next_turn(Ctx(1)))
    assert outcome["type"] == "final" and [a.retry_number for a in retrying.attempts] == [0, 1]
    with pytest.raises(AgentSdkError) as excinfo:
        arun(FailoverAgentModel([Failing("A")], max_retries_per_model=0).next_turn(Ctx(5)))
    assert excinfo.value.code == "MODEL_FALLBACK_EXHAUSTED" and "A-error-for-iteration-5" in str(
        excinfo.value
    )


def test_failover_attempt_history_stays_bounded():
    class Failing:
        async def next_turn(self, context):
            raise RuntimeError("down")

    class Ctx:
        iteration = 1

    failover = FailoverAgentModel([Failing()], max_retries_per_model=0)
    for _ in range(1_100):
        with pytest.raises(AgentSdkError):
            arun(failover.next_turn(Ctx()))
    assert len(failover.attempts) == 1_000


def test_streaming_listener_and_failures():
    deltas = []

    class Streamer:
        async def next_turn(self, context):
            raise AssertionError("must stream")

        async def stream_turn(self, context, on_delta):
            result = on_delta(ModelTextDelta(text="hel"))
            if asyncio.iscoroutine(result):
                await result
            await_result = on_delta(ModelStreamCompleted(usage=None))
            if asyncio.iscoroutine(await_result):
                await await_result
            return ModelTurnResponse(
                turn=FinalTurn(output={"status": "complete"}),
                usage=ProviderUsage(input_tokens=5, output_tokens=2),
            )

    async def listener(event):
        deltas.append(type(event).__name__)

    result = arun(BaseAgent(definition(), Streamer(), on_model_stream=listener).run(task()))
    assert result.status is AgentRunStatus.COMPLETED and deltas == [
        "ModelTextDelta",
        "ModelStreamCompleted",
    ]

    def exploding_listener(event):
        raise RuntimeError("ui bug")

    class StreamerBadListener(Streamer):
        async def stream_turn(self, context, on_delta):
            on_delta(ModelTextDelta(text="x"))
            return FinalTurn(output={"status": "complete"})

    result = arun(
        BaseAgent(definition(), StreamerBadListener(), on_model_stream=exploding_listener).run(
            task()
        )
    )
    assert result.status is AgentRunStatus.COMPLETED, result.reason


def test_huge_tool_output_is_projected_not_replayed():
    huge = "x" * 5_000_000
    executor = FnExecutor(lambda t, c: ok({"blob": huge}))
    subject, model = agent(
        [tool_call("c1", "echo"), final()], executor=executor, definition_=definition(tools=[ECHO])
    )
    result = arun(subject.run(task()))
    observation = model.calls[1].observations[0]
    assert result.status is AgentRunStatus.COMPLETED
    assert len(observation.model_dump_json()) < 20_000
    assert len(str(result.project_state)) < 50_000


def test_parallel_safe_batch_concurrency_is_bounded():
    peak = {"now": 0, "max": 0}
    parallel = tool("par", concurrency=ToolConcurrency.PARALLEL_SAFE)

    async def handler(tool_definition, context):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(0.05)
        peak["now"] -= 1
        return ok()

    calls = [call(f"c{i}", "par") for i in range(300)]
    subject, _ = agent(
        [batch(*calls), final()],
        executor=FnExecutor(handler),
        definition_=definition(tools=[parallel]),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED
    assert peak["max"] <= 32, f"{peak['max']} parallel-safe calls ran at once"


def test_serial_tool_runs_alone_and_dependencies_order(tmp_path):
    order = []

    async def handler(tool_definition, context):
        order.append(("start", context.call.id))
        await asyncio.sleep(0.05)
        order.append(("end", context.call.id))
        return ok()

    serial = tool("ser")
    subject, _ = agent(
        [
            batch(call("a", "ser"), call("b", "ser"), call("c", "ser", depends_on_call_ids=["a"])),
            final(),
        ],
        executor=FnExecutor(handler),
        definition_=definition(tools=[serial]),
    )
    arun(subject.run(task()))
    starts = [item[1] for item in order if item[0] == "start"]
    assert starts == ["a", "b", "c"]
    for index in range(0, len(order) - 1, 2):
        assert order[index][0] == "start" and order[index + 1][0] == "end"


def test_telemetry_and_audit_record_the_run(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    audit = AuditTranscriptStore(tmp_path)
    try:
        subject, _ = agent(
            [tool_call("c1", "echo", {"q": 1}), final()],
            executor=echo_executor(),
            definition_=definition(tools=[ECHO]),
            telemetry=telemetry,
            audit_logs=audit,
        )
        result = arun(subject.run(task("audited")))
        assert result.status is AgentRunStatus.COMPLETED
        events = telemetry.list_events("audited", limit=500)
        types = [e.event_type for e in events]
        assert (
            "agent.run-started" in types
            and "agent.terminated" in types
            and "agent.profile-completed" in types
        )
        assert telemetry.verify_run_chain("audited") is True
        entries = audit.list_entries("audited")
        kinds = [e.event_type for e in entries]
        assert "task-received" in kinds and "model-turn" in kinds and "tool-result" in kinds
        assert audit.verify("audited") is True
        metrics = {
            m.metric_id: m.value for m in telemetry.list_metrics("audited") if m.value is not None
        }
        assert (
            metrics["agent.tool_call_attempt_count"] == 1
            and metrics["agent.model_turn_attempt_count"] == 2
        )
    finally:
        telemetry.close()


def test_terminal_metrics_are_per_run_and_not_truncated(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        executor = echo_executor()
        counts = []
        for _ in range(2):
            subject, _ = agent(
                [
                    tool_call("c1", "echo"),
                    tool_call("c2", "echo"),
                    tool_call("c3", "echo"),
                    final(),
                ],
                executor=executor,
                definition_=definition(tools=[ECHO], max_iterations=6),
                telemetry=telemetry,
            )
            arun(subject.run(task("shared-task-id")))
            observed = [
                m.value
                for m in telemetry.list_metrics("shared-task-id")
                if m.metric_id == "agent.tool_call_attempt_count"
            ]
            counts.append(observed[-1])
        assert counts == [3.0, 3.0], counts
    finally:
        telemetry.close()


def test_terminal_metrics_survive_long_runs(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        executor = echo_executor()
        turns = 150
        scripted = [tool_call(f"x{i}", "echo") for i in range(turns)] + [final()]
        long_agent, _ = agent(
            scripted,
            executor=executor,
            definition_=definition(tools=[ECHO], max_iterations=turns + 5),
            telemetry=telemetry,
        )
        arun(long_agent.run(task("long-run")), timeout=600)
        values = {
            m.metric_id: m.value for m in telemetry.list_metrics("long-run") if m.value is not None
        }
        assert values["agent.tool_call_attempt_count"] == float(turns), values
    finally:
        telemetry.close()
