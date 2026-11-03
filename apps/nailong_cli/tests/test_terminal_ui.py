from __future__ import annotations

import asyncio
import io
import threading
import time
from types import SimpleNamespace

import pytest
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.cli import main
from nailong_cli.console import Console
from nailong_cli.keys import ESCAPE, OTHER, EscapeWatcher
from nailong_cli.markdown import BlockSplitter, render_markdown
from nailong_cli.render import Renderer, output_text
from nailong_cli.tui import choose
from tests.conftest import call, factory, final, make_console


def fancy_console(lines: str = ""):
    console, out, err = make_console(lines, interactive=True)
    console.fancy = True
    return console, out, err


class Keys:
    def __init__(self, *script):
        self.script = list(script)
        self.opened = 0
        self.closed = 0

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def poll(self, timeout):
        time.sleep(0.01)
        return self.script.pop(0) if self.script else None


def test_the_spinner_animates_with_elapsed_time_and_leaves_no_trace():
    console, out, _ = fancy_console()
    console.start_spinner("Thinking…")
    time.sleep(0.35)
    console.stop_spinner()
    text = out.getvalue()
    assert text.count("Thinking…") >= 3 and "Esc to interrupt" in text
    assert len({frame for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if frame in text}) >= 2
    assert text.endswith("\r\x1b[K")


class SlowInput(io.StringIO):
    def __init__(self, console_output):
        super().__init__()
        self.console_output = console_output
        self.during = ""

    def readline(self):
        before = len(self.console_output.getvalue())
        time.sleep(0.3)
        self.during = self.console_output.getvalue()[before:]
        return "yes\n"


def test_a_write_clears_the_spinner_line_and_a_prompt_pauses_it():
    console, out, _ = fancy_console()
    console.stdin = SlowInput(out)
    console.start_spinner("Working…")
    console.line("a result")
    assert console.ask("Continue? ") == "yes"
    console.stop_spinner()
    assert out.getvalue().index("a result") < out.getvalue().index("Continue? ")
    assert "Working…" not in console.stdin.during


def test_escape_stops_a_turn_and_other_keys_are_ignored():
    console, _, _ = fancy_console()
    fired = threading.Event()
    keys = Keys(None, OTHER, None, ESCAPE)
    with EscapeWatcher(console, fired.set, keys):
        assert fired.wait(2.0)
    assert keys.opened == 1 and keys.closed == 1 and console.key_hooks is None


def test_the_watcher_leaves_the_keyboard_alone_while_a_prompt_is_open():
    console, _, _ = fancy_console()
    fired = threading.Event()
    armed = threading.Event()

    class Gated(Keys):
        def poll(self, timeout):
            time.sleep(0.01)
            return ESCAPE if armed.is_set() else None

    keys = Gated()
    with EscapeWatcher(console, fired.set, keys):
        with console.reading_input():
            assert keys.closed == 1
            armed.set()
            assert not fired.wait(0.4)
        assert fired.wait(2.0)


@pytest.fixture
def pipe_terminal():
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        yield pipe, DummyOutput()


@pytest.mark.parametrize(
    "keys, expected",
    [("\r", 0), ("\x1b[B\r", 1), ("\x1b[B\x1b[B\x1b[A\r", 1), ("3", 2), ("j\r", 1), ("\x03", None)],
)
def test_the_menu_moves_with_the_arrows_and_numbers(pipe_terminal, keys, expected):
    pipe, output = pipe_terminal
    pipe.send_text(keys)
    options = ["Yes", "Session", "Always", "No"]
    assert choose("Approve write_file?", options, input=pipe, output=output) == expected


def test_escape_in_the_menu_means_no(pipe_terminal):
    pipe, output = pipe_terminal
    pipe.send_text("\x1b")
    assert choose("Approve?", ["Yes", "No"], input=pipe, output=output) is None


def run_fancy(project, home, *turns, lines, chooser=None, keys=None, build=None, argv=()):
    console, out, err = fancy_console(lines)
    console.chooser = chooser
    code = main(
        list(argv),
        environ={"TEST_KEY": "k"},
        console=console,
        model_factory=build or factory(*turns),
        home=home,
        cwd=project,
        keys=keys,
    )
    return code, out.getvalue(), err.getvalue()


def test_an_approval_menu_choice_decides_the_write(project, home):
    asked = []

    def chooser(title, options):
        asked.append((title, options))
        return 0

    code, out, _ = run_fancy(
        project,
        home,
        call("write_file", path="a.txt", content="hi"),
        final("done"),
        lines="Write it\n/exit\n",
        chooser=chooser,
    )
    assert code == 0 and (project / "a.txt").read_text(encoding="utf-8") == "hi"
    assert asked[0][0] == "Approve write_file?" and asked[0][1][-1] == "No"


