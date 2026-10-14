import asyncio
import time

import pytest

from nailong_agent_sdk.agent.base_agent import AgentWatchdogPolicy, BaseAgent
from nailong_agent_sdk.agent.model import (
    FailoverAgentModel,
    ModelStreamCompleted,
    ModelTextDelta,
    ModelTurnResponse,
    ProviderUsage,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus, FinalTurn
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from tests.support.agents import FnExecutor, agent, arun, call, definition, final, task, tool_call
from tests.support.base_agent_cases import ECHO, echo_executor


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
