import pytest

from nailong_agent_sdk.agent.openai_compatible.chat import _agent_turn_from_chat_response
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.starter.models import default_text_answer


def response(content):
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]
    }


def test_plain_text_becomes_a_final_answer_when_a_text_answer_is_given():
    turn = _agent_turn_from_chat_response(response("Hi there"), default_text_answer)
    assert turn.type == "final"
    assert turn.output == {"status": "complete", "answer": "Hi there"}


def test_an_output_object_without_the_type_wrapper_is_wrapped():
    content = '{"status":"complete","answer":"done"}'
    turn = _agent_turn_from_chat_response(response(content), default_text_answer)
    assert turn.type == "final" and turn.output["answer"] == "done"


def test_a_well_formed_turn_is_left_alone():
    content = '{"type":"blocked","reason":"cannot"}'
    turn = _agent_turn_from_chat_response(response(content), default_text_answer)
    assert turn.type == "blocked"


def test_without_a_text_answer_plain_text_is_still_refused():
    with pytest.raises(AgentSdkError, match="must be a JSON AgentTurn object"):
        _agent_turn_from_chat_response(response("Hi there"))
