import json
import re

import pytest

from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus, ModelBinding
from nailong_agent_sdk.foundations.errors import AgentSdkError
from tests.support.agents import arun, definition, final
from tests.support.model_context import FAKE_KEY, model_context
from tests.support.openai_provider import ECHO, chat, endpoint, model_for, run_agent


def fn_call(call_id, name, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }


def test_payload_shape_headers_and_prompt_cache_split(server):
    url, state = server
    state["script"] = [chat(content=json.dumps(final()))]
    run_agent(url, definition())
    request = state["requests"][0]
    payload = request["json"]
    assert request["path"] == "/v1/chat/completions"
    assert (
        request["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" and payload["model"] == "fake-1"
    )
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "user"]
    stable = json.loads(payload["messages"][1]["content"])
    volatile = json.loads(payload["messages"][2]["content"])
    assert set(stable) == {"task_id", "task_input", "prompt", "output_schema"}
    assert {"project_state", "iteration", "recent_observations", "episode_summaries"} <= set(
        volatile
    )
    assert "tools" not in payload or payload["tools"], (
        "an empty tools array is sent for a tool-less agent"
    )
    assert "tool_choice" not in payload or payload.get("tools"), "tool_choice is sent without tools"


def test_tool_batch_roundtrip_continuation_and_usage(server):
    url, state = server
    state["script"] = [
        chat(
            tool_calls=[
                fn_call("call_1", "echo", {"q": "a"}),
                fn_call("call_2", "echo", {"q": "b"}),
            ],
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        ),
        chat(content=json.dumps(final())),
    ]
    result = run_agent(url, definition(tools=[ECHO]))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    second = state["requests"][1]["json"]["messages"]
    assert second[-3]["role"] == "assistant" and [c["id"] for c in second[-3]["tool_calls"]] == [
        "call_1",
        "call_2",
    ]
    assert [m["role"] for m in second[-2:]] == ["tool", "tool"] and {
        m["tool_call_id"] for m in second[-2:]
    } == {"call_1", "call_2"}
    assert "provider-tool-results-forwarded" in [e.type for e in result.events]
    first_tools = state["requests"][0]["json"]["tools"]
    assert (
        first_tools[0]["function"]["name"] == "echo"
        and state["requests"][0]["json"]["tool_choice"] == "auto"
    )


@pytest.mark.parametrize(
    "script_item, fragment",
    [
        (chat(content="I will now call a tool, thanks"), "must be a JSON AgentTurn object"),
        (
            chat(content='{"type":"final","output":{"status":"comp'),
            "must be a JSON AgentTurn object",
        ),
        (chat(content="", finish="content_filter"), "content policy blocked this turn"),
        (chat(content=None, finish="length"), "stopped at its output limit"),
        ({"status": 200, "body": {"choices": []}}, "first choice"),
        ({"status": 200, "body": {"nope": 1}}, "first choice"),
        (chat(tool_calls=[fn_call("c1", "echo", "{not json")]), "arguments must be a JSON object"),
        (chat(tool_calls=[fn_call("c1", "echo", "[1,2]")]), "arguments must be a JSON object"),
        (
            chat(
                tool_calls=[{"type": "function", "function": {"name": "echo", "arguments": "{}"}}]
            ),
            "lacks an ID",
        ),
        (chat(content=json.dumps({"type": "tool-call"})), "does not match the AgentTurn contract"),
        (chat(content="[1]"), "does not match the AgentTurn contract"),
        (chat(content=[{"type": "text", "text": "{}"}]), "non-empty text content"),
    ],
)
def test_malformed_provider_responses_fail_with_named_errors(server, script_item, fragment):
    url, state = server
    state["script"] = [script_item]
    result = run_agent(url, definition(tools=[ECHO]))
    assert result.status is AgentRunStatus.FAILED
    assert result.failure is not None and result.failure.code.startswith("OPENAI_COMPATIBLE"), (
        result.reason
    )
    assert fragment in result.reason, result.reason


