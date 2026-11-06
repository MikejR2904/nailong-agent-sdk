from __future__ import annotations

import sys

from tests.conftest import call, final, run_cli, write_user_config
from tests.test_sdk_features import BASE
from tests.test_terminal_ui import run_fancy


def hook_settings(tmp_path, event, extra=""):
    script = tmp_path / f"{event}.py"
    marker = tmp_path / f"{event}.flag"
    script.write_text(
        "import os, pathlib\n"
        f"pathlib.Path(r'{marker}').write_text(\n"
        "    os.environ.get('NAILONG_TOOL', '') + '|'\n"
        "    + os.environ.get('NAILONG_SUBJECT', '') + '|'\n"
        "    + os.environ.get('NAILONG_TOOL_INPUT', '')\n"
        ")\n",
        encoding="utf-8",
    )
    text = f'[[hooks]]\nevent = "{event}"\ncommand = \'"{sys.executable}" "{script}"\'\n{extra}'
    return text, marker


def test_the_file_edited_hook_runs_after_each_edit_with_the_input(project, home, tmp_path):
    text, marker = hook_settings(tmp_path, "file_edited")
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    code, _, err, _ = run_cli(
        project,
        home,
        ["--permission-mode", "bypass", "--allow-unsandboxed"],
        call("write_file", path="note.txt", content="hi"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert marker.read_text(encoding="utf-8").startswith("write_file||")
    assert "note.txt" in marker.read_text(encoding="utf-8")


def test_the_file_edited_hook_ignores_reads(project, home, tmp_path):
    text, marker = hook_settings(tmp_path, "file_edited")
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    (project / "a.txt").write_text("x", encoding="utf-8")
    run_cli(
        project,
        home,
        [],
        call("read_file", path="a.txt"),
        final("done"),
        lines="Read\n/exit\n",
        interactive=True,
    )
    assert not marker.exists()


def test_the_permission_asked_hook_runs_before_the_question(project, home, tmp_path):
    text, marker = hook_settings(tmp_path, "permission_asked")
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    code, _, _ = run_fancy(
        project,
        home,
        call("write_file", path="a.txt", content="hi"),
        final("done"),
        lines="Write\n/exit\n",
        chooser=lambda title, options: 3,
    )
    assert code == 0
    assert marker.read_text(encoding="utf-8").startswith("write_file|a.txt|")


def test_the_compacted_hook_runs_after_a_summary_is_saved(project, home, tmp_path):
    text, marker = hook_settings(tmp_path, "compacted")
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    run_cli(
        project,
        home,
        [],
        final("first answer"),
        lines="Hello\n/compact\n/exit\n",
        interactive=True,
    )
    assert marker.exists()


def message_hook(tmp_path, event):
    script = tmp_path / f"{event}.py"
    marker = tmp_path / f"{event}.flag"
    script.write_text(
        "import os, pathlib\n"
        f"pathlib.Path(r'{marker}').write_text(os.environ.get('NAILONG_MESSAGE', '')\n"
        "    + '|' + os.environ.get('NAILONG_TOOL_STATUS', ''))\n",
        encoding="utf-8",
    )
    return f'[[hooks]]\nevent = "{event}"\ncommand = \'"{sys.executable}" "{script}"\'\n', marker


def test_the_assistant_message_hook_gets_the_reply_text(project, home, tmp_path):
    text, marker = message_hook(tmp_path, "assistant_message")
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    code, _, err, _ = run_cli(
        project, home, [], final("the reply"), lines="Hi\n/exit\n", interactive=True
    )
    assert code == 0, err
    assert marker.read_text(encoding="utf-8").startswith("the reply|")


def test_the_tool_failed_hook_runs_only_when_a_tool_fails(project, home, tmp_path):
    text, marker = message_hook(tmp_path, "tool_failed")
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    (project / "a.txt").write_text("x", encoding="utf-8")
    run_cli(
        project,
        home,
        [],
        call("read_file", path="a.txt"),
        final("done"),
        lines="Read\n/exit\n",
        interactive=True,
    )
    assert not marker.exists()
    run_cli(
        project,
        home,
        [],
        call("read_file", path="missing.txt"),
        final("done"),
        lines="Read\n/exit\n",
        interactive=True,
    )
    assert marker.exists() and marker.read_text(encoding="utf-8").endswith("|failed")
