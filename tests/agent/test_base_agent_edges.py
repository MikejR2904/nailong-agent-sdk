import asyncio

import pytest

from nailong_agent_sdk.agent.base_agent import AgentWatchdogPolicy, BaseAgent
from nailong_agent_sdk.agent.model import (
    ModelTurnResponse,
    ProviderContinuation,
    ProviderUsage,
    ScriptedModel,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.profiler import AgentRunProfiler
from nailong_agent_sdk.observability.telemetry_models import TelemetryContext
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.project_state_models import StateTransitionKind
from nailong_agent_sdk.state.project_state_store import InMemoryProjectStateStore
from tests.support.agents import (
    TURN_ADAPTER,
    FnExecutor,
    agent,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)

FAILED = AgentRunStatus.FAILED


class UsageModel:
    def __init__(self, usage):
        self.usage = usage

    async def next_turn(self, context):
        return ModelTurnResponse(turn=TURN_ADAPTER.validate_python(final()), usage=self.usage)


def metrics_of(store, run_id="run-1"):
    by_id = {}
    for metric in store.list_metrics(run_id):
        by_id.setdefault(metric.metric_id, []).append(metric)
    return by_id


def run_with_telemetry(tmp_path, model):
    store = TelemetryStore(tmp_path)
    try:
        subject = BaseAgent(
            definition(),
            model,
            telemetry=store,
            telemetry_context=TelemetryContext(
                run_id="run-1", task_id="t1", agent_id="audit-agent"
            ),
        )
        arun(subject.run(task()))
        return metrics_of(store)
    finally:
        store.close()


def test_provider_reported_usage_becomes_metrics_and_unreported_counters_are_unavailable(tmp_path):
    usage = ProviderUsage(
        input_tokens=120, output_tokens=30, context_window_tokens=1000, request_id="req-1"
    )
    metrics = run_with_telemetry(tmp_path, UsageModel(usage))
    reported = {
        "model.provider_input_tokens": 120.0,
        "model.provider_output_tokens": 30.0,
        "model.provider_context_window_tokens": 1000.0,
        "model.provider_remaining_context_tokens": 880.0,
    }
    for metric_id, value in reported.items():
        (observation,) = metrics[metric_id]
        assert observation.value == value and observation.availability.value == "available"
    for metric_id in ("model.provider_cached_input_tokens", "model.provider_reasoning_tokens"):
        (observation,) = metrics[metric_id]
        assert observation.value is None and observation.availability.value == "unavailable"
        assert (
            observation.unavailable_reason == "Selected provider did not report this token counter."
        )


def test_usage_without_a_context_window_leaves_the_remaining_tokens_unavailable(tmp_path):
    metrics = run_with_telemetry(tmp_path, UsageModel(ProviderUsage(input_tokens=50)))
    (remaining,) = metrics["model.provider_remaining_context_tokens"]
    assert remaining.value is None
    assert remaining.unavailable_reason == (
        "Provider did not report both context-window capacity and input usage."
    )


def test_an_adapter_that_reports_no_usage_leaves_every_token_metric_unavailable(tmp_path):
    metrics = run_with_telemetry(tmp_path, ScriptedModel([final()]))
    for metric_id in (
        "model.provider_input_tokens",
        "model.provider_output_tokens",
        "model.provider_remaining_context_tokens",
    ):
        (observation,) = metrics[metric_id]
        assert observation.value is None
        assert (
            observation.unavailable_reason
            == "Selected model adapter did not return provider usage."
        )


def test_an_expired_run_deadline_fails_the_run_before_any_model_call():
    subject, model = agent(
        [final()], watchdog_policy=AgentWatchdogPolicy(run_deadline_seconds=1e-9)
    )
    result = arun(subject.run(task()))
    assert result.status is FAILED and result.iterations == 0
    assert result.failure.code == "WATCHDOG_RUN_DEADLINE"
    assert result.failure.details == {"run_deadline_seconds": 1e-9, "completed_iterations": 0}
    assert "overall run deadline after 0 completed iteration(s)" in result.reason
    assert model.calls == []


def test_a_profiler_that_already_profiled_a_run_is_refused_with_guidance():
    profiler = AgentRunProfiler()
    arun(BaseAgent(definition(), ScriptedModel([final()]), profiler=profiler).run(task("first")))
    second = BaseAgent(definition(), ScriptedModel([final()]), profiler=profiler)
    with pytest.raises(RuntimeError) as raised:
        arun(second.run(task("second")))
    assert "already profiled a run (AgentRunProfiler may profile only one run.)" in str(
        raised.value
    )
    assert "pass a new profiler or create a new BaseAgent for each task" in str(raised.value)


class Continuing:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    async def next_turn(self, context):
        return ModelTurnResponse(
            turn=TURN_ADAPTER.validate_python(tool_call("c1", "probe")),
            continuation=ProviderContinuation(provider="p", state={}),
        )

    async def accept_tool_results(self, continuation, results):
        return await self.behaviour()


def continuing_run(behaviour, **options):
    executor = FnExecutor(lambda tool_definition, context: ok({"seen": True}))
    subject = BaseAgent(
        definition(tools=[tool("probe")]),
        Continuing(behaviour),
        tool_executor=executor,
        **options,
    )
    return arun(subject.run(task()))


def test_a_continuation_that_raises_an_sdk_error_keeps_its_code():
    async def refuse():
        raise AgentSdkError("PROVIDER_REFUSED", "the provider refused the continuation")

    result = continuing_run(refuse)
    assert result.status is FAILED
    assert result.reason == (
        "MODEL_TOOL_CONTINUATION_FAILED: the provider refused the continuation"
    )
    assert result.failure.code == "PROVIDER_REFUSED"


def test_a_continuation_that_raises_anything_else_is_named_by_its_exception():
    async def crash():
        raise RuntimeError("socket closed")

    result = continuing_run(crash)
    assert result.status is FAILED
    assert result.reason == "MODEL_TOOL_CONTINUATION_FAILED: socket closed"
    assert result.failure.code == "MODEL_TOOL_CONTINUATION_FAILED"
    assert result.failure.details == {"error_type": "RuntimeError"}


def test_a_continuation_that_hangs_is_stopped_by_the_turn_watchdog():
    async def hang():
        await asyncio.sleep(30)

    result = continuing_run(
        hang, watchdog_policy=AgentWatchdogPolicy(model_turn_timeout_seconds=0.2)
    )
    assert result.status is FAILED
    assert result.failure.code == "WATCHDOG_MODEL_TIMEOUT"
    assert result.reason.startswith(
        "WATCHDOG_MODEL_TIMEOUT: provider tool-result continuation exceeded its 0.2s per-turn "
        "watchdog timeout"
    )


class FailingStateStore(InMemoryProjectStateStore):
    def __init__(self, failing):
        super().__init__()
        self.failing = failing

    def apply(self, project_id, transition, *, summary_max_chars=2_048):
        if transition.kind in self.failing:
            raise RuntimeError("disk full")
        return super().apply(project_id, transition, summary_max_chars=summary_max_chars)


def test_an_accepted_output_that_cannot_be_recorded_in_project_state_fails_the_run():
    store = FailingStateStore({StateTransitionKind.AGENT_RESULT})
    subject, _ = agent([final()], project_state_store=store)
    result = arun(subject.run(task()))
    assert result.status is FAILED
    assert result.reason == (
        "The agent finished and its output was accepted, but the result could not be recorded "
        "in project state: Project state update raised RuntimeError: disk full"
    )
    assert result.failure.code == "PROJECT_STATE_UPDATE_FAILED"


def test_a_tool_outcome_that_cannot_be_recorded_in_project_state_fails_the_run():
    store = FailingStateStore({StateTransitionKind.TOOL_OUTCOME})
    executor = FnExecutor(lambda tool_definition, context: ok({"seen": True}))
    subject, _ = agent(
        [tool_call("c1", "probe"), final()],
        executor=executor,
        definition_=definition(tools=[tool("probe")]),
        project_state_store=store,
    )
    result = arun(subject.run(task()))
    assert result.status is FAILED
    assert result.reason == "Project state update raised RuntimeError: disk full"
    assert result.failure.code == "PROJECT_STATE_UPDATE_FAILED"


def test_a_failure_that_also_cannot_be_recorded_says_so_after_its_own_reason():
    store = FailingStateStore({StateTransitionKind.TOOL_OUTCOME, StateTransitionKind.AGENT_RESULT})
    executor = FnExecutor(lambda tool_definition, context: ok({"seen": True}))
    subject, _ = agent(
        [tool_call("c1", "probe"), final()],
        executor=executor,
        definition_=definition(tools=[tool("probe")]),
        project_state_store=store,
    )
    result = arun(subject.run(task()))
    assert result.status is FAILED
    assert result.reason == (
        "Project state update raised RuntimeError: disk full Additionally, the result could "
        "not be recorded in project state: Project state update raised RuntimeError: disk full"
    )


def test_a_tool_executor_that_raises_an_sdk_error_ends_the_run_with_its_code():
    def broken(tool_definition, context):
        raise AgentSdkError("DRIVER_CRASHED", "the simulator driver crashed")

    subject, _ = agent(
        [tool_call("c1", "probe"), final()],
        executor=FnExecutor(broken),
        definition_=definition(tools=[tool("probe")]),
    )
    result = arun(subject.run(task()))
    assert result.status is FAILED
    assert result.reason == 'Tool "probe" lifecycle failed: the simulator driver crashed'
    assert result.failure.code == "DRIVER_CRASHED"