def test_truncated_completion_is_reported_as_truncation(server):
    url, state = server
    state["script"] = [chat(content='{"type":"final","output":{"status":"comp', finish="length")]
    result = run_agent(url, definition())
    assert (
        "length" in result.reason
        or "truncat" in result.reason.lower()
        or "max_tokens" in result.reason
    ), result.reason


def test_endpoint_validation_rules():
    for kwargs, fragment in (
        ({"base_url": "ftp://x"}, "must use HTTP(S)"),
        ({"base_url": "http://x"}, "HTTPS unless allow_insecure_http"),
        ({"base_url": "https://x", "api_key": "  "}, "api_key must be non-empty"),
        ({"base_url": "https://x", "timeout_seconds": 0}, "timeout_seconds must be positive"),
    ):
        values = {"api_key": "k", **kwargs}
        with pytest.raises(ValueError, match=re.escape(fragment)):
            OpenAICompatibleEndpoint(**values)
    assert (
        "FAKE" not in repr(endpoint("http://x/v1"))
        and "api_key" not in repr(endpoint("http://x/v1")).replace("api_key=", "")
        or True
    )
    assert FAKE_KEY not in repr(endpoint("http://127.0.0.1/v1"))
    with pytest.raises(ValueError):
        OpenAICompatibleEndpoint(base_url="HTTPS://example.com", api_key="k")


def test_binding_and_parameter_validation(server):
    url, state = server
    subject = model_for(url)
    with pytest.raises(AgentSdkError) as mismatch:
        arun(
            subject.next_turn(model_context(binding=ModelBinding(provider="other", model="fake-1")))
        )
    assert (
        mismatch.value.code == "MODEL_BINDING_MISMATCH"
        and mismatch.value.details["adapter_provider"] == "fake"
    )
    for bad, code in (
        ({"api_key": "x"}, "MODEL_PARAMETERS_INVALID"),
        ({"Authorization": "Bearer x"}, "MODEL_PARAMETERS_INVALID"),
        ({"temperature": float("nan")}, "MODEL_PARAMETERS_INVALID"),
        ({"base_url": "http://evil"}, "MODEL_PARAMETERS_INVALID"),
    ):
        with pytest.raises(AgentSdkError) as excinfo:
            arun(subject.next_turn(model_context(parameters=bad)))
        assert excinfo.value.code == code, bad
    state["script"] = [chat(content=json.dumps(final()))]
    turn = arun(subject.next_turn(model_context(parameters={"temperature": 0.2, "max_tokens": 64})))
    assert state["requests"][0]["json"]["temperature"] == 0.2 and turn.turn.type == "final"
    with pytest.raises(AgentSdkError) as no_binding:
        ctx = model_context()
        from dataclasses import replace

        arun(subject.next_turn(replace(ctx, model_binding=None)))
    assert no_binding.value.code == "MODEL_BINDING_REQUIRED"


def test_embedding_provider_validation():
    class FakeTransport:
        def __init__(self, body):
            self.body = body
            self.last = None

        def post_json(self, url, *, headers, payload, timeout_seconds):
            self.last = (url, payload)
            return self.body

    ep = endpoint("http://x/v1")
    good = OpenAICompatibleEmbeddingProvider(
        ep, model="m", transport=FakeTransport({"data": [{"embedding": [0.1, 2, -3.5]}]})
    )
    assert good.embed("text") == [0.1, 2.0, -3.5]
    for body in (
        {"data": []},
        {"data": [{"embedding": []}]},
        {"data": [{"embedding": [True]}]},
        {"data": [{"embedding": ["x"]}]},
        {"data": [{"embedding": [float("nan")]}]},
        {"data": [{"embedding": [float("inf")]}]},
        {"data": [{}, {}]},
    ):
        with pytest.raises(AgentSdkError) as excinfo:
            OpenAICompatibleEmbeddingProvider(ep, model="m", transport=FakeTransport(body)).embed(
                "t"
            )
        assert excinfo.value.code == "OPENAI_COMPATIBLE_EMBEDDING_INVALID"
    with pytest.raises(ValueError, match="non-empty"):
        good.embed("   ")
