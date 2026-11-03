import json

import pytest
from nailong_agent_sdk.agent.model import ModelStreamCompleted, ModelTextDelta
from nailong_agent_sdk.starter import Agent, ModelSetup

from nailong_cli.render import AnswerStreamer, Renderer
from tests.conftest import call, factory, final, make_console


def stream_all(text, step):
    streamer = AnswerStreamer()
    return "".join(streamer.feed(text[i : i + step]) for i in range(0, len(text), step))


TURN = json.dumps(
    {"type": "final", "output": {"status": "complete", "answer": 'Say "hi"\nthen\tgo \\ ok'}}
)


@pytest.mark.parametrize("step", [1, 2, 3, 7, 1000])
def test_the_answer_is_extracted_the_same_however_the_text_is_chunked(step):
    assert stream_all(TURN, step) == 'Say "hi"\nthen\tgo \\ ok'


def test_unicode_escapes_and_surrogate_pairs_are_decoded_across_chunks():
    text = json.dumps({"output": {"answer": "café \U0001f600 end"}}, ensure_ascii=True)
    for step in (1, 4, 100):
        assert stream_all(text, step) == "café \U0001f600 end"


def test_nothing_is_printed_before_the_answer_starts_or_after_it_ends():
    streamer = AnswerStreamer()
    assert streamer.feed('{"type":"final","output":{"status":"complete",') == ""
    assert not streamer.started
    assert streamer.feed('"answer": "abc') == "abc"
    assert streamer.feed('def"}}') == "def"
    assert streamer.feed("more") == "" and streamer.started


def test_a_turn_without_an_answer_field_prints_nothing_while_streaming():
    streamer = AnswerStreamer()
    assert streamer.feed('{"type":"final","output":{"score": 7}}') == ""
    assert not streamer.started


def test_the_renderer_prints_the_streamed_answer_once_and_a_footer():
    console, out, _ = make_console()
    renderer = Renderer(console)
    renderer.start_turn()
    renderer.on_stream(ModelTextDelta(text=TURN[:30]))
    renderer.on_stream(ModelTextDelta(text=TURN[30:]))
    renderer.on_stream(ModelStreamCompleted())

    class Done:
        ok = True
        status = "completed"
        steps = 1
        cost = None

        def __str__(self):
            return "SHOULD NOT REPEAT"

    renderer.finish_turn(Done())
    text = out.getvalue()
    assert text.count("then") == 1 and "SHOULD NOT REPEAT" not in text
    assert text.rstrip().endswith("[completed] 1 step(s)")


def test_a_run_that_did_not_stream_prints_the_answer_at_the_end(tmp_path):
    console, out, _ = make_console()
    renderer = Renderer(console)
    setup = factory(call("missing_tool"), final())(None, None)
    with Agent(
        model=setup,
        instructions="x",
        workspace=tmp_path,
        before_tool=[renderer.before_tool],
        after_tool=[renderer.after_tool],
    ) as agent:
        renderer.start_turn()
        renderer.finish_turn(agent.run_sync("Go."))
    assert "[failed]" in out.getvalue() and "missing_tool" in out.getvalue()


def test_tool_lines_show_the_call_and_its_result(tmp_path):
    console, out, _ = make_console()
    renderer = Renderer(console)
    from nailong_agent_sdk.starter import tool

    @tool
    def run_command(command: str) -> str:
        """Pretend shell."""
        return "ran " + command

    setup = ModelSetup(
        factory(call("run_command", command="make test"), final("ok"))(None, None).model,
        "scripted",
        "scripted-1",
    )
    with Agent(
        model=setup,
        instructions="x",
        tools=[run_command],
        workspace=tmp_path,
        before_tool=[renderer.before_tool],
        after_tool=[renderer.after_tool],
    ) as agent:
        agent.run_sync("Test.")
    text = out.getvalue()
    assert "> run_command make test" in text and "succeeded: ran make test" in text
