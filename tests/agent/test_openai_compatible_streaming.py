import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.openai_compatible import (
    HttpxStreamingJsonTransport,
    OpenAICompatibleAgentModel,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError, TransientProviderError
from tests.support.agents import FnExecutor, arun, definition, ok, task
from tests.support.model_context import model_context
from tests.support.openai_provider import ECHO, endpoint


def test_streaming_roundtrip_deltas_usage_and_tool_calls(server):
    url, state = server
    state["script"] = [
        {
            "sse": [
                {
                    "id": "s1",
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_a",
                                        "function": {"name": "echo", "arguments": ""},
                                    }
                                ],
                            }
                        }
                    ],
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '{"q":'}}]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '"hi"}'}}]
                            }
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
                "data: [DONE]",
            ]
        },
        {
            "sse": [
                {"id": "s2", "choices": [{"delta": {"content": '{"type":"final",'}}]},
                ": keep-alive comment",
                "data: {not json at all",
                {"choices": [{"delta": {"content": '"output":{"status":"complete"}}'}}]},
                {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
                "data: [DONE]",
            ]
        },
    ]
    seen = []

    async def listener(event):
        seen.append(type(event).__name__)

    subject = BaseAgent(
        definition(tools=[ECHO]),
        OpenAICompatibleAgentModel(
            endpoint(url),
            provider="fake",
            model="fake-1",
            streaming_transport=HttpxStreamingJsonTransport(),
        ),
        tool_executor=FnExecutor(lambda t, c: ok(c.call.arguments)),
        on_model_stream=listener,
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    assert (
        seen.count("ModelToolCallDelta") == 3
        and seen.count("ModelTextDelta") == 2
        and seen.count("ModelStreamCompleted") == 2
    )
    assert state["requests"][0]["json"]["stream"] is True and state["requests"][0]["json"][
        "stream_options"
    ] == {"include_usage": True}


def test_streaming_error_chunks_http_errors_and_truncation(server):
    url, state = server
    subject = OpenAICompatibleAgentModel(
        endpoint(url),
        provider="fake",
        model="fake-1",
        streaming_transport=HttpxStreamingJsonTransport(),
    )

    async def listener(event):
        return None

    state["script"] = [
        {
            "sse": [
                {"error": {"message": "context_length_exceeded", "type": "invalid_request_error"}},
                "data: [DONE]",
            ]
        }
    ]
    with pytest.raises(AgentSdkError) as mid_stream:
        arun(subject.stream_turn(model_context(), listener))
    assert (
        "context_length_exceeded" in str(mid_stream.value)
        or "error" in str(mid_stream.value).lower()
    ), str(mid_stream.value)
    state["script"] = [{"status": 429, "body": b"slow", "headers": {"Retry-After": "3"}}]
    with pytest.raises(TransientProviderError) as limited:
        arun(subject.stream_turn(model_context(), listener))
    assert limited.value.retry_after_seconds == 3.0
    state["script"] = [
        {
            "sse": [
                {"choices": [{"delta": {"content": '{"type":"final","output":{"status":"comp'}}]}
            ],
            "truncate_after_sse": True,
        }
    ]
    with pytest.raises(AgentSdkError) as truncated:
        arun(subject.stream_turn(model_context(), listener))
    assert truncated.value.code in {
        "OPENAI_COMPATIBLE_RESPONSE_INVALID",
        "OPENAI_COMPATIBLE_TRANSPORT_ERROR",
    }


def test_a_streaming_redirect_is_refused_like_the_blocking_transports(server):
    url, state = server
    subject = OpenAICompatibleAgentModel(
        endpoint(url),
        provider="fake",
        model="fake-1",
        streaming_transport=HttpxStreamingJsonTransport(),
    )

    async def listener(event):
        return None

    for status in (301, 302, 307, 308):
        state["script"] = [
            {"status": status, "body": b"", "headers": {"Location": "http://elsewhere/"}}
        ]
        with pytest.raises(AgentSdkError) as redirected:
            arun(subject.stream_turn(model_context(), listener))
        assert redirected.value.code == "OPENAI_COMPATIBLE_HTTP_ERROR"
        assert f"HTTP {status}" in str(redirected.value)
        assert "redirects are refused" in str(redirected.value)
        assert not isinstance(redirected.value, TransientProviderError)
