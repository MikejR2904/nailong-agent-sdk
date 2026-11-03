from __future__ import annotations

import json

from nailong_agent_sdk.agent.model import ModelReasoningDelta, ModelTextDelta

from nailong_cli.render import Renderer
from nailong_cli.stream_json import JsonStream
from tests.conftest import final, make_console
from tests.test_terminal_ui import fancy_console, run_fancy


def plain(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_reasoning_streams_dim_and_indented_under_a_heading():
    console, out, _ = fancy_console()
    renderer = Renderer(console)
    renderer.start_turn()
    for piece in ("First I will ", "read the file.\nThen ", "edit it.\n"):
        renderer.on_stream(ModelReasoningDelta(piece))
    shown = plain(out.getvalue())
    assert "∴ Thinking\n  First I will read the file.\n  Then edit it.\n" in shown
    console.stop_spinner()


def test_the_answer_closes_the_thought_with_a_blank_line_and_follows_it():
    console, out, _ = fancy_console()
    renderer = Renderer(console)
    renderer.start_turn()
    renderer.on_stream(ModelReasoningDelta("hmm"))
    renderer.on_stream(ModelTextDelta('{"type":"final","output":{"status":"complete","answer":"Hi'))
    renderer.on_stream(ModelTextDelta('.\\n\\nBye"}}'))
    renderer.finish_turn(
        type("A", (), {"ok": True, "status": "completed", "steps": 1, "cost": None})()
    )
    shown = plain(out.getvalue())
    assert shown.index("hmm") < shown.index("Hi.") and "hmm\n\n" in shown


def test_hidden_reasoning_prints_nothing():
    console, out, _ = fancy_console()
    renderer = Renderer(console, show_reasoning=False)
    renderer.start_turn()
    renderer.on_stream(ModelReasoningDelta("secret plan"))
    renderer.finish_turn(type("A", (), {"ok": True, "status": "x", "steps": 0, "cost": None})())
    assert "secret plan" not in out.getvalue() and "Thinking\n" not in plain(out.getvalue())


def test_plain_output_shows_reasoning_only_with_verbose():
    console, quiet, _ = make_console("")
    Renderer(console).on_stream(ModelReasoningDelta("planning"))
    assert quiet.getvalue() == ""
    console, loud, _ = make_console("")
    Renderer(console, verbose=True).on_stream(ModelReasoningDelta("planning"))
    assert "planning" in loud.getvalue()


def test_stream_json_carries_reasoning_as_its_own_event_type():
    console, out, _ = make_console("")
    stream = JsonStream(console)
    stream.on_stream(ModelReasoningDelta("because"))
    stream.on_stream(ModelTextDelta('{"answer":"x"}'))
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {"type": "reasoning", "delta": "because"} in events


def test_the_reasoning_command_shows_and_changes_the_setting(project, home):
    code, out, err = run_fancy(
        project,
        home,
        final(),
        lines="/reasoning\n/reasoning off\n/reasoning on\n/reasoning maybe\n/exit\n",
    )
    shown = plain(out)
    assert code == 0 and shown.count("reasoning display is on") == 2
    assert "reasoning display is off" in shown and "Usage: /reasoning [on|off]" in err


def test_the_no_reasoning_flag_turns_it_off(project, home):
    lines = "/reasoning\n/exit\n"
    code, out, _ = run_fancy(project, home, final(), lines=lines, argv=["--no-reasoning"])
    assert code == 0 and "reasoning display is off" in plain(out)
