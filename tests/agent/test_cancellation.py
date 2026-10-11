import asyncio
import threading
import time

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.model import ModelTurnResponse, ProviderContinuation
from nailong_agent_sdk.agent.verification import VerificationGateRegistry
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.support.agents import (
    TURN_ADAPTER,
    FnExecutor,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)

CANCELLED = AgentRunStatus.CANCELLED


class Token:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def is_cancelled(self):
        return self._event.is_set()


def cancel_after(token, seconds):
    timer = threading.Timer(seconds, token.cancel)
    timer.daemon = True
    timer.start()
    return timer


def run_cancelled(subject, delay=0.2, limit=10):
    token = Token()
    cancel_after(token, delay)
    started = time.monotonic()
    result = arun(subject.run(task(), token), timeout=30)
    assert time.monotonic() - started < limit, "the run did not stop promptly"
    return result


class HangingModel:
    def __init__(self):
        self.entered = False
        self.interrupted = False

    async def next_turn(self, context):
        self.entered = True
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.interrupted = True
            raise


def test_cancelling_interrupts_a_model_call_that_has_not_returned():
    model = HangingModel()
    result = run_cancelled(BaseAgent(definition(), model))
    assert result.status is CANCELLED
    assert result.reason == "Execution was cancelled while the model turn was running."
    assert model.entered and model.interrupted


def test_a_run_that_is_cancelled_before_it_starts_stops_at_the_first_check():
    token = Token()
    token.cancel()
    model = HangingModel()
    result = arun(BaseAgent(definition(), model).run(task(), token), timeout=30)
    assert result.status is CANCELLED and result.reason == "Execution was cancelled."
    assert not model.entered


def test_cancelling_interrupts_a_tool_call_and_records_it_as_interrupted():
    state = {"entered": False, "interrupted": False}

    async def slow(tool_definition, context):
        state["entered"] = True
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state["interrupted"] = True
            raise

    from nailong_agent_sdk.agent.model import ScriptedModel

    subject = BaseAgent(
        definition(tools=[tool("slow")]),
        ScriptedModel([tool_call("c1", "slow"), final()]),
        tool_executor=FnExecutor(slow),
    )
    result = run_cancelled(subject)
    assert result.status is CANCELLED
    assert result.reason == 'Tool "slow" was interrupted because the run was cancelled.'
    assert state == {"entered": True, "interrupted": True}


def test_cancelling_interrupts_every_call_of_a_parallel_batch():
    from nailong_agent_sdk.agent.model import ScriptedModel
    from nailong_agent_sdk.foundations.contracts import ToolConcurrency
    from tests.support.agents import batch, call

    entered = []

    async def slow(tool_definition, context):
        entered.append(context.call.id)
        await asyncio.sleep(60)

    subject = BaseAgent(
        definition(tools=[tool("slow", concurrency=ToolConcurrency.PARALLEL_SAFE)]),
        ScriptedModel([batch(call("a", "slow"), call("b", "slow"), call("c", "slow")), final()]),
        tool_executor=FnExecutor(slow),
    )
    result = run_cancelled(subject)
    assert result.status is CANCELLED and sorted(entered) == ["a", "b", "c"]


def test_cancelling_interrupts_a_verification_gate_and_names_it():
    registry = VerificationGateRegistry()
    state = {"interrupted": False}

    async def slow(context):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state["interrupted"] = True
            raise

    registry.register_callable("slow-gate", slow)
    from nailong_agent_sdk.agent.model import ScriptedModel

    subject = BaseAgent(
        definition(gate="slow-gate"), ScriptedModel([final()]), verification_gates=registry
    )
    result = run_cancelled(subject)
    assert result.status is CANCELLED
    assert (
        result.reason == "Execution was cancelled while verification gate 'slow-gate' was running."
    )
    assert state["interrupted"]


class Continuing:
    def __init__(self):
        self.interrupted = False

    async def next_turn(self, context):
        return ModelTurnResponse(
            turn=TURN_ADAPTER.validate_python(tool_call("c1", "probe")),
            continuation=ProviderContinuation(provider="p", state={}),
        )

    async def accept_tool_results(self, continuation, results):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.interrupted = True
            raise


def test_cancelling_interrupts_a_provider_continuation():
    model = Continuing()
    subject = BaseAgent(
        definition(tools=[tool("probe")]),
        model,
        tool_executor=FnExecutor(lambda tool_definition, context: ok({"seen": True})),
    )
    result = run_cancelled(subject)
    assert result.status is CANCELLED
    assert result.reason == "Execution was cancelled while forwarding tool results to the provider."
    assert model.interrupted


def test_an_operation_that_finishes_wins_over_a_cancellation_that_arrives_later():
    from nailong_agent_sdk.agent.model import ScriptedModel

    token = Token()
    cancel_after(token, 30)
    subject = BaseAgent(definition(), ScriptedModel([final()]))
    result = arun(subject.run(task(), token), timeout=30)
    assert result.status is AgentRunStatus.COMPLETED


def test_cancelling_leaves_no_watcher_task_behind():
    async def scenario():
        token = Token()
        cancel_after(token, 0.2)
        subject = BaseAgent(definition(), HangingModel())
        result = await subject.run(task(), token)
        await asyncio.sleep(0.2)
        stray = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return result, stray

    result, stray = asyncio.run(scenario())
    assert result.status is CANCELLED and stray == []


def test_a_timeout_still_wins_when_the_token_is_never_cancelled():
    from nailong_agent_sdk.agent.base_agent import AgentWatchdogPolicy

    subject = BaseAgent(
        definition(),
        HangingModel(),
        watchdog_policy=AgentWatchdogPolicy(model_turn_timeout_seconds=0.3),
    )
    result = arun(subject.run(task(), Token()), timeout=30)
    assert (
        result.status is AgentRunStatus.FAILED and result.failure.code == "WATCHDOG_MODEL_TIMEOUT"
    )
