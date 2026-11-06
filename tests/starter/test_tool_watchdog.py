import asyncio
import threading
import time

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.foundations.detached import abandoned_calls
from nailong_agent_sdk.starter import Agent, ModelSetup, tool


def scripted(*turns):
    return ModelSetup(ScriptedModel(list(turns)), "scripted", "scripted-1")


def call(name, ident):
    return {"type": "tool-call", "call": {"id": ident, "name": name, "arguments": {}}}


def final():
    return {"type": "final", "output": {"status": "complete", "answer": "done"}}


def test_a_timed_out_tool_that_cannot_be_stopped_is_named_and_blocks_the_next_tool(tmp_path):
    release = threading.Event()
    ran = []

    @tool
    def slow() -> str:
        """Block until released."""
        release.wait(10)
        ran.append("slow finished")
        return "late"

    @tool
    def after() -> str:
        """Run afterwards."""
        ran.append("after ran")
        return "ok"

    setup = scripted(call("slow", "c1"), call("after", "c2"), final())
    agent = Agent(
        model=setup,
        instructions="Help.",
        tools=[slow, after],
        workspace=tmp_path / "w",
        tool_timeout_seconds=0.3,
    )
    try:
        answer = asyncio.run(agent.run("Go"))
        assert not answer.ok
        assert "slow" in answer.reason and "still running in the background" in answer.reason
        assert abandoned_calls() == ["slow"]
    finally:
        release.set()
        agent.close()
    deadline = time.monotonic() + 5
    while abandoned_calls() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert abandoned_calls() == [] and "after ran" not in ran


def test_a_tool_started_while_an_abandoned_one_runs_is_refused_with_the_reason(tmp_path):
    release = threading.Event()

    @tool
    def slow() -> str:
        """Block until released."""
        release.wait(10)
        return "late"

    @tool
    def after() -> str:
        """Run afterwards."""
        return "ok"

    async def scenario():
        try:
            await asyncio.wait_for(slow.run({}), 0.2)
        except TimeoutError:
            pass
        return await after.run({})

    try:
        result = asyncio.run(scenario())
    finally:
        release.set()
    assert result.status == "failed"
    assert 'Tool "after" was not started' in result.error and "slow" in result.error
