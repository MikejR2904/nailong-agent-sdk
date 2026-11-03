import json

import pytest

from nailong_agent_sdk.agent.anthropic_messages import AnthropicMessagesAgentModel
from nailong_agent_sdk.agent.openai_compatible import OpenAICompatibleAgentModel
from nailong_agent_sdk.starter import (
    OPENAI_COMPATIBLE_BASE_URLS,
    Agent,
    anthropic_messages,
    openai_chat,
    tool,
)
from tests.support.model_context import FAKE_KEY

ANSWER = {"type": "final", "output": {"status": "complete", "answer": "42"}}


@tool(read_only=True)
def double(value: int) -> int:
    """Double a number."""
    return value * 2


def claude_reply(content, stop="end_turn"):
    body = {"id": "msg_1", "type": "message", "content": content, "stop_reason": stop}
    return {"status": 200, "body": body}


def test_a_claude_agent_calls_a_tool_then_answers_over_http(server, tmp_path):
    url, state = server
    state["script"] = [
        claude_reply(
            [{"type": "tool_use", "id": "tu_1", "name": "double", "input": {"value": 21}}],
            "tool_use",
        ),
        claude_reply([{"type": "text", "text": json.dumps(ANSWER)}]),
    ]
    setup = anthropic_messages(
        api_key=FAKE_KEY, model="claude-test", base_url=url, allow_insecure_http=True
    )
    assert isinstance(setup.model, AnthropicMessagesAgentModel) and setup.provider == "anthropic"
    with Agent(model=setup, instructions="Double it.", tools=[double], workspace=tmp_path) as agent:
        answer = agent.run_sync("Double 21.", input={})
    assert answer.ok and answer.text == "42" and answer.steps == 2
    result_block = state["requests"][1]["json"]["messages"][-1]["content"][0]
    assert result_block["type"] == "tool_result" and "42" in result_block["content"]


def test_a_claude_model_can_fall_back_to_an_openai_compatible_one(server, tmp_path):
    url, state = server
    state["script"] = [
        {"status": 401, "body": {"type": "error", "error": {"message": "bad key"}}},
        {
            "status": 200,
            "body": {
                "id": "c1",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(ANSWER)},
                    }
                ],
            },
        },
    ]
    primary = anthropic_messages(
        api_key=FAKE_KEY, model="claude-test", base_url=url, allow_insecure_http=True
    )
    backup = openai_chat(
        base_url=url + "/v1", api_key=FAKE_KEY, model="other", allow_insecure_http=True
    )
    with Agent(model=primary, fallbacks=[backup], instructions="x", workspace=tmp_path) as agent:
        answer = agent.run_sync("Answer.")
    assert answer.ok and answer.text == "42"
    assert [r["path"] for r in state["requests"]] == ["/v1/messages", "/v1/chat/completions"]


def test_openai_compatible_presets_name_the_verified_base_urls():
    assert OPENAI_COMPATIBLE_BASE_URLS["openrouter"] == "https://openrouter.ai/api/v1"
    assert OPENAI_COMPATIBLE_BASE_URLS["xai"] == "https://api.x.ai/v1"
    assert OPENAI_COMPATIBLE_BASE_URLS["gemini"].startswith("https://generativelanguage.")
    for name, base_url in OPENAI_COMPATIBLE_BASE_URLS.items():
        setup = openai_chat(
            base_url=base_url,
            api_key=FAKE_KEY,
            model=f"{name}-model",
            allow_insecure_http=base_url.startswith("http://"),
        )
        assert isinstance(setup.model, OpenAICompatibleAgentModel)


def test_an_unsafe_claude_endpoint_is_refused_before_any_request():
    with pytest.raises(ValueError, match="HTTPS unless allow_insecure_http"):
        anthropic_messages(api_key=FAKE_KEY, model="m", base_url="http://example.com")


def test_agent_stream_yields_text_deltas_then_the_answer(server, tmp_path):
    import asyncio

    from nailong_agent_sdk.agent.model import ModelTextDelta

    url, state = server
    body = json.dumps(ANSWER)
    half = len(body) // 2
    state["script"] = [
        {
            "sse": [
                {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 5}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": body[:half]},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": body[half:]},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 9},
                },
                {"type": "message_stop"},
            ]
        }
    ]
    setup = anthropic_messages(
        api_key=FAKE_KEY, model="claude-test", base_url=url, allow_insecure_http=True
    )
    heard = []

    async def collect(agent):
        return [item async for item in agent.stream("Answer.")]

    with Agent(model=setup, instructions="x", workspace=tmp_path, on_stream=heard.append) as agent:
        items = asyncio.run(collect(agent))
    assert "".join(i.text for i in items if isinstance(i, ModelTextDelta)) == body
    assert items[-1].ok and items[-1].text == "42"
    assert any(isinstance(i, ModelTextDelta) for i in heard)
