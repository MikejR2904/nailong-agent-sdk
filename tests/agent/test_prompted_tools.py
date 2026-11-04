import json

import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.openai_compatible import OpenAICompatibleAgentModel
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.support.agents import FnExecutor, arun, definition, final, ok, task
from tests.support.model_context import FAKE_KEY
from tests.support.openai_provider import ECHO, chat, endpoint


def run(url, mode, definition_=None):
    model = OpenAICompatibleAgentModel(
        endpoint(url, FAKE_KEY), provider="fake", model="fake-1", tool_mode=mode
    )
    executor = FnExecutor(lambda t, c: ok({"echo": c.call.arguments}))
    agent = BaseAgent(
        definition_ or definition(tools=[ECHO]),
        model,
        require_tool_authorization=False,
        tool_executor=executor,
    )
    return arun(agent.run(task()))


def batch(*calls):
    return json.dumps({"type": "tool-batch", "calls": list(calls)})


def call(name="echo", arguments=None, id="1"):
    return {"id": id, "name": name, "arguments": arguments or {"q": "a"}}


def test_a_model_without_tool_calling_is_given_the_tools_in_the_prompt(server):
    url, state = server
    state["script"] = [chat(content=batch(call())), chat(content=json.dumps(final()))]
    result = run(url, "prompted")
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2
    first = state["requests"][0]["json"]
    assert "tools" not in first and "tool_choice" not in first
    assert first["response_format"] == {"type": "json_object"}
    system = first["messages"][0]["content"]
    assert "no native tool calling" in system and "- echo:" in system and "input_schema=" in system


def test_tool_results_come_back_as_ordinary_messages(server):
    url, state = server
    state["script"] = [
        chat(content=batch(call(arguments={"q": "hello"}))),
        chat(content=json.dumps(final())),
    ]
    run(url, "prompted")
    messages = state["requests"][1]["json"]["messages"]
    assert [m["role"] for m in messages[-2:]] == ["assistant", "user"]
    assert "tool_calls" not in messages[-2] and messages[-2]["content"].startswith('{"type"')
    reply = messages[-1]["content"]
    assert reply.startswith("Tool results: [") and '"tool":"echo"' in reply and "hello" in reply
    assert "another tool call or the final answer" in reply


@pytest.mark.parametrize(
    "text",
    [
        'I will look that up.\n```json\n{"name": "echo", "arguments": {"q": "a"}}\n```',
        '{"type": "tool-call", "call": {"name": "echo", "arguments": "{\\"q\\": \\"a\\"}"}}',
        'Sure! {"calls": [{"name": "echo", "arguments": {"q": "a"}}]} Done.',
    ],
)
def test_the_usual_ways_weak_models_write_a_tool_call_are_understood(server, text):
    url, state = server
    state["script"] = [chat(content=text), chat(content=json.dumps(final()))]
    result = run(url, "prompted")
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 2


def test_call_ids_are_made_unique_so_a_model_that_always_says_1_does_not_collide(server):
    url, state = server
    state["script"] = [
        chat(content=batch(call(id="1"))),
        chat(content=batch(call(id="1", arguments={"q": "b"}))),
        chat(content=json.dumps(final())),
    ]
    result = run(url, "prompted")
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 3


def test_a_tool_call_with_unusable_arguments_is_sent_back_for_another_try(server):
    url, state = server
    bad = '{"type": "tool-call", "call": {"name": "echo", "arguments": "not json"}}'
    state["script"] = [
        chat(content=bad),
        chat(content=batch(call())),
        chat(content=json.dumps(final())),
    ]
    result = run(url, "prompted")
    assert result.status is AgentRunStatus.COMPLETED and result.iterations == 3
    sent = json.dumps(state["requests"][1]["json"]["messages"])
    assert "Your previous reply was rejected" in sent and "not a JSON object" in sent


def test_auto_switches_to_prompted_when_the_server_rejects_tools(server):
    url, state = server
    rejection = {"status": 400, "body": {"error": {"message": "this model does not support tools"}}}
    state["script"] = [rejection, chat(content=batch(call())), chat(content=json.dumps(final()))]
    result = run(url, "auto")
    assert result.status is AgentRunStatus.COMPLETED
    first, second, third = (r["json"] for r in state["requests"])
    assert "tools" in first and "tools" not in second and "tools" not in third


def test_auto_does_not_switch_for_an_unrelated_bad_request(server):
    url, state = server
    state["script"] = [{"status": 400, "body": {"error": {"message": "temperature out of range"}}}]
    result = run(url, "auto")
    assert result.status is AgentRunStatus.FAILED and "temperature out of range" in result.reason
    assert len(state["requests"]) == 1


def test_native_mode_keeps_failing_loudly_when_tools_are_rejected(server):
    url, state = server
    state["script"] = [{"status": 400, "body": {"error": {"message": "tools are not supported"}}}]
    result = run(url, "native")
    assert result.status is AgentRunStatus.FAILED and len(state["requests"]) == 1


def test_auto_drops_json_mode_when_the_server_does_not_know_it(server):
    url, state = server
    message = "invalid params, unknown response_format type 'json_object'"
    state["script"] = [
        {"status": 400, "body": {"error": {"message": message}}},
        chat(content=json.dumps(final())),
    ]
    result = run(url, "auto", definition())
    assert result.status is AgentRunStatus.COMPLETED
    first, second = (r["json"] for r in state["requests"])
    assert "response_format" in first and "response_format" not in second


def test_a_server_that_rejects_json_mode_and_tools_gets_both_adjustments(server):
    url, state = server
    state["script"] = [
        {"status": 400, "body": {"error": {"message": "tools are not supported"}}},
        {"status": 400, "body": {"error": {"message": "unknown response_format type"}}},
        chat(content=batch(call())),
        chat(content=json.dumps(final())),
    ]
    result = run(url, "auto")
    assert result.status is AgentRunStatus.COMPLETED
    sent = [r["json"] for r in state["requests"]]
    assert "tools" in sent[0] and "tools" not in sent[1] and "response_format" in sent[1]
    assert "response_format" not in sent[2]


def test_an_agent_with_no_tools_is_unchanged_in_prompted_mode(server):
    url, state = server
    state["script"] = [chat(content=json.dumps(final()))]
    result = run(url, "prompted", definition())
    assert result.status is AgentRunStatus.COMPLETED
    system = state["requests"][0]["json"]["messages"][0]["content"]
    assert "no native tool calling" not in system


def test_an_unknown_tool_mode_is_named():
    with pytest.raises(ValueError, match='tool_mode must be one of .* not "sometimes"'):
        OpenAICompatibleAgentModel(
            endpoint("http://127.0.0.1:1/v1"), provider="p", model="m", tool_mode="sometimes"
        )
