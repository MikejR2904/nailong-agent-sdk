from __future__ import annotations

import json

import pytest

from nailong_cli.config import ConfigError
from nailong_cli.tui import (
    THEMES,
    ToolkitReader,
    key_chords,
    load_palette,
    pretty_chord,
)
from tests.test_sdk_features import user_only_project
from tests.test_tui import COMMANDS, terminal  # noqa: F401


def reader(tmp_path, pipe, output, **overrides):
    return ToolkitReader(
        project=tmp_path,
        history_file=tmp_path / "history",
        editing_mode="emacs",
        theme="default",
        toolbar=lambda: "model m",
        cycle_mode=overrides.get("cycle_mode", lambda: None),
        commands=lambda: COMMANDS,
        mode_hint=lambda: "default mode",
        on_expand=overrides.get("on_expand"),
        input=pipe,
        output=output,
        keybinds=overrides.get("keybinds"),
        palette=overrides.get("palette"),
    )


def test_a_rebound_key_does_the_action(tmp_path, terminal):  # noqa: F811
    pipe, output = terminal
    cycled = []
    pipe.send_text("hi\x14\r")
    value = reader(
        tmp_path, pipe, output, cycle_mode=lambda: cycled.append(1), keybinds={"cycle_mode": "c-t"}
    ).read("> ")
    assert value == "hi" and cycled == [1]


def test_a_rebound_action_no_longer_answers_to_its_old_key(tmp_path, terminal):  # noqa: F811
    pipe, output = terminal
    cycled = []
    pipe.send_text("hi\x1b[Z\r")
    reader(
        tmp_path, pipe, output, cycle_mode=lambda: cycled.append(1), keybinds={"cycle_mode": "c-t"}
    ).read("> ")
    assert cycled == []


def test_an_action_may_have_several_keys(tmp_path, terminal):  # noqa: F811
    pipe, output = terminal
    cycled = []
    pipe.send_text("a\x14b\x1b[Z\r")
    reader(
        tmp_path,
        pipe,
        output,
        cycle_mode=lambda: cycled.append(1),
        keybinds={"cycle_mode": ["c-t", "s-tab"]},
    ).read("> ")
    assert cycled == [1, 1]


def test_a_key_prompt_toolkit_does_not_know_is_named_with_its_action(tmp_path, terminal):  # noqa: F811
    pipe, output = terminal
    with pytest.raises(ValueError, match=r'keybinds.expand_output: "c-nonsense" is not a key'):
        reader(tmp_path, pipe, output, keybinds={"expand_output": "c-nonsense"})


def test_key_names_become_readable_labels():
    assert pretty_chord(("c-o",)) == "Ctrl+O"
    assert pretty_chord(("s-tab",)) == "Shift+Tab"
    assert pretty_chord(("escape", "enter")) == "Escape Enter"
    assert key_chords("expand_output", None) == [("c-o",)]
    assert key_chords("expand_output", {"expand_output": "c-x c-e"}) == [("c-x", "c-e")]
    with pytest.raises(ValueError, match="keybinds.newline must be a key"):
        key_chords("newline", {"newline": ""})


def test_the_settings_reject_an_unknown_action(project, home):
    with pytest.raises(ConfigError, match=r"unknown action\(s\) \['quit'\]"):
        user_only_project(project, home, '[keybinds]\nquit = "c-q"\n')
    config = user_only_project(project, home, '[keybinds]\nexpand_output = "c-e"\n')
    assert config.keybinds == {"expand_output": "c-e"}


def test_built_in_themes_resolve_and_a_theme_file_adds_one(tmp_path):
    assert load_palette("mono", tmp_path, tmp_path) is THEMES["mono"]
    folder = tmp_path / ".nailong" / "themes"
    folder.mkdir(parents=True)
    (folder / "sunset.json").write_text(json.dumps({"mode": "#ff8800", "rule": "#442200"}))
    palette = load_palette("sunset", tmp_path, tmp_path)
    assert palette["mode"] == "#ff8800" and palette["hint"] == THEMES["default"]["hint"]


def test_a_user_theme_folder_is_searched_too(tmp_path):
    home = tmp_path / "home"
    folder = home / ".config" / "nailong" / "themes"
    folder.mkdir(parents=True)
    (folder / "night.json").write_text(json.dumps({"menu": "bg:#000000 #ffffff"}))
    assert load_palette("night", tmp_path / "project", home)["menu"] == "bg:#000000 #ffffff"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("{not json", "cannot be read"),
        (json.dumps({"colour": "red"}), r"must be a JSON object with text values.*\['colour'\]"),
        (json.dumps({"mode": 5}), "non-text value"),
    ],
)
def test_a_broken_theme_file_names_itself_and_the_problem(tmp_path, text, fragment):
    folder = tmp_path / ".nailong" / "themes"
    folder.mkdir(parents=True)
    (folder / "bad.json").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=fragment) as caught:
        load_palette("bad", tmp_path, tmp_path)
    assert "bad.json" in str(caught.value)


def test_an_unknown_theme_lists_the_choices(tmp_path):
    with pytest.raises(ValueError, match=r'There is no theme "nope".*dark.*default.*light.*mono'):
        load_palette("nope", tmp_path, tmp_path)
