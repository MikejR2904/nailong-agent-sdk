import json

import pytest

from nailong_agent_sdk.agent.anthropic_messages import (
    AnthropicEndpoint,
    AnthropicMessagesAgentModel,
)
from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from tests.support.agents import FnExecutor, arun, definition, final, ok, task
from tests.support.model_context import FAKE_KEY, model_context
from tests.support.openai_provider import ECHO


def model_for(url, **options):
    endpoint = AnthropicEndpoint(
        api_key=FAKE_KEY,
        base_url=url.removesuffix("/v1"),
        allow_insecure_http=True,
        timeout_seconds=5.0,
    )
    return AnthropicMessagesAgentModel(endpoint, provider="fake", model="fake-1", **options)


def reply(content, stop="end_turn", usage=None):
    body = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "content": content,
        "stop_reason": stop,
    }
    if usage:
        body["usage"] = usage
    return {"status": 200, "body": body}


def text(value):
    return {"type": "text", "text": value}


def tool_use(call_id, name, arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


def run_agent(url, definition_, **options):
    subject = BaseAgent(
        definition_,
        model_for(url, **options),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    return arun(subject.run(task()))


def next_turn(url, parameters=None, **options):
    return arun(model_for(url, **options).next_turn(model_context(parameters)))


def test_request_uses_the_messages_endpoint_and_anthropic_headers(server):
    url, state = server
    state["script"] = [reply([text(json.dumps(final()))])]
    assert run_agent(url, definition()).status is AgentRunStatus.COMPLETED
    request = state["requests"][0]
    headers = {name.lower(): value for name, value in request["headers"].items()}
    payload = request["json"]
    assert request["path"] == "/v1/messages"
    assert headers["x-api-key"] == FAKE_KEY and headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in headers
    assert payload["model"] == "fake-1" and payload["max_tokens"] == 8192
    assert isinstance(payload["system"], str) and "tools" not in payload
    roles = [message["role"] for message in payload["messages"]]
    assert roles == ["user", "user"]
    stable_block = payload["messages"][0]["content"][0]
    assert stable_block["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload["messages"][1]["content"][0]
    assert set(json.loads(stable_block["text"])) == {
        "task_id",
        "task_input",
        "prompt",
        "output_schema",
    }


def test_the_stable_prefix_cache_marker_can_be_turned_off(server):
    url, state = server
    state["script"] = [reply([text(json.dumps(final()))])]
    run_agent(url, definition(), cache_stable_prefix=False)
    assert "cache_control" not in state["requests"][0]["json"]["messages"][0]["content"][0]


def test_a_json_turn_wrapped_in_a_code_fence_is_read(server):
    url, state = server
    fenced = "```json\n" + json.dumps(final()) + "\n```"
    state["script"] = [reply([text(fenced)])]
    assert run_agent(url, definition()).status is AgentRunStatus.COMPLETED


def test_tool_use_roundtrip_returns_the_assistant_blocks_and_matching_results(server):
    url, state = server
    first = [text("I will look."), tool_use("tu_1", "echo", {"q": "a"})]
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 2,
    }
    state["script"] = [reply(first, "tool_use", usage), reply([text(json.dumps(final()))])]
    result = run_agent(url, definition(tools=[ECHO]))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    first_request, second_request = (item["json"] for item in state["requests"])
    assert first_request["tools"][0]["name"] == "echo"
    assert set(first_request["tools"][0]) == {"name", "description", "input_schema"}
    assistant, results = second_request["messages"][-2:]
    assert assistant == {"role": "assistant", "content": first}
    assert results["role"] == "user"
    assert [(block["type"], block["tool_use_id"]) for block in results["content"]] == [
        ("tool_result", "tu_1")
    ]
    assert "is_error" not in results["content"][0]


def test_usage_reports_total_input_and_cached_reads():
    from nailong_agent_sdk.agent.anthropic_messages.messages import _usage

    usage = _usage(
        {
            "id": "msg_9",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        }
    )
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (15, 3, 5)
    assert usage.request_id == "msg_9"


@pytest.mark.parametrize(
    ("stop", "code"),
    [
        ("max_tokens", "ANTHROPIC_MESSAGES_RESPONSE_TRUNCATED"),
        ("model_context_window_exceeded", "ANTHROPIC_MESSAGES_RESPONSE_TRUNCATED"),
        ("refusal", "ANTHROPIC_MESSAGES_RESPONSE_REFUSED"),
        ("pause_turn", "ANTHROPIC_MESSAGES_RESPONSE_INVALID"),
    ],
)
def test_a_stop_reason_that_ends_the_turn_early_is_named(server, stop, code):
    url, state = server
    state["script"] = [reply([text(json.dumps(final()))], stop)]
    with pytest.raises(AgentSdkError) as raised:
        next_turn(url)
    assert raised.value.code == code
    assert stop in str(raised.value)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}, "busy"),
        (reply([]), "non-empty content list"),
        (reply([text("not json")]), "must be a JSON AgentTurn object"),
        (reply([text("[1]")]), "must be a JSON AgentTurn object"),
        (reply([tool_use("tu_1", "echo", "oops")], "tool_use"), "input that is not a JSON object"),
        (reply([{"type": "tool_use", "name": "echo", "input": {}}], "tool_use"), "lacks an id"),
        (reply([text(json.dumps({"type": "bogus"}))]), "AgentTurn contract"),
    ],
)
def test_a_malformed_response_names_what_is_wrong(server, body, fragment):
    url, state = server
    state["script"] = [body if "status" in body else {"status": 200, "body": body}]
    with pytest.raises(AgentSdkError) as raised:
        next_turn(url)
    assert raised.value.code.startswith("ANTHROPIC_MESSAGES_")
    assert fragment in str(raised.value)


