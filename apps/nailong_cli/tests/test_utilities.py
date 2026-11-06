from __future__ import annotations

import sys

import pytest

from nailong_cli.cli import main, parser
from nailong_cli.utilities import completion_script, upgrade_hint
from tests.conftest import final, run_cli


def editor_script(tmp_path, text):
    script = tmp_path / "edit.py"
    script.write_text(
        f"import sys, pathlib\npathlib.Path(sys.argv[1]).write_text({text!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def test_the_editor_command_sends_what_you_wrote_as_a_prompt(project, home, tmp_path):
    editor = editor_script(tmp_path, "A long prompt\nwith two lines")
    _, _, err, setup = run_cli(
        project,
        home,
        [],
        final("ok"),
        lines="/editor\n/exit\n",
        interactive=True,
        environ={"TEST_KEY": "k", "EDITOR": editor},
    )
    assert setup.model.calls[0].task.instructions == "A long prompt\nwith two lines", err


def test_an_empty_editor_session_sends_nothing(project, home, tmp_path):
    editor = editor_script(tmp_path, "   ")
    _, out, _, setup = run_cli(
        project,
        home,
        [],
        final("ok"),
        lines="/editor\n/exit\n",
        interactive=True,
        environ={"TEST_KEY": "k", "EDITOR": editor},
    )
    assert "nothing was sent" in out and not setup.model.calls


def test_a_failing_editor_is_named(project, home):
    failing = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    _, _, err, _ = run_cli(
        project,
        home,
        [],
        lines="/editor\n/exit\n",
        interactive=True,
        environ={"TEST_KEY": "k", "EDITOR": failing},
    )
    assert "exited with status 3; nothing was sent" in err


def test_the_editor_needs_a_terminal(project, home):
    _, out, _, _ = run_cli(project, home, [], lines="/editor\n")
    assert "needs a terminal" in out


def test_stats_summarise_recent_runs(project, home):
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        final("a"),
        lines="One\n/stats\n/stats 30\n/exit\n",
        interactive=True,
    )
    assert "last 7 day(s): 1 run(s)" in out and "last 30 day(s): 1 run(s)" in out
    assert "most frequent events" in out


def test_stats_reject_a_bad_number(project, home):
    _, _, err, _ = run_cli(project, home, [], lines="/stats zero\n/exit\n", interactive=True)
    assert "Usage: /stats [days]" in err


def test_a_session_can_be_deleted_but_not_the_current_one(project, home):
    run_cli(project, home, ["-n", "old-one"], final("a"), lines="One\n/exit\n", interactive=True)
    _, out, err, _ = run_cli(
        project,
        home,
        ["-n", "new-one"],
        final("b"),
        lines=(
            "Two\n/sessions delete new-one\n/sessions delete nope\n"
            "/sessions delete old-one\ny\n/sessions\n/exit\n"
        ),
        interactive=True,
    )
    assert "the session you are in" in err and "does not exist" in err
    assert "deleted old-one" in out
    listing = out.split("deleted old-one")[1]
    assert "new-one" in listing and "old-one" not in listing


def test_completion_scripts_name_the_flags_for_each_shell():
    for shell in ("bash", "zsh", "fish", "powershell"):
        text = completion_script(shell, parser())
        assert "permission" in text and "serve" in text and "nailong" in text
    with pytest.raises(ValueError, match="Unknown shell"):
        completion_script("tcsh", parser())


def test_the_completion_flag_prints_a_script_and_exits(capsys):
    assert main(["--completion", "bash"]) == 0
    assert capsys.readouterr().out.startswith("complete -W")


def test_the_upgrade_flag_says_how_to_upgrade(capsys):
    assert main(["--upgrade"]) == 0
    assert "upgrade" in capsys.readouterr().out.lower()
    assert any("nailong-cli" in line for line in upgrade_hint())
