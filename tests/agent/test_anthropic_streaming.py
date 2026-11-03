import json

import pytest

from nailong_agent_sdk.agent.anthropic_messages import (
    AnthropicEndpoint,
    AnthropicMessagesAgentModel,
)
from nailong_agent_sdk.agent.model import (
    ModelStreamCompleted,
    ModelTextDelta,
    ModelToolCallDelta,
    StreamingAgentModel,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from tests.support.agents import arun, final
from tests.support.model_context import FAKE_KEY, model_context


def model_for(url):
    endpoint = AnthropicEndpoint(
        api_key=FAKE_KEY, base_url=url.removesuffix("/v1"), allow_insecure_http=True
    )
    return AnthropicMessagesAgentModel(endpoint, provider="fake", model="fake-1")


def start(usage=None):
    usage = usage or {"input_tokens": 10, "cache_read_input_tokens": 3, "output_tokens": 1}
    return {"type": "message_start", "message": {"id": "msg_s", "usage": usage}}


def block_start(index, block):
    return {"type": "content_block_start", "index": index, "content_block": block}


def delta(index, **fields):
    return {"type": "content_block_delta", "index": index, "delta": fields}


def stop(index):
    return {"type": "content_block_stop", "index": index}


def end(reason="end_turn", output_tokens=20):
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


def stream_turn(url):
    events = []
    model = model_for(url)
    assert isinstance(model, StreamingAgentModel)
    response = arun(model.stream_turn(model_context(), events.append))
    return response, events


def test_text_deltas_stream_and_the_final_turn_and_usage_are_assembled(server):
    url, state = server
    body = json.dumps(final())
    third = len(body) // 3
    state["script"] = [
        {
            "sse": [
                start(),
                {"type": "ping"},
                block_start(0, {"type": "text", "text": ""}),
                delta(0, type="text_delta", text=body[:third]),
                delta(0, type="text_delta", text=body[third : 2 * third]),
                delta(0, type="text_delta", text=body[2 * third :]),
                stop(0),
                *end(),
            ]
        }
    ]
    response, events = stream_turn(url)
    assert response.turn.type == "final"
    assert "".join(e.text for e in events if isinstance(e, ModelTextDelta)) == body
    assert isinstance(events[-1], ModelStreamCompleted)
    assert (response.usage.input_tokens, response.usage.cached_input_tokens) == (13, 3)
    assert response.usage.output_tokens == 20 and response.usage.request_id == "msg_s"
    sent = state["requests"][0]["json"]
    assert sent["stream"] is True and state["requests"][0]["path"] == "/v1/messages"


def test_streamed_tool_use_keeps_thinking_blocks_and_rebuilds_the_input(server):
    url, state = server
    state["script"] = [
        {
            "sse": [
                start(),
                block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
                delta(0, type="thinking_delta", thinking="let me "),
                delta(0, type="thinking_delta", thinking="check"),
                delta(0, type="signature_delta", signature="sig-123"),
                stop(0),
                block_start(1, {"type": "tool_use", "id": "tu_1", "name": "echo", "input": {}}),
                delta(1, type="input_json_delta", partial_json='{"q":'),
                delta(1, type="input_json_delta", partial_json='"hi"}'),
                stop(1),
                *end("tool_use"),
            ]
        }
    ]
    response, events = stream_turn(url)
    assert response.turn.type == "tool-batch"
    assert response.turn.calls[0].arguments == {"q": "hi"}
    content = response.continuation.state["assistant_content"]
    assert content[0] == {"type": "thinking", "thinking": "let me check", "signature": "sig-123"}
    assert content[1]["input"] == {"q": "hi"}
    deltas = [e for e in events if isinstance(e, ModelToolCallDelta)]
    assert (deltas[0].call_id, deltas[0].name, deltas[0].index) == ("tu_1", "echo", 0)
    assert "".join(d.arguments_delta for d in deltas) == '{"q":"hi"}'


@pytest.mark.parametrize(
    ("script", "code", "fragment"),
    [
        (
            [start(), {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}],
            "ANTHROPIC_MESSAGES_PROVIDER_ERROR",
            "busy",
        ),
        (
            [start(), block_start(0, {"type": "text", "text": ""})],
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "before the provider sent message_stop",
        ),
        (
            [
                start(),
                block_start(1, {"type": "tool_use", "id": "t", "name": "echo", "input": {}}),
                delta(1, type="input_json_delta", partial_json="{broken"),
                stop(1),
                *end("tool_use"),
            ],
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "not valid JSON",
        ),
        (
            [start(), delta(5, type="text_delta", text="x")],
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "never started",
        ),
        (
            [
                start(),
                block_start(0, {"type": "text", "text": ""}),
                delta(0, type="text_delta", text="{}"),
                stop(0),
                *end("max_tokens"),
            ],
            "ANTHROPIC_MESSAGES_RESPONSE_TRUNCATED",
            "max_tokens",
        ),
    ],
)
def test_a_bad_stream_is_named_not_guessed_at(server, script, code, fragment):
    url, state = server
    state["script"] = [{"sse": script}]
    with pytest.raises(AgentSdkError) as raised:
        stream_turn(url)
    assert raised.value.code == code and fragment in str(raised.value)


def test_a_529_before_the_stream_starts_is_retryable(server):
    url, state = server
    state["script"] = [{"status": 529, "body": {"type": "error", "error": {"message": "busy"}}}]
    with pytest.raises(TransientProviderError):
        stream_turn(url)
