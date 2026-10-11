import importlib

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.model import ModelContext, ModelTurnResponse, ProviderUsage
from nailong_agent_sdk.agent.openai_compatible import OpenAICompatibleEndpoint
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.telemetry_models import TelemetryContext
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.trace_context import (
    is_span_id,
    is_trace_id,
    parse_traceparent,
)
from nailong_agent_sdk.tools.tools import ToolInvocationContext
from tests.support.agents import (
    TURN_ADAPTER,
    DynamicModel,
    FnExecutor,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)
from tests.support.agents import agent as scripted_agent
from tests.support.chat_provider import ScriptedChatTransport, final_chat

TRACE = "0af7651916cd43dd8448eb211c80319c"
PARENT = "b7ad6b7169203331"
HEADER = f"00-{TRACE}-{PARENT}-01"
ECHO = tool("echo")
KEY = "sk-live-0123456789abcdef"
ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": KEY, "allow_insecure_http": True}


class UsageModel:
    def __init__(self, usage):
        self.usage = usage

    async def next_turn(self, context):
        return ModelTurnResponse(turn=TURN_ADAPTER.validate_python(final()), usage=self.usage)


def echo_executor():
    return FnExecutor(lambda tool_definition, context: ok({"echo": context.call.arguments}))


def run_agent(tmp_path, turns=None, *, context=None, model=None, task_id="trace-1"):
    store = TelemetryStore(tmp_path)
    executor = echo_executor()
    try:
        if model is None:
            subject, _ = scripted_agent(
                turns,
                executor=executor,
                definition_=definition(tools=[ECHO], max_iterations=6),
                telemetry=store,
                telemetry_context=context,
            )
        else:
            subject = BaseAgent(
                definition(tools=[ECHO], max_iterations=6),
                model,
                tool_executor=executor,
                telemetry=store,
                telemetry_context=context,
            )
        result = arun(subject.run(task(task_id)))
        return result, store.list_events(task_id, limit=500), executor
    finally:
        store.close()


def spans_of(result, kind):
    return [span for span in result.profile["spans"] if span["kind"] == kind]


def root_of(result):
    (root,) = spans_of(result, "run")
    return root


def test_every_event_of_a_run_carries_one_generated_trace_and_the_root_span(tmp_path):
    result, events, _ = run_agent(tmp_path, [tool_call("c1", "echo", {"q": 1}), final()])
    assert result.status is AgentRunStatus.COMPLETED
    trace_id = result.profile["trace_id"]
    root = root_of(result)
    assert is_trace_id(trace_id) and is_span_id(root["span_id"])
    assert root["parent_span_id"] is None
    assert len(events) > 5
    for event in events:
        assert event.context.trace_id == trace_id
        assert event.context.span_id == root["span_id"]
        assert event.context.parent_span_id is None


def test_each_run_generates_its_own_trace(tmp_path):
    first, _, _ = run_agent(tmp_path / "a", [final()], task_id="a")
    second, _, _ = run_agent(tmp_path / "b", [final()], task_id="b")
    assert first.profile["trace_id"] != second.profile["trace_id"]


def test_the_profile_event_persists_the_trace_resource_and_every_span(tmp_path):
    result, events, _ = run_agent(tmp_path, [tool_call("c1", "echo"), final()])
    (completed,) = [event for event in events if event.event_type == "agent.profile-completed"]
    payload = completed.payload
    profile = result.profile
    assert payload["trace_id"] == profile["trace_id"]
    assert payload["integrity_hash"] == profile["integrity_hash"]
    assert payload["span_count"] == len(profile["spans"]) == len(payload["spans"])
    assert payload["spans"] == profile["spans"]
    assert payload["resource"]["service.name"]
    assert payload["resource"]["telemetry.sdk.language"] == "python"
    root = root_of(result)
    children = [span for span in payload["spans"] if span["kind"] != "run"]
    assert {span["kind"] for span in children} == {"context-projection", "model-turn", "tool"}
    assert all(is_span_id(span["span_id"]) for span in payload["spans"])
    assert all(span["parent_span_id"] == root["span_id"] for span in children)


def test_the_profile_event_keeps_the_most_recent_spans_and_always_the_root(tmp_path, monkeypatch):
    module = importlib.import_module("nailong_agent_sdk.agent.base_agent.agent")
    monkeypatch.setattr(module, "_PROFILE_EVENT_SPAN_LIMIT", 3)
    result, events, _ = run_agent(
        tmp_path, [tool_call("c1", "echo"), tool_call("c2", "echo"), final()]
    )
    (completed,) = [event for event in events if event.event_type == "agent.profile-completed"]
    total = len(result.profile["spans"])
    assert total > 3
    assert completed.payload["span_count"] == total
    assert completed.payload["spans"] == result.profile["spans"][-3:]
    assert completed.payload["spans"][-1]["kind"] == "run"


