import json

from nailong_agent_sdk.agent.openai_compatible.chat import (
    _assistant_extra,
    _ChatStreamAccumulator,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.agent.test_openai_compatible import fn_call
from tests.support.agents import definition, final
from tests.support.openai_provider import ECHO, chat, run_agent


def tool_turn(**extra):
    reply = chat(tool_calls=[fn_call("call_1", "echo", {"q": "a"})])
    reply["body"]["choices"][0]["message"].update(extra)
    return reply


def test_the_previous_steps_reasoning_and_spoken_text_go_back_with_its_tool_calls(server):
    url, state = server
    state["script"] = [
        tool_turn(
            reasoning_content="Plan: look up the name, then read the best hit.",
            content="Searching for the name first.",
        ),
        chat(content=json.dumps(final())),
    ]
    result = run_agent(url, definition(tools=[ECHO]))
    assert result.status is AgentRunStatus.COMPLETED
    assistant = next(
        m for m in state["requests"][1]["json"]["messages"] if m.get("tool_calls") is not None
    )
    assert assistant["reasoning_content"] == "Plan: look up the name, then read the best hit."
    assert assistant["content"] == "Searching for the name first."


def test_a_providers_other_reasoning_field_names_are_sent_back_as_they_came(server):
    url, state = server
    details = [{"type": "reasoning.text", "text": "think", "index": 0}]
    state["script"] = [
        tool_turn(reasoning="short thought", reasoning_details=details),
        chat(content=json.dumps(final())),
    ]
    run_agent(url, definition(tools=[ECHO]))
    assistant = next(
        m for m in state["requests"][1]["json"]["messages"] if m.get("tool_calls") is not None
    )
    assert assistant["reasoning"] == "short thought" and assistant["reasoning_details"] == details
    assert "reasoning_content" not in assistant


def test_a_provider_that_sends_no_reasoning_gets_the_plain_message_back(server):
    url, state = server
    state["script"] = [tool_turn(), chat(content=json.dumps(final()))]
    run_agent(url, definition(tools=[ECHO]))
    assistant = next(
        m for m in state["requests"][1]["json"]["messages"] if m.get("tool_calls") is not None
    )
    assert set(assistant) == {"role", "content", "tool_calls"} and assistant["content"] is None


def test_think_tags_in_the_spoken_text_are_not_sent_back():
    message = {"content": "<think>hidden plan</think>Now fetching the page."}
    assert _assistant_extra(message) == {"content": "Now fetching the page."}
    assert _assistant_extra({"content": "<think>only thought</think>"}) == {}


def test_streamed_reasoning_is_collected_for_the_next_step():
    accumulator = _ChatStreamAccumulator()
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "step one, "}}]},
        {"choices": [{"delta": {"reasoning_content": "step two"}}]},
        {"choices": [{"delta": {"content": "Looking it up."}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "echo", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        },
    ]
    for chunk in chunks:
        accumulator.consume(chunk)
    message = accumulator.finalize()["choices"][0]["message"]
    assert message["reasoning_content"] == "step one, step two"
    assert message["content"] == "Looking it up." and message["tool_calls"][0]["id"] == "c1"
    assert _assistant_extra(message) == {
        "content": "Looking it up.",
        "reasoning_content": "step one, step two",
    }