def test_overload_and_rate_limit_are_retryable_and_other_failures_are_not(server):
    url, state = server
    for status in (529, 429, 503):
        state["script"] = [{"status": status, "body": {"type": "error", "error": {"message": "x"}}}]
        with pytest.raises(TransientProviderError):
            next_turn(url)
    state["script"] = [
        {"status": 401, "body": {"type": "error", "error": {"message": f"bad key {FAKE_KEY}"}}}
    ]
    with pytest.raises(AgentSdkError) as raised:
        next_turn(url)
    assert not isinstance(raised.value, TransientProviderError)
    assert "HTTP 401" in str(raised.value) and FAKE_KEY not in str(raised.value)


def test_binding_parameters_pass_through_and_cannot_replace_the_system_prompt(server):
    url, state = server
    state["script"] = [reply([text(json.dumps(final()))]), reply([text(json.dumps(final()))])]
    next_turn(url, {"temperature": 0.2, "max_tokens": 321})
    sent = state["requests"][0]["json"]
    assert sent["temperature"] == 0.2 and sent["max_tokens"] == 321
    for forbidden in ("system", "api_key", "tools"):
        with pytest.raises(AgentSdkError) as raised:
            next_turn(url, {forbidden: "x"})
        assert raised.value.code == "MODEL_PARAMETERS_INVALID"


def test_an_adapter_for_another_model_is_refused(server):
    url, _ = server
    endpoint = AnthropicEndpoint(
        api_key=FAKE_KEY, base_url=url.removesuffix("/v1"), allow_insecure_http=True
    )
    other = AnthropicMessagesAgentModel(endpoint, provider="fake", model="other")
    with pytest.raises(AgentSdkError) as raised:
        arun(other.next_turn(model_context()))
    assert raised.value.code == "MODEL_BINDING_MISMATCH"


def test_tool_results_must_match_the_tool_uses_that_were_issued():
    from nailong_agent_sdk.agent.model import ProviderContinuation, ProviderToolResult

    model = model_for("http://127.0.0.1:9")
    continuation = ProviderContinuation(
        "fake", {"assistant_content": [tool_use("tu_1", "echo", {})], "tool_results": []}
    )
    wrong = [ProviderToolResult("tu_2", "echo", "succeeded", "{}", False)]
    with pytest.raises(AgentSdkError) as raised:
        arun(model.accept_tool_results(continuation, wrong))
    assert raised.value.code == "MODEL_CONTINUATION_INVALID"
    failed = [ProviderToolResult("tu_1", "echo", "failed", "boom", False)]
    resolved = arun(model.accept_tool_results(continuation, failed))
    assert resolved.state["tool_results"][0]["is_error"] is True


def test_the_endpoint_refuses_unsafe_or_conflicting_settings():
    with pytest.raises(ValueError, match="HTTPS unless allow_insecure_http"):
        AnthropicEndpoint(api_key=FAKE_KEY, base_url="http://example.com")
    with pytest.raises(ValueError, match="api_key must be non-empty"):
        AnthropicEndpoint(api_key="  ")
    with pytest.raises(ValueError, match="may not set"):
        AnthropicEndpoint(api_key=FAKE_KEY, extra_headers={"X-Api-Key": "other"})
    beta = AnthropicEndpoint(api_key=FAKE_KEY, extra_headers={"anthropic-beta": "feature"})
    assert beta.request_headers()["anthropic-beta"] == "feature"
    assert FAKE_KEY not in repr(beta)