def test_a_supplied_trace_is_continued_under_the_callers_span(tmp_path):
    context = TelemetryContext(
        run_id="trace-1",
        task_id="trace-1",
        agent_id="audit-agent",
        trace_id=TRACE,
        span_id=PARENT,
    )
    result, events, _ = run_agent(tmp_path, [final()], context=context)
    root = root_of(result)
    assert result.profile["trace_id"] == TRACE
    assert root["parent_span_id"] == PARENT
    assert root["span_id"] != PARENT
    for event in events:
        assert event.context.trace_id == TRACE
        assert event.context.parent_span_id == PARENT
        assert event.context.span_id == root["span_id"]


def test_the_callers_own_context_object_is_left_unchanged(tmp_path):
    context = TelemetryContext(run_id="trace-1", trace_id=TRACE)
    run_agent(tmp_path, [final()], context=context)
    assert context.trace_id == TRACE and context.span_id is None and context.parent_span_id is None


def test_the_model_and_tools_receive_a_traceparent_naming_their_own_span(tmp_path):
    model = DynamicModel(
        lambda context: final() if context.iteration == 2 else tool_call("c1", "echo")
    )
    result, _, executor = run_agent(tmp_path, model=model)
    spans = {span["span_id"]: span for span in result.profile["spans"]}
    received = [parse_traceparent(context.trace_parent) for context in model.calls]
    assert len(received) == 2
    for parent in received:
        assert parent.trace_id == result.profile["trace_id"] and parent.sampled is True
        assert spans[parent.parent_span_id]["kind"] == "model-turn"
    assert received[0].parent_span_id != received[1].parent_span_id
    (invocation,) = executor.calls
    tool_parent = parse_traceparent(invocation.trace_parent)
    assert tool_parent.trace_id == result.profile["trace_id"]
    assert spans[tool_parent.parent_span_id]["kind"] == "tool"
    assert spans[tool_parent.parent_span_id]["attributes"]["gen_ai.tool.call.id"] == "c1"


def test_model_turn_spans_carry_the_request_and_the_reported_usage(tmp_path):
    usage = ProviderUsage(input_tokens=120, output_tokens=30, request_id="req-1")
    result, _, _ = run_agent(tmp_path, model=UsageModel(usage))
    (model_span,) = spans_of(result, "model-turn")
    attributes = model_span["attributes"]
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.provider.name"] == "fake"
    assert attributes["gen_ai.request.model"] == "fake-1"
    assert attributes["gen_ai.usage.input_tokens"] == 120
    assert attributes["gen_ai.usage.output_tokens"] == 30
    assert attributes["gen_ai.response.id"] == "req-1"
    assert attributes["iteration"] == 1


def test_unreported_usage_counters_are_left_off_the_span_not_zeroed(tmp_path):
    result, _, _ = run_agent(tmp_path / "partial", model=UsageModel(ProviderUsage(input_tokens=5)))
    (partial,) = spans_of(result, "model-turn")
    assert partial["attributes"]["gen_ai.usage.input_tokens"] == 5
    assert "gen_ai.usage.output_tokens" not in partial["attributes"]
    assert "gen_ai.response.id" not in partial["attributes"]
    bare, _, _ = run_agent(tmp_path / "bare", [final()])
    (plain,) = spans_of(bare, "model-turn")
    assert not [key for key in plain["attributes"] if key.startswith("gen_ai.usage")]


def test_tool_and_run_spans_use_the_standard_operation_names(tmp_path):
    result, _, _ = run_agent(tmp_path, [tool_call("c1", "echo"), final()])
    (tool_span,) = spans_of(result, "tool")
    assert tool_span["attributes"]["gen_ai.operation.name"] == "execute_tool"
    assert tool_span["attributes"]["gen_ai.tool.name"] == "echo"
    assert tool_span["attributes"]["gen_ai.tool.call.id"] == "c1"
    assert tool_span["attributes"]["tool_call_id"] == "c1"
    attributes = root_of(result)["attributes"]
    assert attributes["gen_ai.operation.name"] == "invoke_agent"
    assert attributes["gen_ai.agent.name"] == "audit-agent"
    assert attributes["agent_status"] == "completed"


