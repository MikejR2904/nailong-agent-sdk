from dataclasses import replace

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.foundations.errors import InvalidModelOutputError
from nailong_agent_sdk.starter import Agent, ModelSetup


class Flaky:
    def __init__(self, failures, then):
        self.failures = failures
        self.inner = ScriptedModel([then])
        self.calls = []

    async def next_turn(self, context):
        self.calls.append(context)
        if len(self.calls) <= self.failures:
            raise InvalidModelOutputError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID", "Chat response message must contain text."
            )
        return await self.inner.next_turn(replace(context, iteration=1))


def run(tmp_path, flaky, **options):
    agent = Agent(
        model=ModelSetup(flaky, "scripted", "scripted-1"),
        instructions="Answer briefly.",
        workspace=tmp_path / "workspace",
        max_steps=6,
        **options,
    )
    return agent.run_sync("Say done.")


FINAL = {"type": "final", "output": {"status": "complete", "answer": "done"}}


def test_an_unusable_reply_is_shown_to_the_model_and_the_next_reply_is_accepted(tmp_path):
    flaky = Flaky(2, FINAL)
    answer = run(tmp_path, flaky)
    assert answer.ok and answer.text == "done" and len(flaky.calls) == 3
    shown = " ".join(str(item) for item in flaky.calls[-1].observations)
    assert "Your previous reply was rejected (OPENAI_COMPATIBLE_RESPONSE_INVALID)" in shown
    assert "must contain text" in shown


def test_too_many_unusable_replies_in_a_row_fail_the_run_with_the_count(tmp_path):
    flaky = Flaky(10, FINAL)
    answer = run(tmp_path, flaky)
    assert not answer.ok and len(flaky.calls) == 3
    assert "3 times in a row (invalid_output_retries is 2)" in str(answer)
