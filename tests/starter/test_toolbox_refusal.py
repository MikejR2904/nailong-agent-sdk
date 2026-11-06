import asyncio

from nailong_agent_sdk.starter import Refusal, Toolbox, tool


def risky() -> str:
    return "ran"


def box_that_answers(verdict):
    return Toolbox(
        [tool(risky, description="Do something risky.", needs_approval=True)],
        approve=lambda request: verdict,
    )


def test_a_refusal_is_false_and_carries_its_reason():
    assert not Refusal("no terminal")
    assert Refusal("no terminal").reason == "no terminal"


def test_the_reason_is_added_to_the_declined_result_the_model_reads():
    result = asyncio.run(
        box_that_answers(Refusal("Nobody can approve this run."))._refusal("risky", {})
    )
    assert result is not None
    assert result.error == (
        'Tool "risky" was declined by the approve= callback. Nobody can approve this run.'
    )


def test_a_plain_no_keeps_the_old_message_and_a_yes_lets_the_call_through():
    declined = asyncio.run(box_that_answers(False)._refusal("risky", {}))
    assert declined is not None
    assert declined.error == 'Tool "risky" was declined by the approve= callback.'
    assert asyncio.run(box_that_answers(True)._refusal("risky", {})) is None