def test_an_oversized_tool_call_id_is_truncated_in_the_span_and_does_not_fail_the_run(tmp_path):
    result, _, _ = run_agent(tmp_path, [tool_call("c" * 6_000, "echo"), final()])
    assert result.status is AgentRunStatus.COMPLETED
    (tool_span,) = spans_of(result, "tool")
    shown = tool_span["attributes"]["gen_ai.tool.call.id"]
    assert len(shown) == 256 and shown.endswith("...") and shown.startswith("ccc")
    assert tool_span["attributes"]["tool_call_id"] == shown


def test_the_tool_invocation_context_and_model_context_default_to_no_trace_parent():
    assert ModelContext.__dataclass_fields__["trace_parent"].default is None
    assert ToolInvocationContext.__dataclass_fields__["trace_parent"].default is None


def test_runtime_services_hand_the_context_to_the_agent_they_build(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    try:
        context = TelemetryContext(run_id="trace-1", trace_id=TRACE)
        built = services.create_agent(
            definition(), DynamicModel(lambda _: final()), telemetry_context=context
        )
        assert built.telemetry_context is context
        assert (
            services.create_agent(definition(), DynamicModel(lambda _: final())).telemetry_context
            is None
        )
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def options(**changes):
    return TaskRunOptions.model_validate({"model_endpoint": ENDPOINT, **changes})


def runner(tmp_path, bodies):
    services = AgentRuntimeServices.open(tmp_path / "service")
    transport = ScriptedChatTransport(bodies)
    return AgentTaskRunner(services, transport=transport), services, transport


def test_the_runner_continues_a_trace_the_caller_names(tmp_path):
    subject, services, _ = runner(tmp_path, [final_chat()])
    result = arun(subject.run(definition(), task(), options(traceparent=HEADER)))
    events = services.telemetry.list_events("t1", limit=500)
    assert result.profile["trace_id"] == TRACE
    assert root_of(result)["parent_span_id"] == PARENT
    assert {event.context.trace_id for event in events} == {TRACE}
    created = next(event for event in events if event.event_type == "run.created")
    assert created.context.span_id == PARENT
    agent_events = [event for event in events if event.event_type.startswith("agent.")]
    assert {event.context.span_id for event in agent_events} == {root_of(result)["span_id"]}


def test_without_a_traceparent_the_runner_gives_the_whole_run_one_trace(tmp_path):
    subject, services, _ = runner(tmp_path, [final_chat()])
    result = arun(subject.run(definition(), task(), options()))
    events = services.telemetry.list_events("t1", limit=500)
    traces = {event.context.trace_id for event in events}
    assert traces == {result.profile["trace_id"]} and is_trace_id(next(iter(traces)))
    assert {"run.created", "run.completed"} <= {event.event_type for event in events}
    assert root_of(result)["parent_span_id"] is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-header", "four fields"),
        (f"00-{'0' * 32}-{PARENT}-01", "trace_id must be 32 lowercase hex characters"),
        (f"00-{TRACE}-{PARENT}-xx", "flags must be 2 lowercase hex characters"),
    ],
)
def test_a_malformed_traceparent_is_refused_naming_the_part(value, message):
    with pytest.raises(ValidationError) as raised:
        options(traceparent=value)
    (issue,) = raised.value.errors()
    assert issue["loc"] == ("traceparent",)
    assert message in issue["msg"]


def test_the_endpoint_sends_traceparent_to_the_provider_only_when_the_caller_opts_in(tmp_path):
    subject, _, transport = runner(tmp_path / "off", [final_chat()])
    arun(subject.run(definition(), task(), options()))
    assert "traceparent" not in {name.lower() for name in transport.requests[0]["headers"]}

    subject, _, transport = runner(tmp_path / "on", [final_chat()])
    result = arun(
        subject.run(
            definition(),
            task(),
            options(model_endpoint={**ENDPOINT, "propagate_trace_context": True}),
        )
    )
    sent = parse_traceparent(transport.requests[0]["headers"]["traceparent"])
    (model_span,) = spans_of(result, "model-turn")
    assert sent.trace_id == result.profile["trace_id"]
    assert sent.parent_span_id == model_span["span_id"]


def test_endpoint_request_headers_add_traceparent_only_with_both_the_opt_in_and_a_value():
    plain = OpenAICompatibleEndpoint(base_url="https://example.test/v1", api_key=KEY)
    enabled = OpenAICompatibleEndpoint(
        base_url="https://example.test/v1", api_key=KEY, propagate_trace_context=True
    )
    assert plain.request_headers(HEADER) == plain.headers
    assert enabled.request_headers(None) == enabled.headers
    assert enabled.request_headers("") == enabled.headers
    assert enabled.request_headers(HEADER) == {**enabled.headers, "traceparent": HEADER}
    assert "traceparent" not in enabled.headers
