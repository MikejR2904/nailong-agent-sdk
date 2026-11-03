import pytest

from nailong_agent_sdk.agent.model import ModelReasoningDelta, ModelTextDelta
from nailong_agent_sdk.agent.openai_compatible.chat import _ChatStreamAccumulator
from nailong_agent_sdk.agent.provider_context import ReasoningSplitter
from tests.agent.test_anthropic_streaming import block_start, delta, end, start, stream_turn


def split(*chunks):
    splitter = ReasoningSplitter()
    parts = []
    for chunk in chunks:
        parts += splitter.feed(chunk)
    return parts + splitter.flush()


def joined(parts):
    reasoning = "".join(text for flag, text in parts if flag)
    answer = "".join(text for flag, text in parts if not flag)
    return reasoning, answer


def test_a_think_block_is_reasoning_and_the_rest_is_the_answer():
    assert joined(split("<think>plan it</think>Hello")) == ("plan it", "Hello")


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
def test_tags_split_across_chunks_are_still_recognised(size):
    text = "Before <think>inner thought</think> after <thinking>more</thinking>done"
    chunks = [text[i : i + size] for i in range(0, len(text), size)]
    assert joined(split(*chunks)) == ("inner thoughtmore", "Before  after done")


def test_text_that_only_looks_like_a_tag_is_kept_as_text():
    assert joined(split("a < b and <thing> ", "c <thi")) == ("", "a < b and <thing> c <thi")


def test_an_unclosed_block_streams_as_reasoning_until_the_end():
    assert joined(split("<think>still going", " and going")) == ("still going and going", "")


def chunk(**delta_fields):
    return {"id": "r1", "choices": [{"index": 0, "delta": delta_fields, "finish_reason": None}]}


def test_reasoning_fields_stream_as_reasoning_and_never_enter_the_content():
    accumulator = _ChatStreamAccumulator()
    events = []
    for item in (
        chunk(reasoning_content="think "),
        chunk(reasoning="more"),
        chunk(content="The answer."),
    ):
        events += accumulator.consume(item)
    events += accumulator.flush()
    assert [e for e in events if isinstance(e, ModelReasoningDelta)] == [
        ModelReasoningDelta("think "),
        ModelReasoningDelta("more"),
    ]
    assert [e.text for e in events if isinstance(e, ModelTextDelta)] == ["The answer."]
    finished = accumulator.finalize()
    assert finished["choices"][0]["message"]["content"] == "The answer."


def test_think_tags_inside_streamed_content_become_reasoning_events():
    accumulator = _ChatStreamAccumulator()
    events = []
    for piece in ("<thi", "nk>weigh it</th", "ink>Answer"):
        events += accumulator.consume(chunk(content=piece))
    events += accumulator.flush()
    assert "".join(e.text for e in events if isinstance(e, ModelReasoningDelta)) == "weigh it"
    assert "".join(e.text for e in events if isinstance(e, ModelTextDelta)) == "Answer"


def test_anthropic_thinking_deltas_stream_as_reasoning(server):
    url, state = server
    state["script"] = [
        {
            "sse": [
                start(),
                block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
                delta(0, type="thinking_delta", thinking="let me "),
                delta(0, type="thinking_delta", thinking="think"),
                delta(0, type="signature_delta", signature="sig"),
                {"type": "content_block_stop", "index": 0},
                block_start(1, {"type": "text", "text": ""}),
                delta(1, type="text_delta", text='{"type":"blocked","reason":"x"}'),
                {"type": "content_block_stop", "index": 1},
                *end(),
            ]
        }
    ]
    _, events = stream_turn(url)
    assert [e.text for e in events if isinstance(e, ModelReasoningDelta)] == ["let me ", "think"]
