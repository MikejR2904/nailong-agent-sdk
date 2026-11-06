import asyncio
import threading
import time

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import Agent, CancelToken, ModelSetup


def setup():
    turn = {"type": "final", "output": {"status": "complete", "answer": "done"}}
    return ModelSetup(ScriptedModel([turn]), "scripted", "scripted-1")


def resume_after(agent, seconds):
    threading.Timer(seconds, agent.pause.resume).start()


def test_a_paused_run_waits_where_it_is_and_carries_on_when_resumed(tmp_path):
    agent = Agent(model=setup(), instructions="Help.", workspace=tmp_path / "w")
    agent.pause.pause()
    resume_after(agent, 0.6)
    started = time.monotonic()
    answer = asyncio.run(agent.run("Go"))
    agent.close()
    assert answer.ok and time.monotonic() - started >= 0.55
    types = [event.type for event in answer.result.events]
    assert types.index("run-paused") < types.index("run-resumed")


def test_time_spent_paused_does_not_count_against_the_run_deadline(tmp_path):
    agent = Agent(
        model=setup(), instructions="Help.", workspace=tmp_path / "w", deadline_seconds=1.0
    )
    agent.pause.pause()
    resume_after(agent, 1.6)
    answer = asyncio.run(agent.run("Go"))
    agent.close()
    assert answer.ok, answer.reason


def test_stopping_a_paused_run_ends_it_instead_of_waiting_for_a_resume(tmp_path):
    agent = Agent(model=setup(), instructions="Help.", workspace=tmp_path / "w")
    agent.pause.pause()
    token = CancelToken()
    threading.Timer(0.4, token.cancel).start()
    started = time.monotonic()
    answer = asyncio.run(agent.run("Go", cancellation=token))
    agent.close()
    assert answer.status == "cancelled" and time.monotonic() - started < 5


def test_pausing_twice_or_resuming_when_not_paused_changes_nothing(tmp_path):
    agent = Agent(model=setup(), instructions="Help.", workspace=tmp_path / "w")
    assert agent.pause.pause() is True and agent.pause.pause() is False
    assert agent.pause.resume() is True and agent.pause.resume() is False
    agent.close()
