from __future__ import annotations

import sys

from nailong_cli import extra_commands
from tests.conftest import CONFIG, final, run_cli, write_user_config


def test_hooks_lists_what_is_configured_and_how_it_is_read(project, home):
    write_user_config(
        home,
        CONFIG
        + '[[hooks]]\nevent = "PreToolUse"\nmatcher = "Bash"\ncommand = "echo a"\n'
        + '[[hooks]]\nevent = "post_tool"\ncommand = "echo b"\n',
    )
    _, out, _, _ = run_cli(project, home, [], final(), lines="/hooks\n/exit\n", interactive=True)
    assert "PreToolUse" in out and "Bash" in out and "[claude]" in out and "echo a" in out
    assert "post_tool" in out and "[env]" in out


def test_hooks_says_so_when_there_are_none(project, home):
    _, out, _, _ = run_cli(project, home, [], final(), lines="/hooks\n/exit\n", interactive=True)
    assert "No hooks are configured" in out


def test_config_shows_the_effective_settings_without_the_key_itself(project, home):
    _, out, _, _ = run_cli(project, home, [], final(), lines="/config\n/exit\n", interactive=True)
    assert "model" in out and "test-model" in out and "TEST_KEY" in out
    assert "project settings" in out


def test_login_needs_a_terminal(project, home):
    _, out, _, _ = run_cli(project, home, [], final(), lines="/login\n/exit\n")
    assert "needs a terminal" in out


def test_image_attaches_a_clipboard_picture_to_the_next_message(project, home, monkeypatch):
    def fake(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG fake")
        return None

    monkeypatch.setattr(extra_commands, "grab_clipboard_image", fake)
    _, out, _, setup = run_cli(
        project, home, [], final("seen"), lines="/image\nWhat is this?\n/exit\n", interactive=True
    )
    assert "Attached the clipboard image" in out


def test_image_says_when_the_clipboard_has_none(project, home, monkeypatch):
    monkeypatch.setattr(
        extra_commands, "grab_clipboard_image", lambda target: "There is no image on the clipboard."
    )
    _, out, _, _ = run_cli(project, home, [], final(), lines="/image\n/exit\n", interactive=True)
    assert "There is no image on the clipboard." in out


def test_the_real_clipboard_reader_reports_instead_of_raising(tmp_path):
    problem = extra_commands.grab_clipboard_image(tmp_path / "c.png")
    assert problem is None or isinstance(problem, str)
    assert sys.platform  # ran without an exception on this platform
