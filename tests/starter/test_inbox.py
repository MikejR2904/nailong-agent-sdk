import asyncio

import pytest

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.agent.base_agent.types import (
    MAX_INBOX_MESSAGE_CHARS,
    MAX_INBOX_PENDING,
    MessageInbox,
)
from nailong_agent_sdk.starter import Agent, ModelSetup, tool


def scripted(*turns):
    return ModelSetup(ScriptedModel(list(turns)), "scripted", "scripted-1")


def final(answer="done"):
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def call(name):
    return {"type": "tool-call", "call": {"id": f"c-{name}", "name": name, "arguments": {}}}


def seen(model, index):
    return [(item.kind, item.message) for item in model.calls[index].observations]


def test_messages_are_taken_in_order_and_only_once():
    inbox = MessageInbox()
    inbox.put("  first ")
    inbox.put("second")
    assert inbox.pending == 2 and inbox.drain() == ["first", "second"]
    assert inbox.drain() == [] and inbox.pending == 0


def test_an_empty_oversized_or_excess_message_is_refused_with_the_limit():
    inbox = MessageInbox()
    with pytest.raises(ValueError, match="cannot be empty"):
        inbox.put("   ")
    with pytest.raises(ValueError, match=f"limited to {MAX_INBOX_MESSAGE_CHARS} characters"):
        inbox.put("x" * (MAX_INBOX_MESSAGE_CHARS + 1))
    for number in range(MAX_INBOX_PENDING):
        inbox.put(f"m{number}")
    with pytest.raises(ValueError, match=f"{MAX_INBOX_PENDING} messages are already waiting"):
        inbox.put("one too many")


def test_a_message_sent_before_the_run_reaches_the_model_as_a_user_message(tmp_path):
    setup = scripted(final())
    agent = Agent(model=setup, instructions="Help.", workspace=tmp_path / "w")
    agent.say("Please use the blue theme.")
    answer = asyncio.run(agent.run("Style the page."))
    agent.close()
    assert answer.ok
    assert seen(setup.model, 0) == [("user-message", "Please use the blue theme.")]
    assert "user-message-received" in [event.type for event in answer.result.events]


def test_a_message_sent_while_a_tool_runs_arrives_at_the_next_step(tmp_path):
    holder = {}

    @tool
    def poke() -> str:
        """Do something slow."""
        holder["agent"].say("Actually, skip the tests.")
        return "poked"

    setup = scripted(call("poke"), final())
    agent = Agent(model=setup, instructions="Help.", tools=[poke], workspace=tmp_path / "w")
    holder["agent"] = agent
    answer = asyncio.run(agent.run("Do the job."))
    agent.close()
    assert answer.ok
    assert ("user-message", "Actually, skip the tests.") in seen(setup.model, 1)
    assert seen(setup.model, 0) == []


def test_the_system_prompt_tells_the_model_what_a_user_message_is():
    from nailong_agent_sdk.agent.provider_context import SYSTEM_PROMPT

    assert "observation of kind user-message" in SYSTEM_PROMPT