def test_choosing_no_or_escape_refuses_and_always_saves_a_rule(project, home):
    write = call("write_file", path="n.txt", content="x")
    for pick in (3, None):
        run_fancy(
            project,
            home,
            write,
            final(),
            lines="Write\n/exit\n",
            chooser=lambda title, options, pick=pick: pick,
        )
        assert not (project / "n.txt").exists()
    run_fancy(
        project,
        home,
        call("write_file", path="y.txt", content="x"),
        final(),
        lines="Write\n/exit\n",
        chooser=lambda title, options: 2,
    )
    assert (project / "y.txt").exists()
    code, _, _ = run_fancy(
        project,
        home,
        call("write_file", path="z.txt", content="x"),
        final(),
        lines="Write\n/exit\n",
        chooser=lambda title, options: 3,
    )
    assert code == 0 and not (project / "z.txt").exists()


def test_the_session_choice_is_not_offered_for_commands_without_a_sandbox(project, home):
    answers = iter([1, 3])
    code, out, _ = run_fancy(
        project,
        home,
        call("run_command", command="echo hi"),
        final(),
        lines="Run\n/exit\n",
        chooser=lambda title, options: next(answers),
    )
    assert code == 0 and "never allowed for a whole session without a sandbox" in out


def test_escape_interrupts_a_running_turn_and_returns_to_the_prompt(project, home):
    class Slow:
        async def next_turn(self, context):
            await asyncio.sleep(30)

    started = time.monotonic()
    code, out, _ = run_fancy(
        project,
        home,
        lines="Do it\n/exit\n",
        keys=Keys(None, None, ESCAPE),
        build=lambda config, environ: ModelSetup(Slow(), "scripted", "s"),
    )
    assert code == 0 and "Interrupted (Esc)." in out and time.monotonic() - started < 15


def test_blocks_split_at_blank_lines_but_never_inside_a_code_fence():
    splitter = BlockSplitter()
    blocks = []
    pieces = ["# Title\n\nFirst para", " continues.\n\n```py\nx = 1\n\ny = 2\n```\n", "- a\n- b\n"]
    for piece in pieces:
        blocks += splitter.feed(piece)
    assert blocks == ["# Title", "First para continues.", "```py\nx = 1\n\ny = 2\n```"]
    assert splitter.flush() == "- a\n- b"


def test_markdown_becomes_styled_terminal_text():
    text = render_markdown("# Heading\n\nSome **bold** and `code`.\n\n- one\n- two", width=60)
    assert "\x1b[" in text and "Heading" in text and "bold" in text and "**" not in text


def test_a_fancy_turn_renders_the_streamed_answer_as_markdown(project, home):
    answer = "Done.\n\n**Important**: see `a.py`."
    code, out, _ = run_fancy(project, home, final(answer), lines="Go\n/exit\n")
    assert code == 0 and "**" not in out and "Important" in out and "\x1b[" in out


def event(tool, status="succeeded", output=None, error=None, **arguments):
    result = SimpleNamespace(status=status, output=output, error=error)
    return SimpleNamespace(tool=tool, arguments=arguments, result=result)


def test_long_command_output_is_collapsed_to_three_lines_and_expandable():
    console, out, _ = fancy_console()
    renderer = Renderer(console)
    body = "\n".join(f"line {number}" for number in range(1, 11))
    renderer.after_tool(
        event("run_command", output={"exit_code": 0, "output": body, "seconds": 0.2})
    )
    shown = out.getvalue()
    assert "exit 0 · 0.2s" in shown and "line 3" in shown and "line 4" not in shown
    assert "+7 lines" in shown and "/expand 1" in shown
    assert "line 10" in renderer.expand(1) and renderer.expand(None).startswith("run_command")


def test_file_reads_and_errors_have_one_line_headlines():
    console, out, _ = fancy_console()
    renderer = Renderer(console)
    lines = [{"line": n, "text": f"t{n}"} for n in range(1, 41)]
    renderer.after_tool(event("read_file", output={"lines": lines}))
    renderer.after_tool(event("write_file", status="failed", error="Refused: protected path"))
    shown = out.getvalue()
    assert "read 40 lines" in shown and "failed: Refused: protected path" in shown
    assert renderer.expand(1).splitlines()[1] == "1\tt1"


def test_expand_with_a_bad_number_names_the_choices():
    renderer = Renderer(Console(io.StringIO(), io.StringIO(), io.StringIO()))
    renderer.after_tool(event("read_file", output={"lines": []}))
    with pytest.raises(ValueError, match="There is no tool output 5; there are 1"):
        renderer.expand(5)


def test_the_expand_command_prints_the_last_output_in_full(project, home):
    (project / "n.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    code, out, _ = run_fancy(
        project,
        home,
        call("read_file", path="n.txt"),
        final(),
        lines="Read\n/expand\n/expand 9\n/exit\n",
    )
    assert code == 0 and "read_file (succeeded)" in out and "1\talpha" in out


def test_output_text_of_a_failure_is_its_reason():
    result = SimpleNamespace(status="failed", output=None, error="Tool was declined")
    assert output_text("write_file", result) == "Tool was declined"


def test_the_menu_works_from_a_worker_thread_like_an_approval_does(pipe_terminal):
    pipe, output = pipe_terminal
    pipe.send_text("\x1b[B\r")
    result = asyncio.run(
        asyncio.to_thread(choose, "Approve?", ["Yes", "No"], input=pipe, output=output)
    )
    assert result == 1
