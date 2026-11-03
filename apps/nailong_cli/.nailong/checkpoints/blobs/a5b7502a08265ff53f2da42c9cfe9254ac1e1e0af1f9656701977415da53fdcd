from __future__ import annotations

import pytest

from nailong_cli.tui import ToolkitReader, mention_matches, slash_matches

COMMANDS = {"/help": "this list", "/mode": "show or set the permission mode", "/model": "switch"}


def test_typing_a_slash_lists_the_matching_commands_with_their_descriptions():
    assert [name for name, _ in slash_matches("/mo", COMMANDS)] == ["/mode", "/model"]
    assert slash_matches("/", COMMANDS)[0] == ("/help", "this list")
    assert slash_matches("/mode x", COMMANDS) == []
    assert slash_matches("hello", COMMANDS) == []


def test_at_completion_lists_project_files_first_directories_and_skips_noise(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".git").mkdir()
    assert mention_matches("", tmp_path) == ["src/", "README.md"]
    assert mention_matches("sr", tmp_path) == ["src/"]
    assert mention_matches("src/", tmp_path) == ["src/app.py"]


def test_at_completion_never_leaves_the_project(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    (tmp_path / "secret.txt").write_text("", encoding="utf-8")
    assert mention_matches("../", outer) == []


def reader(tmp_path, pipe, output, **overrides):
    return ToolkitReader(
        project=tmp_path,
        history_file=tmp_path / "history",
        editing_mode="emacs",
        theme="default",
        toolbar=lambda: "model m | session s",
        cycle_mode=overrides.get("cycle_mode", lambda: None),
        commands=lambda: COMMANDS,
        mode_hint=lambda: "default mode",
        input=pipe,
        output=output,
    )


@pytest.fixture
def terminal():
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        yield pipe, DummyOutput()


def test_enter_submits_the_line(tmp_path, terminal):
    pipe, output = terminal
    pipe.send_text("fix the bug\r")
    assert reader(tmp_path, pipe, output).read("> ") == "fix the bug"


def test_a_trailing_backslash_then_enter_continues_on_a_new_line(tmp_path, terminal):
    pipe, output = terminal
    pipe.send_text("first line\\\rsecond line\r")
    assert reader(tmp_path, pipe, output).read("> ") == "first line\nsecond line"


def test_ctrl_j_inserts_a_new_line_without_submitting(tmp_path, terminal):
    pipe, output = terminal
    pipe.send_text("a\nb\r")
    assert reader(tmp_path, pipe, output).read("> ") == "a\nb"


def test_shift_tab_cycles_the_mode_without_changing_the_text(tmp_path, terminal):
    pipe, output = terminal
    cycled = []
    pipe.send_text("hi\x1b[Z\r")
    value = reader(tmp_path, pipe, output, cycle_mode=lambda: cycled.append(1)).read("> ")
    assert value == "hi" and cycled == [1]


def test_end_of_input_raises_eof(tmp_path, terminal):
    pipe, output = terminal
    pipe.send_text("\x04")
    with pytest.raises(EOFError):
        reader(tmp_path, pipe, output).read("> ")
