from __future__ import annotations

import pytest

from nailong_cli.config import ConfigError, load_config
from nailong_cli.paste import COLLAPSE_CHARS, COLLAPSE_LINES, PasteStore
from nailong_cli.tui import ToolkitReader
from tests.conftest import final, run_cli, write_user_config
from tests.test_sdk_features import BASE

LONG = "\n".join(f"line {n} of a long log" for n in range(40))


def reader(tmp_path, pipe, output, store=None, **extra):
    return ToolkitReader(
        project=tmp_path,
        history_file=tmp_path / "history",
        editing_mode="emacs",
        theme="default",
        toolbar=lambda: "model m",
        cycle_mode=lambda: None,
        commands=lambda: {},
        pastes=store,
        input=pipe,
        output=output,
        **extra,
    )


@pytest.fixture
def terminal():
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        yield pipe, DummyOutput()


def paste(text):
    return "\x1b[200~" + text + "\x1b[201~"


def test_only_long_text_is_collapsed():
    store = PasteStore()
    assert not store.should_collapse("short")
    assert not store.should_collapse("\n".join("x" for _ in range(COLLAPSE_LINES)))
    assert store.should_collapse("\n".join("x" for _ in range(COLLAPSE_LINES + 1)))
    assert store.should_collapse("y" * (COLLAPSE_CHARS + 1))
    assert not store.should_collapse("y" * COLLAPSE_CHARS)


def test_a_label_names_the_paste_and_expanding_puts_back_exactly_the_text():
    store = PasteStore()
    label = store.add(LONG)
    assert label == "[Pasted text #1 +40 lines]"
    second = store.add("a\nb")
    assert second == "[Pasted text #2 +2 lines]"
    assert store.expand(f"Look at {label} and {second}, then {label}.") == (
        f"Look at {LONG} and a\nb, then {LONG}."
    )
    assert store.expand("[Pasted text #9 +3 lines] and [Pasted text #1 +5 lines]") == (
        "[Pasted text #9 +3 lines] and [Pasted text #1 +5 lines]"
    )
    assert store.get(1).preview.startswith("line 0 of a long log line 1")
    with pytest.raises(ValueError, match=r"There is no paste #7; the pastes are \[1, 2\]"):
        store.get(7)


def test_open_at_opens_the_label_at_or_before_the_cursor_only():
    store = PasteStore()
    label = store.add(LONG)
    text = f"see {label} please"
    start = text.index("[")
    opened, cursor = store.open_at(text, start + 5)
    assert opened == f"see {LONG} please" and cursor == start + len(LONG)
    assert store.open_at(text, len(text))[0] == opened
    assert store.open_at(text, 0) is None
    assert store.open_at("no label here", 3) is None


def test_a_long_paste_shows_a_label_in_the_box_and_the_full_text_is_what_is_returned(
    tmp_path, terminal
):
    pipe, output = terminal
    store = PasteStore()
    pipe.send_text("check this: " + paste(LONG) + " thanks\r")
    got = reader(tmp_path, pipe, output, store).read("> ")
    assert got == f"check this: {LONG} thanks"
    assert [item.label for item in store.listing()] == ["[Pasted text #1 +40 lines]"]
    history = (tmp_path / "history").read_text(encoding="utf-8")
    assert "[Pasted text #1 +40 lines]" in history and "line 20 of a long log" not in history


def test_a_short_paste_goes_in_as_it_is(tmp_path, terminal):
    pipe, output = terminal
    store = PasteStore()
    pipe.send_text(paste("one\ntwo\nthree") + "\r")
    assert reader(tmp_path, pipe, output, store).read("> ") == "one\ntwo\nthree"
    assert store.listing() == []


def test_windows_line_endings_in_a_paste_are_normalised(tmp_path, terminal):
    pipe, output = terminal
    pipe.send_text(paste("a\r\nb\r\nc") + "\r")
    assert reader(tmp_path, pipe, output).read("> ") == "a\nb\nc"


def test_alt_e_opens_the_label_into_its_text_before_sending(tmp_path, terminal):
    pipe, output = terminal
    store = PasteStore()
    pipe.send_text(paste(LONG) + "\x1be\r")
    got = reader(tmp_path, pipe, output, store).read("> ")
    assert got == LONG


def test_two_pastes_get_separate_numbers_and_both_come_back(tmp_path, terminal):
    pipe, output = terminal
    store = PasteStore()
    other = "\n".join(f"other {n}" for n in range(12))
    pipe.send_text(paste(LONG) + " and " + paste(other) + "\r")
    got = reader(tmp_path, pipe, output, store).read("> ")
    assert got == f"{LONG} and {other}"
    assert [item.number for item in store.listing()] == [1, 2]


def test_the_paste_command_lists_and_shows_what_was_pasted(project, home):
    _, out, err, _ = run_cli(
        project, home, [], lines="/paste\n/paste 3\n/paste x\n/exit\n", interactive=True
    )
    assert "No long pastes yet" in out
    assert "There is no paste #3; the pastes are none yet" in out + err
    assert "Usage: /paste" in out + err


def test_the_expand_key_is_a_setting_with_a_checked_name(project, home):
    write_user_config(home, BASE + '[keybinds]\nexpand_paste = "c-p"\n')
    assert load_config(project, home=home).keybinds["expand_paste"] == "c-p"
    write_user_config(home, BASE + '[keybinds]\nopen_paste = "c-p"\n')
    with pytest.raises(ConfigError, match="unknown action.*open_paste.*expand_paste"):
        load_config(project, home=home)


def test_a_pasted_log_reaches_the_model_in_full_through_the_real_session(project, home):
    store = PasteStore()
    label = store.add(LONG)

    class Reader:
        def __init__(self):
            self.lines = [f"Explain {label}", "/exit"]

        def read(self, prompt, default=""):
            return store.expand(self.lines.pop(0))

    from nailong_cli.cli import main
    from tests.conftest import factory, make_console

    console, out, err = make_console("", interactive=True)
    build = factory(final("done"))
    import nailong_cli.cli as cli_module

    original = cli_module.make_reader
    cli_module.make_reader = lambda *a, **k: Reader()
    try:
        main(
            [],
            environ={"TEST_KEY": "k"},
            console=console,
            model_factory=build,
            home=home,
            cwd=project,
        )
    finally:
        cli_module.make_reader = original
    sent = build.setup.model.calls[0].task.instructions
    assert "line 39 of a long log" in sent and label not in sent
